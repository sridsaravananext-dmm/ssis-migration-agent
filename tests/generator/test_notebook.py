from __future__ import annotations

import json
from pathlib import Path

import pytest

from ssis_migration.generator.models import GeneratedArtifact
from ssis_migration.generator.notebook import NotebookAssembler


# ---------------------------------------------------------
# Fixtures
# ---------------------------------------------------------


@pytest.fixture
def assembler() -> NotebookAssembler:
    return NotebookAssembler()


@pytest.fixture
def deterministic_artifact() -> GeneratedArtifact:
    return GeneratedArtifact(
        artifact_id="step_load_fact",
        name="Load Fact",
        artifact_type="SQL_TRANSLATION",
        target_platform="databricks",
        language="spark_sql",
        code=(
            "-- Translated SQL\n"
            "SELECT id, value FROM source.table\n"
            "WHERE updated > :LastCutoff"
        ),
        source_step_id="load_fact",
        metadata={
            "migration_strategy": {"strategy": "DETERMINISTIC"},
        },
    )


@pytest.fixture
def ai_artifact() -> GeneratedArtifact:
    return GeneratedArtifact(
        artifact_id="step_load_dim",
        name="Load Dimension",
        artifact_type="AI_CONTRACT",
        target_platform="databricks",
        language="pyspark",
        code=(
            "# AI_REQUIRED\n"
            "# Migration step: Load Dimension\n"
            "# Strategy: AI_ASSISTED\n"
            "# Patterns: ['TEMPORAL']\n"
        ),
        source_step_id="load_dim",
        metadata={
            "migration_strategy": {"strategy": "AI_ASSISTED"},
        },
    )


@pytest.fixture
def simple_plan() -> dict:
    return {
        "package_name": "TestPackage",
        "steps": [
            {
                "step_id": "load_dim",
                "name": "Load Dimension",
                "step_type": "DATA_FLOW",
                "source_components": [],
                "source_objects": ["[Application].[Cities]"],
                "target_components": [],
                "target_objects": ["Dimension.City"],
                "transformations": [],
                "dependencies": [],
                "metadata": {
                    "migration_strategy": {
                        "strategy": "AI_ASSISTED",
                    },
                },
            },
            {
                "step_id": "load_fact",
                "name": "Load Fact",
                "step_type": "DATA_FLOW",
                "source_components": [],
                "source_objects": ["[Application].[Transactions]"],
                "target_components": [],
                "target_objects": ["Fact.Movement"],
                "transformations": [],
                "dependencies": ["load_dim"],
                "metadata": {
                    "migration_strategy": {
                        "strategy": "DETERMINISTIC",
                    },
                },
            },
        ],
        "unresolved": [],
        "metadata": {"planner": "deterministic"},
    }


@pytest.fixture
def operations_by_step() -> dict:
    return {
        "load_dim": [
            {
                "operation_id": "set_table_name",
                "operation_type": "CONTROL",
                "order": 1,
                "code": 'table_name = "Dimension.City"',
            },
            {
                "operation_id": "clear_staging",
                "operation_type": "STAGING",
                "order": 2,
                "code": 'spark.sql("""DELETE FROM Integration.City_Staging""")',
            },
        ],
        "load_fact": [
            {
                "operation_id": "extract_to_staging",
                "operation_type": "EXTRACT",
                "order": 1,
                "code": 'source_df = spark.sql("""SELECT * FROM src""")',
            },
            {
                "operation_id": "merge_target",
                "operation_type": "MERGE",
                "order": 2,
                "code": "# TODO: generate Delta MERGE",
            },
        ],
    }


# ---------------------------------------------------------
# Tests
# ---------------------------------------------------------


def test_assemble_produces_valid_notebook_structure(
    assembler, simple_plan, deterministic_artifact, ai_artifact,
    operations_by_step,
):
    artifacts = [ai_artifact, deterministic_artifact]
    nb = assembler.assemble(
        simple_plan, artifacts, operations_by_step
    )

    assert nb["nbformat"] == 4
    assert nb["nbformat_minor"] == 5
    assert nb["metadata"]["application/databricks"]["language"] == "python"
    assert nb["metadata"]["application/databricks"]["name"] == (
        "TestPackage_Migration"
    )
    assert isinstance(nb["cells"], list)
    assert len(nb["cells"]) > 0


def test_assemble_orders_steps_by_dependencies(
    assembler, simple_plan, deterministic_artifact, ai_artifact,
    operations_by_step,
):
    """load_fact depends on load_dim, so load_dim must come first."""
    artifacts = [ai_artifact, deterministic_artifact]
    nb = assembler.assemble(
        simple_plan, artifacts, operations_by_step
    )

    # Find the markdown cells that contain step names
    step_cells = [
        "".join(c["source"]) for c in nb["cells"]
        if c["cell_type"] == "markdown"
        and "## " in "".join(c["source"])
    ]

    # First step section should be Load Dimension (no deps)
    dim_idx = next(
        i for i, s in enumerate(step_cells)
        if "Load Dimension" in s
    )
    fact_idx = next(
        i for i, s in enumerate(step_cells)
        if "Load Fact" in s
    )
    assert dim_idx < fact_idx


def test_assemble_includes_title_cell(
    assembler, simple_plan, deterministic_artifact, ai_artifact,
):
    nb = assembler.assemble(
        simple_plan, [deterministic_artifact, ai_artifact]
    )

    first_cell = nb["cells"][0]
    assert first_cell["cell_type"] == "markdown"
    source = "".join(first_cell["source"])
    assert "TestPackage" in source
    assert "Databricks Migration" in source


