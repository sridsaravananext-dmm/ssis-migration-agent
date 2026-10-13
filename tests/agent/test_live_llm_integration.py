"""
Integration tests for live Databricks LLM endpoint.

These tests require a running Databricks workspace with
access to the databricks-gpt-oss-120b serving endpoint.
They are skipped automatically when the Databricks SDK
cannot authenticate or the endpoint is unavailable.

Run with: pytest tests/agent/test_live_llm_integration.py -v
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import pytest

# Ensure src is on the path
_project_root = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..", "..",
    )
)
_src_path = os.path.join(_project_root, "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

from ssis_migration.agent.databricks_provider import DatabricksProvider
from ssis_migration.agent.orchestrator import AIMigrationOrchestrator
from ssis_migration.generator.models import GeneratedArtifact
from ssis_migration.generator.notebook import NotebookAssembler


# ---------------------------------------------------------
# Check if the Databricks SDK can authenticate.
# Only live LLM provider tests are skipped when
# the endpoint is unavailable. Non-LLM tests
# (JSON parser, temporal context, notebook
# assembler) run unconditionally.
# ---------------------------------------------------------

_live_skip_reason: str | None = None

try:
    from databricks.sdk import WorkspaceClient as _WC
    _w = _WC()
    _w.config.host
except Exception as _exc:
    _live_skip_reason = f"Databricks SDK not available: {_exc}"


live_llm = pytest.mark.skipif(
    _live_skip_reason is not None,
    reason=_live_skip_reason or "",
)


# ---------------------------------------------------------
# Fixtures
# ---------------------------------------------------------

@pytest.fixture
def provider() -> DatabricksProvider:
    return DatabricksProvider()


@pytest.fixture
def simple_prompt() -> str:
    return (
        "You are a migration assistant. Return ONLY a JSON "
        "object with these exact fields:\n"
        '{"status": "GENERATED", "language": "sql", '
        '"code": "SELECT 1 AS test_value", '
        '"assumptions": ["Test query"], '
        '"unresolved_items": [], '
        '"validation_requirements": []}\n'
        "Return only the JSON object."
    )


@pytest.fixture
def simple_request() -> dict[str, Any]:
    return {"task": {"name": "test", "target_language": "sql"}}


# ---------------------------------------------------------
# Provider tests
# ---------------------------------------------------------

class TestDatabricksProviderLive:
    """Test the DatabricksProvider with a live endpoint."""

    @live_llm
    def test_provider_uses_sdk_backend(
        self, provider: DatabricksProvider,
    ):
        assert provider._backend == "sdk"
        assert provider.endpoint_name == "databricks-gpt-oss-120b"

    @live_llm
    def test_provider_returns_valid_json(
        self,
        provider: DatabricksProvider,
        simple_request: dict,
        simple_prompt: str,
    ):
        result = provider.generate(simple_request, simple_prompt)

        assert isinstance(result, dict)
        assert result["status"] in ("GENERATED", "REVIEW_REQUIRED")
        assert "code" in result
        assert "language" in result
        assert "assumptions" in result
        assert "unresolved_items" in result
        assert "validation_requirements" in result

    @live_llm
    def test_provider_metadata_includes_provenance(
        self,
        provider: DatabricksProvider,
        simple_request: dict,
        simple_prompt: str,
    ):
        result = provider.generate(simple_request, simple_prompt)

        meta = result.get("metadata", {})
        assert meta.get("provider") == "databricks"
        assert meta.get("backend") == "sdk"
        assert meta.get("model") is not None
        assert meta.get("endpoint") is not None

    @live_llm
    def test_provider_handles_large_response(
        self,
        provider: DatabricksProvider,
    ):
        """Verify max_tokens=16384 allows large code generation."""
        import inspect
        src = inspect.getsource(DatabricksProvider._generate_sdk)
        assert "16384" in src


# ---------------------------------------------------------
# JSON parser tests
# ---------------------------------------------------------

class TestRobustJSONParser:
    """Test the robust JSON parser with various formats."""

    @pytest.mark.parametrize("response,expected_status", [
        # Plain JSON
        (
            '{"status": "GENERATED", "code": "x", "language": "sql", '
            '"assumptions": [], "unresolved_items": [], '
            '"validation_requirements": []}',
            "GENERATED",
        ),
        # Markdown-fenced JSON
        (
            '```json\n{"status": "REVIEW_REQUIRED", "code": "y", '
            '"language": "python", "assumptions": [], '
            '"unresolved_items": ["item"], '
            '"validation_requirements": []}\n```',
            "REVIEW_REQUIRED",
        ),
        # JSON with surrounding text
        (
            'Here is the result:\n'
            '{"status": "GENERATED", "code": "z", '
            '"language": "sql", "assumptions": [], '
            '"unresolved_items": [], '
            '"validation_requirements": []}\n'
            'Done.',
            "GENERATED",
        ),
        # Plain code fence (no json tag)
        (
            '```\n{"status": "GENERATED", "code": "a", '
            '"language": "python", "assumptions": [], '
            '"unresolved_items": [], '
            '"validation_requirements": []}\n```',
            "GENERATED",
        ),
    ])
    def test_parse_json_handles_all_formats(
        self, response: str, expected_status: str,
    ):
        result = DatabricksProvider._parse_json(response)
        assert result["status"] == expected_status
        assert isinstance(result, dict)

    def test_parse_json_raises_on_invalid(self):
        with pytest.raises(RuntimeError, match="invalid JSON"):
            DatabricksProvider._parse_json("This is not JSON at all.")


# ---------------------------------------------------------
# Temporal context tests
# ---------------------------------------------------------

class TestTemporalContext:
    """Test temporal context detection in the context builder."""

    def test_detects_temporal_query(self):
        from ssis_migration.agent.context_builder import (
            AIMigrationContextBuilder,
        )
        proc_defs = {
            "Integration.GetCityUpdates": (
                "SELECT * FROM Application.Cities "
                "FOR SYSTEM_TIME AS OF @ValidFrom"
            ),
        }
        ctx = AIMigrationContextBuilder._build_temporal_context(
            proc_defs,
        )
        assert ctx["has_temporal_queries"] is True
        assert "Application.Cities" in ctx["source_tables_with_temporal"]
        assert ctx["validity_columns"]["valid_from"] == "ValidFrom"
        assert ctx["validity_columns"]["valid_to"] == "ValidTo"
        assert "_Archive" in ctx["archive_table_pattern"]
        assert "ValidFrom" in ctx["translation_guidance"]

    def test_no_temporal_query_returns_false(self):
        from ssis_migration.agent.context_builder import (
            AIMigrationContextBuilder,
        )
        proc_defs = {
            "Integration.GetTransactionUpdates": (
                "SELECT * FROM Sales.CustomerTransactions "
                "WHERE LastEditedWhen > @LastCutoff"
            ),
        }
        ctx = AIMigrationContextBuilder._build_temporal_context(
            proc_defs,
        )
        assert ctx["has_temporal_queries"] is False


# ---------------------------------------------------------
# Notebook assembler tests with AI-generated artifacts
# ---------------------------------------------------------

class TestNotebookWithAIGeneratedArtifacts:
    """Test the notebook assembler handles AI-generated artifacts."""

    def _make_plan(self) -> dict[str, Any]:
        return {
            "package_name": "TestPackage",
            "steps": [
                {
                    "step_id": "step_ai_generated",
                    "name": "AI Generated Step",
                    "step_type": "DIMENSION_LOAD",
                    "dependencies": [],
                    "source_objects": ["Application.Cities"],
                    "target_objects": ["Dimension.City"],
                    "metadata": {
                        "migration_strategy": {
                            "strategy": "AI_ASSISTED",
                        },
                    },
                },
                {
                    "step_id": "step_ai_unresolved",
                    "name": "AI Unresolved Step",
                    "step_type": "DIMENSION_LOAD",
                    "dependencies": [],
                    "source_objects": ["Application.Countries"],
                    "target_objects": ["Dimension.Country"],
                    "metadata": {
                        "migration_strategy": {
                            "strategy": "AI_ASSISTED",
                        },
                    },
                },
            ],
            "unresolved": [],
            "metadata": {"planner": "test"},
        }

    def _make_artifacts(self) -> list[GeneratedArtifact]:
        generated = GeneratedArtifact(
            artifact_id="art_1",
            name="AI Generated Step",
            artifact_type="pyspark",
            target_platform="databricks",
            language="pyspark",
            code="from pyspark.sql import SparkSession\n"
            "spark = SparkSession.builder.getOrCreate()\n"
            "df = spark.sql('SELECT 1')\n"
            "df.write.mode('overwrite').saveAsTable('test')",
            source_step_id="step_ai_generated",
            metadata={
                "ai_generated": True,
                "ai_status": "GENERATED",
                "ai_assumptions": ["Table exists"],
                "ai_unresolved": [],
            },
        )

        unresolved = GeneratedArtifact(
            artifact_id="art_2",
            name="AI Unresolved Step",
            artifact_type="pyspark",
            target_platform="databricks",
            language="pyspark",
            code="# AI_REQUIRED\n# This is a placeholder contract\n",
            source_step_id="step_ai_unresolved",
            metadata={},
        )

        return [generated, unresolved]

    def test_ai_generated_step_uses_llm_code(self):
        plan = self._make_plan()
        artifacts = self._make_artifacts()

        assembler = NotebookAssembler()
        notebook = assembler.assemble(plan, artifacts)

        code_cells = [
            c for c in notebook["cells"]
            if c["cell_type"] == "code"
        ]

        # code_cells[0] is the parameter cell;
        # code_cells[1] is the generated step
        generated_cell = code_cells[1]
        source = "".join(generated_cell["source"])
        assert "from pyspark.sql" in source
        assert "AI_REQUIRED" not in source

    def test_ai_generated_step_has_metadata_markdown(self):
        plan = self._make_plan()
        artifacts = self._make_artifacts()

        assembler = NotebookAssembler()
        notebook = assembler.assemble(plan, artifacts)

        md_cells = [
            c for c in notebook["cells"]
            if c["cell_type"] == "markdown"
        ]

        # Find the AI-Generated metadata cell
        ai_md = [
            c for c in md_cells
            if "AI-Generated Code" in "".join(c["source"])
        ]
        assert len(ai_md) == 1
        source = "".join(ai_md[0]["source"])
        assert "GENERATED" in source
        assert "Table exists" in source

    def test_unresolved_step_retains_contract(self):
        plan = self._make_plan()
        artifacts = self._make_artifacts()

        assembler = NotebookAssembler()
        notebook = assembler.assemble(plan, artifacts)

        md_cells = [
            c for c in notebook["cells"]
            if c["cell_type"] == "markdown"
        ]

        # Find the AI Migration Contract cell
        contract_cells = [
            c for c in md_cells
            if "AI Migration Contract" in "".join(c["source"])
        ]
        assert len(contract_cells) == 1

        # The unresolved step's code cell should NOT
        # contain LLM-generated code
        code_cells = [
            c for c in notebook["cells"]
            if c["cell_type"] == "code"
        ]
        # code_cells[0] is the parameter cell,
        # code_cells[1] is the generated step,
        # code_cells[2] is the unresolved step
        unresolved_cell = code_cells[2]
        source = "".join(unresolved_cell["source"])
        assert "AI_REQUIRED" in source
