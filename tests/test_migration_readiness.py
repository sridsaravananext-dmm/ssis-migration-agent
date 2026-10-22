from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from ssis_migration.agent.models import AIMigrationResult
from ssis_migration.generator.models import GeneratedArtifact
from ssis_migration.migrate import _run_ai_assisted_generation, run_migration


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/wwi/DailyETLMain.dtsx"
MAPPING = ROOT / "examples/wwi/connection_mapping.json"


def migrate(output: Path, **kwargs):
    return run_migration(
        str(SOURCE), str(output), connection_mapping=str(MAPPING),
        base_path=str(ROOT), **kwargs,
    )


def test_offline_artifacts_retain_legacy_api_but_do_not_claim_acceptance(tmp_path):
    report = migrate(tmp_path)
    package = report["packages"][0]
    assert report["status"] == "MIGRATION_COMPLETE_DEPLOYMENT_PENDING"
    assert package["status"] == "MIGRATED"
    assert report["result_version"] == "1.0"
    assert report["lifecycle_status"] == package["lifecycle_status"] == "BLOCKED"
    assert report["blocked_package_count"] == 1
    assert report["acceptance"]["accepted"] is False
    assert package["acceptance"]["accepted"] is False
    assert package["coverage"]["semantic"] == {"status": "NOT_VERIFIED", "verified_percent": None}
    assert package["coverage"]["execution"]["status"] == "NOT_RUN"
    assert package["coverage"]["data_equivalence"]["status"] == "NOT_RUN"
    assert package["blocking_issues"]

    for key in ("ir", "analysis", "plan", "manifest", "validation", "readiness", "notebook", "job", "dabs", "review"):
        assert Path(package["artifacts"][key]).is_file(), key
    manifest = json.loads(Path(package["artifacts"]["manifest"]).read_text())
    assert manifest["acceptance"] == package["acceptance"]
    plan = json.loads(Path(package["artifacts"]["plan"]).read_text())
    assert all("migration_intent" in step["metadata"] for step in plan["steps"])
    notebook = json.loads(Path(package["artifacts"]["notebook"]).read_text())
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            compile("".join(cell["source"]), "<notebook>", "exec")
    assert any(cell.get("metadata", {}).get("blocked") for cell in notebook["cells"])
    bundle = json.loads(Path(package["artifacts"]["dabs"]).read_text())
    job = next(iter(bundle["resources"]["jobs"].values()))
    cluster_keys = {cluster["job_cluster_key"] for cluster in job["job_clusters"]}
    assert all(task["job_cluster_key"] in cluster_keys for task in job["tasks"])
    review = Path(package["artifacts"]["review"]).read_text()
    assert "not verified semantic coverage" in review
    assert "✅ Ready" not in review


def test_operation_generation_exception_is_persisted_and_blocks_readiness(tmp_path, monkeypatch):
    from ssis_migration.generator.databricks_operations import DatabricksOperationGenerator

    def broken(self, operation):
        raise ValueError("Supplied rule could not be translated")

    monkeypatch.setattr(DatabricksOperationGenerator, "generate_operation", broken)
    report = migrate(tmp_path)
    package = report["packages"][0]
    assert package["lifecycle_status"] == "BLOCKED"
    assert package["acceptance"]["accepted"] is False
    findings = json.loads(Path(package["artifacts"]["validation"]).read_text())
    assert any(f["status"] == "REJECTED" and f["validator"] == "operation_code" for f in findings)
    assert any("could not be translated" in issue for issue in package["blocking_issues"])


def test_partial_package_failure_cannot_hide_behind_legacy_complete_status(tmp_path):
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / SOURCE.name).write_bytes(SOURCE.read_bytes())
    (sources / "Invalid.dtsx").write_text("<broken>")
    report = run_migration(
        str(sources), str(tmp_path / "output"), connection_mapping=str(MAPPING), base_path=str(ROOT),
    )
    assert report["migrated_package_count"] == 1
    assert report["failed_package_count"] == 1
    assert report["lifecycle_status"] == "BLOCKED"
    assert report["acceptance"]["accepted"] is False
    failed = next(package for package in report["packages"] if package["status"] == "FAILED")
    assert Path(failed["error_file"]).is_file()
    assert failed["lifecycle_status"] == "BLOCKED"


def test_explicit_missing_mapping_is_not_silently_replaced_by_inferred_metadata(tmp_path):
    report = run_migration(
        str(SOURCE), str(tmp_path / "output"), connection_mapping=str(tmp_path / "missing.json"),
    )
    assert report["failed_package_count"] == 1
    assert "Supplied connection mapping does not exist" in report["packages"][0]["error"]
    assert report["lifecycle_status"] == "BLOCKED"


