from __future__ import annotations

import copy
import json

import pytest

from ssis_migration.agent.context_builder import AIMigrationContextBuilder
from ssis_migration.agent.migration_contract import MigrationContract
from ssis_migration.agent.orchestrator import AIMigrationOrchestrator
from ssis_migration.agent.planner import AIMigrationPlanner
from ssis_migration.agent.prompt_builder import AIMigrationPromptBuilder
from ssis_migration.generator.dispatcher import GeneratorDispatcher
from ssis_migration.generator.operations import MigrationOperationBuilder
from ssis_migration.intake import describe_file
from ssis_migration.migration import build_migration_contract, prepare_generation_step, validate_contract
from ssis_migration.schemas import SchemaValidationError, validate_document


@pytest.fixture
def step():
    return {
        "step_id": "load", "name": "Load", "step_type": "DATA_FLOW",
        "target_language": "pyspark", "source_objects": ["dbo.Source"],
        "target_objects": ["dbo.Target"], "dependencies": ["upstream"],
        "transformations": [{"kind": "projection", "columns": ["id"]}],
        "metadata": {
            "migration_intent": {
                "control_flow_operations": ["EXTRACT_TO_STAGING"],
                "source_procedures": [{"procedure": "dbo.Read", "definition": "SELECT 1 AS id", "pattern": "FULL_SNAPSHOT_SIMPLE"}],
                "destination_procedures": [], "data_flows": [{"name": "Flow"}],
                "future_extension": {"unrecognized_but_preserved": True},
            },
            "data_flows": [{"name": "Flow", "column_mappings": [{"source": "id", "destination": "id"}]}],
            "migration_strategy": {"strategy": "DETERMINISTIC", "requires_ai": False},
        },
    }


@pytest.fixture
def evidence(tmp_path):
    source = tmp_path / "definitions.sql"
    source.write_text("CREATE PROCEDURE dbo.Read AS SELECT 1 AS id;")
    return [describe_file(source)]


def attach(step, evidence, **kwargs):
    document = build_migration_contract(step, source_platform="ssis", evidence_files=evidence, **kwargs).to_dict()
    step["metadata"]["migration_contract"] = document
    return document


def test_bridge_preserves_original_intent_including_unknown_fields(step, evidence):
    original = copy.deepcopy(step)
    first = build_migration_contract(step, source_platform="ssis", evidence_files=evidence).to_dict()
    repeated = build_migration_contract(step, source_platform="ssis", evidence_files=evidence).to_dict()
    assert first == repeated
    assert step == original
    assert first["source_behavior"]["migration_intent"] == original["metadata"]["migration_intent"]
    assert first["source_behavior"]["legacy_step"] == original
    assert first["source_behavior"]["analysis_status"] == "UNVERIFIED_LEGACY_ANALYSIS"
    assert first["required_behavior"]["criticality_status"] == "UNASSESSED"
    assert first["required_behavior"]["coverage_status"] == "NOT_VERIFIED"
    assert all(item["critical"] is None and item["weight"] is None for item in first["required_behavior"]["obligations"])
    assert any(item["source_value"].get("column_mappings") for item in first["required_behavior"]["obligations"] if isinstance(item["source_value"], dict))
    assert first["inputs"][0]["schema_status"] == "UNVERIFIED"
    validate_document(first, "migration_contract")
    validate_contract(first, original)


def test_old_dataclass_constructor_remains_usable():
    original = MigrationContract("load", "ssis", "databricks", {"source": True}, {"required": True})
    assert original.to_dict()["source_behavior"] == {"source": True}
    assert original.status == "DRAFT"


@pytest.mark.parametrize("field", ["source_objects", "target_objects", "dependencies", "transformations"])
def test_modified_inputs_require_contract_rebuild(step, evidence, field):
    attach(step, evidence)
    step[field].append("changed")
    with pytest.raises(SchemaValidationError, match="inputs changed"):
        prepare_generation_step(step)


