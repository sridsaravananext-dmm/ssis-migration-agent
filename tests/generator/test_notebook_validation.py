from __future__ import annotations

import copy

import pytest

from ssis_migration.generator.models import GeneratedArtifact
from ssis_migration.generator.notebook import NotebookAssembler


def make_plan():
    return {
        "package_name": "UnrelatedPackage",
        "steps": [{
            "step_id": "step", "name": "Step", "step_type": "DATA_FLOW",
            "dependencies": [], "metadata": {},
        }],
        "metadata": {}, "unresolved": [],
    }


def artifact(code, language="pyspark", **metadata):
    return GeneratedArtifact(
        artifact_id="artifact", name="Step", artifact_type="GENERATED",
        target_platform="databricks", language=language, code=code,
        source_step_id="step", metadata=metadata,
    )


def step_cell(notebook):
    return next(c for c in notebook["cells"] if c["metadata"].get("step_id") == "step")


@pytest.mark.parametrize("step_code", ["# AI_REQUIRED: missing logic", "# a comment", ""])
def test_unimplemented_steps_compile_but_fail_before_execution(step_code):
    assembler = NotebookAssembler()
    notebook = assembler.assemble(make_plan(), [artifact(step_code)])
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            compile("".join(cell["source"]), "<test>", "exec")
    cell = step_cell(notebook)
    assert cell["metadata"]["blocked"] is True
    with pytest.raises(RuntimeError):
        exec("".join(cell["source"]), {"_step_id": "all"})
    assert assembler.validate(notebook)["status"] == "REVIEW_REQUIRED"


def test_incomplete_later_operation_blocks_earlier_target_writes():
    class Spark:
        def __init__(self):
            self.calls = []

        def sql(self, code):
            self.calls.append(code)

    ops = {"step": [
        {"operation_id": "clear", "order": 1, "code": "spark.sql('DELETE FROM stage')"},
        {"operation_id": "load", "order": 2, "error": "Missing source definition"},
    ]}
    notebook = NotebookAssembler().assemble(make_plan(), [], ops)
    spark = Spark()
    with pytest.raises(RuntimeError, match="Incomplete"):
        exec("".join(step_cell(notebook)["source"]), {"_step_id": "all", "spark": spark})
    assert spark.calls == []


@pytest.mark.parametrize("steps,diagnostic", [
    ([{"step_id": "a"}, {"step_id": "a"}], "duplicate"),
    ([{"name": "no ID"}], "missing or invalid"),
    ([{"step_id": "a", "dependencies": ["missing"]}], "Unknown dependency"),
    ([{"step_id": "a", "dependencies": ["a"]}], "Circular dependency"),
    ([{"step_id": "a", "dependencies": "b"}], "Invalid dependencies"),
])
def test_invalid_graphs_are_rejected(steps, diagnostic):
    with pytest.raises(ValueError, match=diagnostic):
        NotebookAssembler()._order_steps(steps)


def test_no_wwi_helpers_for_unrelated_package():
    notebook = NotebookAssembler().assemble(make_plan(), [artifact("value = 1")])
    source = "\n".join("".join(c["source"]) for c in notebook["cells"])
    assert "Integration.ETL_Cutoff" not in source
    assert "Integration.Lineage" not in source
    assert "new_cutoff =" not in source
    assert not any(c["metadata"].get("migration_role") == "utilities" for c in notebook["cells"])
    assert NotebookAssembler.validate(notebook)["status"] == "PASS"


def test_referenced_helpers_require_explicit_code_and_source_evidence():
    code = "last_cutoff = get_last_etl_cutoff(table_name='custom.table')"
    notebook = NotebookAssembler().assemble(make_plan(), [artifact(code)])
    assert step_cell(notebook)["metadata"]["blocked"] is True
    assert "MISSING_CONTROL_HELPER_EVIDENCE" in "".join(step_cell(notebook)["source"])

    plan = make_plan()
    plan["metadata"]["control_helpers"] = {
        "code": "def get_last_etl_cutoff(table_name):\n    return 17\n",
        "verified": True,
        "evidence": [{"file": "reference.py", "locator": "get_last_etl_cutoff"}],
    }
    notebook = NotebookAssembler().assemble(plan, [artifact(code)])
    utilities = [c for c in notebook["cells"] if c["metadata"].get("migration_role") == "utilities"]
    assert len(utilities) == 1
    assert "return 17" in "".join(utilities[0]["source"])
    assert step_cell(notebook)["metadata"]["blocked"] is False

    del plan["metadata"]["control_helpers"]["evidence"]
    notebook = NotebookAssembler().assemble(plan, [artifact(code)])
    assert step_cell(notebook)["metadata"]["blocked"] is True


def test_locally_defined_cutoff_does_not_require_external_helper():
    notebook = NotebookAssembler().assemble(
        make_plan(), [artifact("new_cutoff = 12\nvalue = new_cutoff")],
    )
    assert step_cell(notebook)["metadata"]["blocked"] is False


def test_sql_artifact_is_wrapped_without_changing_python_syntax():
    code = 'SELECT \'"""\' AS unusual_text'
    notebook = NotebookAssembler().assemble(make_plan(), [artifact(code, language="sql")])
    assert NotebookAssembler.validate(notebook)["status"] == "PASS"
    source = "".join(step_cell(notebook)["source"])
    assert "spark.sql" in source
    compile(source, "<sql_cell>", "exec")


def test_ai_unresolved_items_block_otherwise_valid_python():
    notebook = NotebookAssembler().assemble(
        make_plan(), [artifact("value = 1", ai_generated=True, ai_unresolved=["Missing critical rule"])],
    )
    assert step_cell(notebook)["metadata"]["blocked"] is True
    assert NotebookAssembler.validate(notebook)["status"] == "REVIEW_REQUIRED"


def test_static_validation_rejects_syntax_and_metadata_failures():
    notebook = NotebookAssembler().assemble(make_plan(), [artifact("value = 1")])
    broken = copy.deepcopy(notebook)
    step_cell(broken)["source"] = ["if True:\n"]
    result = NotebookAssembler.validate(broken)
    assert result["status"] == "REJECTED"
    assert any(i["code"] == "PYTHON_SYNTAX" for i in result["issues"])
    broken = copy.deepcopy(notebook)
    broken["metadata"] = []
    assert NotebookAssembler.validate(broken)["status"] == "REJECTED"
    broken = copy.deepcopy(notebook)
    broken["cells"][1]["id"] = broken["cells"][0]["id"]
    assert any(i["code"] == "CELL_ID" for i in NotebookAssembler.validate(broken)["issues"])


def test_empty_operation_cannot_report_static_pass():
    notebook = NotebookAssembler().assemble(
        make_plan(), [], {"step": [{"operation_id": "empty", "code": ""}]},
    )
    assert step_cell(notebook)["metadata"]["blocked"] is True


def test_unknown_artifact_language_is_explicitly_blocked():
    notebook = NotebookAssembler().assemble(make_plan(), [artifact("value = 1", language="mdx")])
    assert step_cell(notebook)["metadata"]["blocked"] is True
    assert "Unsupported artifact language" in "".join(step_cell(notebook)["source"])