def test_execution_request_rejected_before_outputs_or_target_calls(tmp_path):
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="Execution is not implemented"):
        run_migration(str(SOURCE), str(output), execute=True)
    assert not output.exists()


def ai_case(monkeypatch, tmp_path, *, unresolved=None, language="pyspark", code="answer = 1"):
    from ssis_migration.agent.orchestrator import AIMigrationOrchestrator
    from ssis_migration.agent import provider_factory

    captured = {}

    def run(self, migration_step, procedure_definitions=None):
        captured["step"] = migration_step
        return AIMigrationResult(
            status="GENERATED", language=language, code=code, assumptions=[],
            unresolved_items=unresolved or [], validation_requirements=["Compare supplied reference rows."], metadata={},
        )

    monkeypatch.setattr(provider_factory, "create_provider", lambda name: object())
    monkeypatch.setattr(AIMigrationOrchestrator, "run", run)
    artifact = GeneratedArtifact("load_ai", "Load", "AI_CONTRACT", "databricks", "pyspark", "# AI_REQUIRED", "load")
    plan = {"steps": [{
        "step_id": "load", "name": "Load",
        "metadata": {"migration_strategy": {"strategy": "AI_ASSISTED"}, "migration_intent": {}},
    }]}
    databases = [{"role": "source", "relevant_objects": [{"name": "ActualSuppliedTable"}]}]
    findings = _run_ai_assisted_generation(plan, [artifact], tmp_path, [], databases=databases)
    assert captured["step"]["databases"] == databases
    return artifact, findings


def test_even_generated_provider_output_with_unresolved_rule_cannot_replace_artifact(monkeypatch, tmp_path):
    artifact, findings = ai_case(monkeypatch, tmp_path, unresolved=["Missing critical rule"])
    assert artifact.code == "# AI_REQUIRED"
    assert findings[0]["status"] == "REVIEW_REQUIRED"
    assert findings[0]["unresolved_items"] == ["Missing critical rule"]
    assert not list(tmp_path.iterdir())


def test_response_language_mismatch_cannot_replace_artifact(monkeypatch, tmp_path):
    artifact, findings = ai_case(monkeypatch, tmp_path, language="sql", code="SELECT 1")
    assert artifact.code == "# AI_REQUIRED"
    assert findings[0]["status"] == "REJECTED"
    assert findings[0]["language_matches"] is False


def test_validated_ai_candidate_is_saved_without_claiming_behavior_acceptance(monkeypatch, tmp_path):
    artifact, findings = ai_case(monkeypatch, tmp_path)
    assert artifact.code == "answer = 1"
    assert artifact.artifact_type == "NOTEBOOK_CELL"
    assert artifact.metadata["ai_generated"] is True
    assert artifact.metadata["ai_validation_requirements"] == ["Compare supplied reference rows."]
    assert findings[0]["status"] == "PASS"
    assert findings[0]["code_validation"]["status"] == "PASS"
    assert (tmp_path / "load.py").read_text() == "answer = 1"
    ast.parse(artifact.code)


def test_ai_python_cannot_hide_invalid_embedded_sql(monkeypatch, tmp_path):
    artifact, findings = ai_case(
        monkeypatch, tmp_path,
        code='spark.sql("DELETE FROM important_target"); spark.sql("DEADBEEF")',
    )
    assert artifact.code == "# AI_REQUIRED"
    assert findings[0]["status"] == "REJECTED"
    assert any(issue["code"] == "UNSUPPORTED_SQL" for issue in findings[0]["code_validation"]["issues"])


def test_static_rejection_blocks_notebook_before_any_operation(tmp_path, monkeypatch):
    from ssis_migration.generator.databricks_operations import DatabricksOperationGenerator

    monkeypatch.setattr(
        DatabricksOperationGenerator, "generate_operation",
        lambda self, op: 'spark.sql("DELETE FROM important_target"); spark.sql("DEADBEEF")',
    )
    report = migrate(tmp_path)
    notebook = json.loads(Path(report["packages"][0]["artifacts"]["notebook"]).read_text())
    cell = next(cell for cell in notebook["cells"] if cell.get("metadata", {}).get("migration_role") == "step")
    assert cell["metadata"]["blocked"] is True
    calls = []

    class Spark:
        def sql(self, query):
            calls.append(query)

    with pytest.raises(RuntimeError, match="Independent static validation"):
        exec("".join(cell["source"]), {"_step_id": "all", "spark": Spark()})
    assert calls == []