def test_modified_intent_or_semantics_cannot_generate_code(step, evidence):
    attach(step, evidence)
    step["metadata"]["migration_intent"]["source_procedures"][0]["definition"] = "SELECT 99"
    with pytest.raises(SchemaValidationError, match="inputs changed"):
        GeneratorDispatcher().generate(step)
    with pytest.raises(SchemaValidationError):
        MigrationOperationBuilder().build(step)


def test_runtime_database_and_static_findings_do_not_change_semantic_binding(step, evidence):
    document = attach(step, evidence)
    step["databases"] = [{"role": "source", "actual_supplied_metadata": True}]
    step["metadata"]["static_validation"] = {"status": "REVIEW_REQUIRED"}
    prepared = prepare_generation_step(step)
    assert prepared["metadata"]["migration_contract"] == document
    assert prepared["databases"] == step["databases"]


@pytest.mark.parametrize("change,match", [
    (lambda c: c.update(contract_version="2.0"), "1.0"),
    (lambda c: c["required_behavior"]["obligations"].pop(), "omitted"),
    (lambda c: c["inputs"].clear(), "inputs"),
    (lambda c: c["implementation_strategy"].update(decision="AI_ASSISTED"), "strategy"),
    (lambda c: c["provenance"]["evidence"][0].update(file_id="absent"), "Unknown evidence"),
    (lambda c: c["unresolved_items"].append("Changed after identity calculation"), "content identity"),
])
def test_invalid_edited_or_incomplete_contracts_are_rejected(step, evidence, change, match):
    document = attach(step, evidence)
    change(document)
    with pytest.raises(SchemaValidationError, match=match):
        prepare_generation_step(step)


def test_missing_evidence_and_procedure_bodies_are_explicit(step):
    step["metadata"]["migration_intent"]["source_procedures"][0]["definition"] = None
    document = build_migration_contract(step).to_dict()
    assert document["status"] == "REVIEW_REQUIRED"
    assert any("original source-file evidence" in item for item in document["unresolved_items"])
    assert any("dbo.Read" in item for item in document["unresolved_items"])
    assert any(dep["kind"] == "procedure" and dep["status"] == "MISSING" for dep in document["dependencies"])
    assert document["evidence_files"] == []
    assert document["inputs"][0]["schema_status"] == "UNVERIFIED"


def test_supplemental_definitions_are_bound_without_rewriting_intent(step, evidence):
    step["metadata"]["migration_intent"]["source_procedures"][0]["definition"] = None
    intent = copy.deepcopy(step["metadata"]["migration_intent"])
    document = attach(step, evidence, supporting_definitions={"dbo.Read": "SELECT 1 AS id"})
    assert document["source_behavior"]["migration_intent"] == intent
    assert document["source_behavior"]["supporting_definitions"]["dbo.Read"] == "SELECT 1 AS id"
    assert any(dep["kind"] == "procedure" and dep["status"] == "SUPPLIED" for dep in document["dependencies"])


class Provider:
    def __init__(self):
        self.request = None
        self.prompt = None

    def generate(self, request, prompt):
        self.request, self.prompt = request, prompt
        return {
            "status": "GENERATED", "language": "pyspark", "code": "value = 1",
            "assumptions": [], "unresolved_items": [], "validation_requirements": [],
            "metadata": {"migration_contract_id": "provider_cannot_replace_identity"},
        }


def test_same_contract_reaches_deterministic_generation_and_ai_request(step, evidence):
    original = copy.deepcopy(step)
    before = GeneratorDispatcher().generate(original)
    document = attach(step, evidence)
    after = GeneratorDispatcher().generate(step)
    assert (after.code, after.language, after.artifact_type) == (before.code, before.language, before.artifact_type)
    assert after.metadata["migration_contract_id"] == document["contract_id"]
    operations = MigrationOperationBuilder().build(step)
    assert operations
    assert all(operation.metadata["migration_contract"] == document for operation in operations)
    provider = Provider()
    result = AIMigrationOrchestrator(provider).run(step)
    assert provider.request["migration_contract"] == document
    assert document["contract_id"] in provider.prompt
    assert result.metadata["migration_contract_id"] == document["contract_id"]
    assert result.validation_requirements == document["validation_requirements"]