def test_assemble_includes_ai_contract_for_ai_assisted_steps(
    assembler, simple_plan, ai_artifact, deterministic_artifact,
    operations_by_step,
):
    artifacts = [ai_artifact, deterministic_artifact]
    nb = assembler.assemble(
        simple_plan, artifacts, operations_by_step
    )

    # Find the AI contract cell (contains <details>)
    contract_cells = [
        c for c in nb["cells"]
        if c["cell_type"] == "markdown"
        and "<details>" in "".join(c["source"])
    ]

    # Only AI_ASSISTED steps should have contract cells
    assert len(contract_cells) == 1
    source = "".join(contract_cells[0]["source"])
    assert "AI_REQUIRED" in source


def test_assemble_includes_operations_code_cells(
    assembler, simple_plan, deterministic_artifact, ai_artifact,
    operations_by_step,
):
    artifacts = [ai_artifact, deterministic_artifact]
    nb = assembler.assemble(
        simple_plan, artifacts, operations_by_step
    )

    code_cells = [
        c for c in nb["cells"] if c["cell_type"] == "code"
    ]

    step_cells = {
        c["metadata"]["step_id"]: c for c in code_cells
        if c["metadata"].get("migration_role") == "step"
    }
    assert set(step_cells) == {"load_dim", "load_fact"}
    parameter_cell = next(c for c in code_cells if c["metadata"].get("migration_role") == "parameters")
    param_code = "".join(parameter_cell["source"])
    assert "dbutils.widgets" in param_code

    first_code = "".join(step_cells["load_dim"]["source"])
    assert "set_table_name" in first_code
    assert "CONTROL" in first_code
    assert "if _step_id" in first_code

    second_code = "".join(step_cells["load_fact"]["source"])
    assert "extract_to_staging" in second_code
    assert "if _step_id" in second_code


def test_assemble_falls_back_to_artifact_code_without_operations(
    assembler, simple_plan, deterministic_artifact, ai_artifact,
):
    """When no operations are provided, use artifact code directly."""
    artifacts = [ai_artifact, deterministic_artifact]
    nb = assembler.assemble(simple_plan, artifacts)

    code_cells = [
        c for c in nb["cells"] if c["cell_type"] == "code"
    ]

    assert {
        c["metadata"]["step_id"] for c in code_cells
        if c["metadata"].get("migration_role") == "step"
    } == {"load_dim", "load_fact"}

    # The deterministic artifact is spark_sql -> wrapped in
    # spark.sql() inside the step guard
    fact_cell = None
    for cell in code_cells:
        source = "".join(cell["source"])
        if "SELECT" in source:
            fact_cell = source
            break

    assert fact_cell is not None
    assert "spark.sql" in fact_cell
    assert "if _step_id" in fact_cell


def test_assemble_includes_unresolved_warning(
    assembler, simple_plan, deterministic_artifact, ai_artifact,
):
    plan = {**simple_plan, "unresolved": ["Unsupported task: Custom Script"]}
    nb = assembler.assemble(
        plan, [deterministic_artifact, ai_artifact]
    )

    # Find the unresolved warning cell (contains "Unresolved Items"
    # heading, not just the word "Unresolved" which also appears in
    # the title summary table)
    warning_cells = [
        c for c in nb["cells"]
        if c["cell_type"] == "markdown"
        and "Unresolved Items" in "".join(c["source"])
    ]
    assert len(warning_cells) == 1
    source = "".join(warning_cells[0]["source"])
    assert "Unsupported task" in source


def test_assemble_handles_error_operations(
    assembler, simple_plan, deterministic_artifact, ai_artifact,
):
    """Operations with 'error' key should be included as comments."""
    ops = {
        "load_dim": [
            {
                "operation_id": "failing_op",
                "operation_type": "MERGE",
                "order": 1,
                "error": "Could not generate code",
            },
        ],
        "load_fact": [],
    }
    nb = assembler.assemble(
        simple_plan,
        [ai_artifact, deterministic_artifact],
        ops,
    )

    code_cells = [
        c for c in nb["cells"] if c["cell_type"] == "code"
    ]

    dim_cell = "".join(next(
        c["source"] for c in code_cells
        if c["metadata"].get("step_id") == "load_dim"
    ))
    assert "ERROR" in dim_cell
    assert "Could not generate code" in dim_cell


def test_assemble_and_write_produces_valid_ipynb(
    assembler, simple_plan, deterministic_artifact, ai_artifact,
    operations_by_step, tmp_path,
):
    artifacts = [ai_artifact, deterministic_artifact]
    notebook_path = assembler.assemble_and_write(
        simple_plan, artifacts, tmp_path, operations_by_step
    )

    assert notebook_path.suffix == ".ipynb"
    assert notebook_path.exists()

    # Verify it's valid JSON in .ipynb format
    content = notebook_path.read_text(encoding="utf-8")
    nb = json.loads(content)
    assert nb["nbformat"] == 4
    assert "cells" in nb
    assert len(nb["cells"]) > 0


def test_assemble_handles_step_with_no_artifact(
    assembler, simple_plan,
):
    """A step with no matching artifact should get a 'no code' message."""
    plan = {
        **simple_plan,
        "steps": simple_plan["steps"][:1],  # just load_dim
    }
    nb = assembler.assemble(plan, [])  # no artifacts

    # Should have a markdown cell saying no code generated
    no_code_cells = [
        c for c in nb["cells"]
        if c["cell_type"] == "markdown"
        and "No code generated" in "".join(c["source"])
    ]
    assert len(no_code_cells) == 1


def test_order_steps_handles_circular_dependency(
    assembler,
):
    """A cycle cannot be represented as an executable task order."""
    steps = [
        {"step_id": "a", "name": "A", "dependencies": ["b"]},
        {"step_id": "b", "name": "B", "dependencies": ["a"]},
    ]
    with pytest.raises(ValueError, match="Circular dependency"):
        assembler._order_steps(steps)