def test_superseded_operations_are_history_not_active_ai_code(tmp_path, monkeypatch):
    from ssis_migration import migrate as migration_module
    from ssis_migration.generator.databricks_operations import DatabricksOperationGenerator

    def broken(self, operation):
        raise ValueError("Earlier deterministic attempt failed")

    def adopt(plan_dict, all_artifacts, artifacts_dir, validation_results, **kwargs):
        ai_steps = {step["step_id"] for step in plan_dict["steps"] if step["metadata"]["migration_strategy"]["strategy"] == "AI_ASSISTED"}
        for artifact in all_artifacts:
            if artifact.source_step_id not in ai_steps:
                continue
            artifact.code = "result = 1"
            artifact.language = "pyspark"
            artifact.artifact_type = "NOTEBOOK_CELL"
            artifact.metadata["ai_generated"] = True
            artifact.metadata["ai_status"] = "GENERATED"
        return []

    monkeypatch.setattr(DatabricksOperationGenerator, "generate_operation", broken)
    monkeypatch.setattr(migration_module, "_run_ai_assisted_generation", adopt)
    report = migrate(tmp_path, ai=True)
    package = report["packages"][0]
    assert package["lifecycle_status"] == "BLOCKED"  # Other, deterministic steps still failed.
    assert package["validation_scope"] == "STATIC_ONLY"
    assert package["acceptance"]["accepted"] is False
    assert package["coverage"]["semantic"]["verified_percent"] is None
    findings = json.loads(Path(package["artifacts"]["validation"]).read_text())
    plan = json.loads(Path(package["artifacts"]["plan"]).read_text())
    ai_steps = {step["step_id"] for step in plan["steps"] if step["metadata"]["migration_strategy"]["strategy"] == "AI_ASSISTED"}
    assert ai_steps
    active_ai_checks = [
        record for record in findings
        if record["step_id"] in ai_steps and record["scope"] == "STATIC_CODE"
    ]
    assert active_ai_checks
    assert all(record["status"] == "PASS" for record in active_ai_checks)
    assert any(record["status"] == "REJECTED" for record in findings if record["step_id"] not in ai_steps)
    notebook = json.loads(Path(package["artifacts"]["notebook"]).read_text())
    # Phase 2 retains unrelated unsupported-expression evidence. AI candidates
    # cannot erase that source finding, but no superseded operation error may
    # be mistaken for part of the actual AI code path.
    ai_cells = [cell for cell in notebook["cells"] if cell.get("metadata", {}).get("step_id") in ai_steps]
    assert ai_cells
    assert all(
        "Incomplete or failed migration operation" not in cell["metadata"]["blocking_reasons"]
        for cell in ai_cells
    )
    histories = Path(package["artifacts"]["generated_dir"]).glob("*_operations.json")
    assert all(json.loads(path.read_text())[0]["error"] == "Earlier deterministic attempt failed" for path in histories)


def test_rerun_failure_never_reuses_previous_success_artifacts(tmp_path):
    original = migrate(tmp_path)
    old_manifest = Path(original["packages"][0]["artifacts"]["manifest"])
    old_contents = old_manifest.read_bytes()
    failed = run_migration(
        str(SOURCE), str(tmp_path), connection_mapping=str(tmp_path / "absent-mapping.json"),
    )
    package = failed["packages"][0]
    assert package["status"] == "FAILED"
    assert "manifest" not in package["artifacts"]
    assert "notebook" not in package["artifacts"]
    assert Path(package["error_file"]).parent != old_manifest.parent
    assert old_manifest.read_bytes() == old_contents
    assert failed["run_id"] != original["run_id"]


def test_packages_with_same_basename_keep_separate_evidence_and_artifacts(tmp_path):
    sources = tmp_path / "sources"
    for directory in ("one", "two"):
        path = sources / directory / "Same.dtsx"
        path.parent.mkdir(parents=True)
        path.write_bytes(SOURCE.read_bytes())
    report = run_migration(
        str(sources), str(tmp_path / "output"), connection_mapping=str(MAPPING), base_path=str(ROOT),
    )
    assert report["migrated_package_count"] == 2
    manifests = [Path(package["artifacts"]["manifest"]) for package in report["packages"]]
    assert len(set(manifests)) == 2
    supplied = {str(path) for path in sources.rglob("Same.dtsx")}
    assert {json.loads(path.read_text())["source_package"] for path in manifests} == supplied