def test_model_cannot_erase_contract_unresolved_dependencies(step, evidence):
    step["metadata"]["migration_intent"]["source_procedures"][0]["definition"] = None
    document = attach(step, evidence)
    result = AIMigrationOrchestrator(Provider()).run(step)
    assert result.status == "UNRESOLVED"
    assert set(document["unresolved_items"]) <= set(result.unresolved_items)


def test_unsupported_contract_never_calls_provider_or_operation_generator(step, evidence):
    step["metadata"]["migration_strategy"]["strategy"] = "UNSUPPORTED"
    attach(step, evidence)

    class ForbiddenProvider:
        def generate(self, *args):
            raise AssertionError("Unsupported contract called a model")

    assert MigrationOperationBuilder().build(step) == []
    assert GeneratorDispatcher().generate(step).artifact_type == "UNRESOLVED"
    assert AIMigrationOrchestrator(ForbiddenProvider()).run(step).status == "UNRESOLVED"


def test_contract_cannot_be_routed_to_another_target_or_unbound_sql(step, evidence):
    attach(step, evidence)
    with pytest.raises(ValueError, match="Target platform override"):
        GeneratorDispatcher().generate(step, target_platform="snowflake")
    with pytest.raises(ValueError, match="SQL override"):
        GeneratorDispatcher().generate(step, sql="SELECT 88")


def test_oversized_evidence_fails_before_provider_without_silent_truncation(step, evidence):
    attach(step, evidence)
    provider = Provider()
    orchestrator = AIMigrationOrchestrator(provider)
    orchestrator.prompt_builder = AIMigrationPromptBuilder(max_context_chars=100)
    with pytest.raises(ValueError, match="silently truncated"):
        orchestrator.run(step)
    assert provider.request is None


def test_contract_is_retained_by_direct_request_and_prompt_builders(step, evidence):
    document = attach(step, evidence)
    request = AIMigrationPlanner().build_request(AIMigrationContextBuilder().build(step))
    assert request["migration_contract"] == document
    prompt = AIMigrationPromptBuilder().build(request)
    assert json.dumps(document["contract_id"]) in prompt


def test_destination_definition_cannot_satisfy_missing_source_definition(step, evidence):
    intent = step["metadata"]["migration_intent"]
    intent["source_procedures"][0]["definition"] = None
    intent["destination_procedures"] = [{"procedure": "dbo.Read", "definition": "SELECT 2 AS id"}]
    document = build_migration_contract(step, evidence_files=evidence).to_dict()
    dependencies = [dep for dep in document["dependencies"] if dep["kind"] == "procedure"]
    assert next(dep for dep in dependencies if dep["source_role"] == "source")["status"] == "MISSING"
    assert next(dep for dep in dependencies if dep["source_role"] == "destination")["status"] == "SUPPLIED"
    assert any("Missing exported procedure" in item for item in document["unresolved_items"])


def test_conflicting_metadata_and_supplemental_exports_are_retained(step, evidence):
    document = attach(step, evidence, supporting_definitions={"dbo.Read": "SELECT 99 AS id"})
    assert document["source_behavior"]["migration_intent"]["source_procedures"][0]["definition"] == "SELECT 1 AS id"
    assert document["source_behavior"]["supporting_definitions"]["dbo.Read"] == "SELECT 99 AS id"
    assert any("Conflicting exported procedure" in item for item in document["unresolved_items"])
    result = AIMigrationOrchestrator(Provider()).run(step)
    assert result.status == "UNRESOLVED"


def test_supplemental_temporal_requirements_cannot_be_hidden_by_legacy_override(step, evidence):
    attach(step, evidence, supporting_definitions={"dbo.Read": "SELECT * FROM dbo.T FOR SYSTEM_TIME AS OF @cutoff"})
    provider = Provider()
    result = AIMigrationOrchestrator(provider).run(step, procedure_definitions={"dbo.Read": "SELECT 1 AS id"})
    assert provider.request["temporal_context"]["has_temporal_queries"] is True
    assert result.status == "UNRESOLVED"
    assert result.metadata["context_unresolved_items"]


def test_ambiguous_supplemental_identities_do_not_silently_overwrite(step, evidence):
    step["metadata"]["migration_intent"]["source_procedures"][0]["definition"] = None
    definitions = {"dbo.Read": "SELECT 1 AS id", "[dbo].[Read]": "SELECT 2 AS id"}
    document = attach(step, evidence, supporting_definitions=definitions)
    assert document["source_behavior"]["supporting_definitions"] == definitions
    assert any("Conflicting supplemental" in item for item in document["unresolved_items"])
    assert next(dep for dep in document["dependencies"] if dep["kind"] == "procedure")["status"] == "MISSING"


def test_whitespace_is_not_an_exported_procedure_body(step, evidence):
    step["metadata"]["migration_intent"]["source_procedures"][0]["definition"] = "  \n "
    document = attach(step, evidence)
    assert next(dep for dep in document["dependencies"] if dep["kind"] == "procedure")["status"] == "MISSING"


def test_procedure_dependency_role_is_required_by_schema(step, evidence):
    document = attach(step, evidence)
    dependency = next(dep for dep in document["dependencies"] if dep["kind"] == "procedure")
    dependency.pop("source_role")
    with pytest.raises(SchemaValidationError, match="source_role"):
        validate_document(document, "migration_contract")


def test_model_cannot_erase_source_evidence_gaps(step, evidence):
    source_id = evidence[0]["file_id"]
    diagnostic = {
        "code": "UNKNOWN_TRANSACTION_BOUNDARY", "severity": "WARNING",
        "message": "Exported transaction behavior needs review.", "file_ids": [source_id],
        "evidence": [{"file_id": source_id, "locator": "file:/", "rule": "supplied_export_gap"}],
    }
    document = attach(step, evidence, source_diagnostics=[diagnostic])
    result = AIMigrationOrchestrator(Provider()).run(step)
    assert result.status == "UNRESOLVED"
    assert set(document["unresolved_items"]) <= set(result.unresolved_items)


def test_invalid_contract_is_rejected_before_provider_initialization(step, evidence, tmp_path, monkeypatch):
    from ssis_migration.agent import provider_factory
    from ssis_migration.migrate import _run_ai_assisted_generation

    document = attach(step, evidence)
    document["contract_version"] = "unsupported"

    def forbidden(*args, **kwargs):
        raise AssertionError("Provider initialized before contract validation")

    monkeypatch.setattr(provider_factory, "create_provider", forbidden)
    with pytest.raises(SchemaValidationError):
        _run_ai_assisted_generation({"steps": [step]}, [], tmp_path, [])


@pytest.mark.parametrize("backend", ["sql", "python"])
def test_direct_backends_honor_contract_binding(step, evidence, backend):
    from ssis_migration.generator.pyspark import PySparkGenerator
    from ssis_migration.generator.spark_sql import SparkSQLGenerator

    attach(step, evidence)
    step["source_objects"].append("unbound.table")
    generator = SparkSQLGenerator() if backend == "sql" else PySparkGenerator()
    with pytest.raises(SchemaValidationError, match="inputs changed"):
        generator.generate(step, "spark_sql" if backend == "sql" else "pyspark")


@pytest.mark.parametrize("field,value", [("step_id", "other"), ("target_platform", "snowflake")])
def test_prompt_rejects_request_contract_mismatch(step, evidence, field, value):
    attach(step, evidence)
    request = AIMigrationPlanner().build_request(AIMigrationContextBuilder().build(step))
    request["task"][field] = value
    with pytest.raises(ValueError, match="migration contract"):
        AIMigrationPromptBuilder().build(request)


def test_prompt_rejects_a_changed_strategy(step, evidence):
    attach(step, evidence)
    request = AIMigrationPlanner().build_request(AIMigrationContextBuilder().build(step))
    request["migration_strategy"] = {"strategy": "AI_ASSISTED"}
    with pytest.raises(ValueError, match="strategy"):
        AIMigrationPromptBuilder().build(request)
