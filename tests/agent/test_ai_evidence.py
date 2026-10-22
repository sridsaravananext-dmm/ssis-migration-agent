from __future__ import annotations

import pytest

from ssis_migration.agent.code_validator import validate_generated_code
from ssis_migration.agent.context_builder import AIMigrationContextBuilder
from ssis_migration.agent.orchestrator import AIMigrationOrchestrator
from ssis_migration.agent.planner import AIMigrationPlanner
from ssis_migration.agent.prompt_builder import AIMigrationPromptBuilder


def make_step(language: str = "pyspark") -> dict:
    return {
        "step_id": "load_example",
        "name": "Load example",
        "target_language": language,
        "source_objects": ["dbo.Source"],
        "metadata": {"migration_intent": {"load_type": "EXTRACT_LOAD"}},
    }


class CapturingProvider:
    def __init__(self, *, language="pyspark", code="result = 1", unresolved=None, metadata=None):
        self.response = {
            "status": "GENERATED", "language": language, "code": code,
            "assumptions": [], "unresolved_items": unresolved or [],
            "validation_requirements": ["Compare reference output."],
            "metadata": {} if metadata is None else metadata,
        }
        self.request = None
        self.prompt = None

    def generate(self, request, prompt):
        self.request = request
        self.prompt = prompt
        return self.response


def test_temporal_detection_records_source_evidence_without_fabricated_schema():
    sql = "SELECT * FROM [Audit].[Events] FOR SYSTEM_TIME AS OF @cutoff"
    context = AIMigrationContextBuilder._build_temporal_context({"Audit.ReadEvents": sql})

    assert context["has_temporal_queries"] is True
    assert context["source_tables_with_temporal"] == ["Audit.Events"]
    assert context["validity_columns"] == {}
    assert context["archive_table_pattern"] is None
    assert context["historical_data_status"] == "UNVERIFIED"
    assert context["unresolved_items"]
    evidence = context["evidence"][0]
    assert evidence["procedure"] == "Audit.ReadEvents"
    assert sql[evidence["start_offset"]:evidence["end_offset"]] == evidence["source_text"]
    assert evidence["source_text"] == "FOR SYSTEM_TIME AS OF"


@pytest.mark.parametrize("sql", [
    "-- FOR SYSTEM_TIME AS OF\nSELECT 1",
    "/* FOR SYSTEM_TIME AS OF */ SELECT 1",
    "SELECT 'FOR SYSTEM_TIME AS OF' AS explanation",
    'SELECT [FOR], [SYSTEM_TIME], [AS], [OF] FROM dbo.T',
])
def test_temporal_comments_literals_and_quoted_names_are_not_behavior(sql):
    context = AIMigrationContextBuilder._build_temporal_context({"dbo.Example": sql})
    assert context["has_temporal_queries"] is False
    assert context["evidence"] == []
    assert context["unresolved_items"] == []


def test_database_evidence_survives_context_request_and_prompt():
    databases = [{
        "connection_reference": "source", "database_name": "ExportedDb", "role": "source",
        "relevant_objects": [{"schema": "custom", "name": "Records", "columns": ["record_id"]}],
        "dependency_edges": [{"from": "dbo.Source", "to": "custom.Records"}],
    }]
    context = AIMigrationContextBuilder().build(make_step(), databases=databases)
    request = AIMigrationPlanner().build_request(context)
    prompt = AIMigrationPromptBuilder().build(request)

    assert request["database_context"] == context["database_context"]
    assert request["database_context"]["source"][0]["relevant_objects"] == databases[0]["relevant_objects"]
    assert '"database_context"' in prompt
    assert '"record_id"' in prompt
    assert "constitute the required evidence" not in prompt
    assert "has_temporal_queries only records detected syntax" in prompt


def test_actual_dtsx_derived_combined_database_reaches_request_and_prompt():
    from pathlib import Path

    from ssis_migration.context.builder import build_migration_context
    from ssis_migration.migrate import analyze_one_package

    root = Path(__file__).resolve().parents[2]
    analyzed = analyze_one_package(root / "examples/wwi/DailyETLMain.dtsx")
    migration_context = build_migration_context(
        analyzed["package_ir"], analyzed["analysis"], base_path=root,
    )
    supplied = migration_context.databases[0].to_dict()
    assert supplied["role"] == "combined"
    assert supplied["database"]["tables"]
    assert any(table["columns"] for table in supplied["database"]["tables"])

    step = make_step()
    step["databases"] = [supplied]
    provider = CapturingProvider()
    AIMigrationOrchestrator(provider).run(step)

    database_context = provider.request["database_context"]
    assert database_context["source"] == []
    assert database_context["destination"] == []
    assert database_context["combined"] == [supplied]
    first_table = supplied["database"]["tables"][0]
    assert first_table["name"] in provider.prompt
    assert '"tables"' in provider.prompt
    assert '"columns"' in provider.prompt


@pytest.mark.parametrize("role", [None, "custom_vendor_role", "SOURCE", {"unparsed": "role"}])
def test_unknown_database_roles_and_full_evidence_are_retained_without_guessing(role):
    supplied = {
        "role": role,
        "database": {"tables": [{"name": "EvidenceTable", "columns": [{"name": "UnusualKey"}]}]},
        "evidence": [{"source_file": "schema-export.json", "locator": "/tables/0"}],
    }
    context = AIMigrationContextBuilder().build(make_step(), databases=[supplied])
    request = AIMigrationPlanner().build_request(context)
    prompt = AIMigrationPromptBuilder().build(request)

    database_context = request["database_context"]
    assert database_context["source"] == []
    assert database_context["destination"] == []
    assert database_context["combined"] == []
    kept = database_context["unassigned"][0]
    assert kept["role"] == role
    assert kept["database"] == supplied["database"]
    assert kept["evidence"] == supplied["evidence"]
    assert "UnusualKey" in prompt
    assert "schema-export.json" in prompt


@pytest.mark.parametrize("language", ["sql", "spark_sql", "Databricks SQL"])
def test_target_sql_uses_target_dialect_static_validation(language):
    assert validate_generated_code("SELECT 1 AS x", language=language)["status"] == "PASS"


@pytest.mark.parametrize("code", ["SELECT FROM", "SELECT * FROM dbo.T WHERE", "SELECT 1; DEADBEEF"])
def test_invalid_or_ununderstood_sql_is_rejected(code):
    assert validate_generated_code(code, language="sql")["status"] == "REJECTED"


def test_sqlglot_command_fallback_is_not_validated_sql():
    result = validate_generated_code("EXECUTE source_procedure", language="sql")
    assert result["status"] == "REJECTED"
    assert any(issue["code"] == "UNSUPPORTED_SQL" for issue in result["issues"])


def test_every_sql_statement_is_checked():
    result = validate_generated_code("SELECT 1; EXECUTE source_procedure", language="sql")
    assert result["status"] == "REJECTED"


@pytest.mark.parametrize("code,language", [("-- pending", "sql"), ("# pending", "pyspark")])
def test_comment_only_code_is_not_an_implementation(code, language):
    assert validate_generated_code(code, language=language)["status"] == "REJECTED"


def test_sql_destructive_write_requires_review():
    result = validate_generated_code("DELETE FROM target WHERE id = 1", language="sql")
    assert result["status"] == "REVIEW_REQUIRED"
    assert any(issue["code"] == "SQL_WRITE_REVIEW" for issue in result["issues"])


def test_unsupported_languages_are_not_treated_as_python_or_sql():
    result = validate_generated_code("SELECT 1", language="mdx")
    assert result["status"] == "REJECTED"
    assert result["issues"][0]["code"] == "UNSUPPORTED_LANGUAGE"


def test_existing_python_validator_call_signature_is_preserved():
    assert validate_generated_code("result = 1", "Load City")["status"] == "PASS"
    assert validate_generated_code("result = ", "Load example")["status"] == "REJECTED"


def test_unresolved_provider_rule_cannot_remain_generated():
    provider = CapturingProvider(unresolved=["Missing critical business rule"])
    result = AIMigrationOrchestrator(provider).run(make_step())

    assert result.status == "UNRESOLVED"
    assert "Missing critical business rule" in result.unresolved_items
    assert result.metadata["static_code_validation"]["status"] == "PASS"
    assert result.metadata["validation_scope"] == "STATIC_CODE_ONLY"


def test_model_cannot_erase_missing_temporal_prerequisite():
    provider = CapturingProvider()
    result = AIMigrationOrchestrator(provider).run(
        make_step(),
        procedure_definitions={"Audit.ReadEvents": "SELECT * FROM Audit.Events FOR SYSTEM_TIME AS OF @cutoff"},
    )

    assert result.status == "UNRESOLVED"
    assert result.unresolved_items
    assert result.metadata["context_unresolved_items"]
    temporal = provider.request["temporal_context"]
    assert temporal["validity_columns"] == {}
    assert temporal["archive_table_pattern"] is None


@pytest.mark.parametrize("location", ["intent", "semantics"])
def test_temporal_definitions_already_on_step_cannot_bypass_missing_evidence(location):
    step = make_step()
    definition = {"procedure": "Audit.ReadEvents", "definition": "SELECT * FROM Audit.Events FOR SYSTEM_TIME AS OF @cutoff"}
    if location == "intent":
        step["metadata"]["migration_intent"]["source_procedures"] = [definition]
    else:
        step["metadata"]["procedure_semantics"] = [definition]
    provider = CapturingProvider()
    result = AIMigrationOrchestrator(provider).run(step)
    assert result.status == "UNRESOLVED"
    assert provider.request["temporal_context"]["has_temporal_queries"] is True
    assert any("historical-data" in item for item in result.unresolved_items)


@pytest.mark.parametrize("code", [
    "SELECT 'FOR SYSTEM_TIME AS OF' AS documentation",
    "SELECT 1 -- FOR SYSTEM_TIME AS OF is source-only syntax",
])
def test_target_sql_temporal_phrase_in_literal_or_comment_is_not_source_syntax(code):
    assert validate_generated_code(code, language="sql")["status"] == "PASS"


@pytest.mark.parametrize("code", [
    "CREATE OR REPLACE TABLE target AS SELECT 1 AS id",
    "INSERT OVERWRITE TABLE target SELECT 1 AS id",
])
def test_sql_replacement_writes_require_review(code):
    result = validate_generated_code(code, language="sql")
    assert result["status"] == "REVIEW_REQUIRED"
    assert any(issue["code"] == "SQL_WRITE_REVIEW" for issue in result["issues"])


def test_orchestrator_keeps_database_evidence_from_dictionary_steps():
    step = make_step()
    step["databases"] = [{"role": "source", "relevant_objects": [{"name": "SuppliedTable"}]}]
    provider = CapturingProvider()
    result = AIMigrationOrchestrator(provider).run(step)
    assert result.status == "GENERATED"
    assert provider.request["database_context"]["source"][0]["relevant_objects"] == [{"name": "SuppliedTable"}]
    assert "SuppliedTable" in provider.prompt


def test_orchestrator_accepts_sql_syntax_without_python_parse_error():
    result = AIMigrationOrchestrator(CapturingProvider(language="sql", code="SELECT 1 AS x")).run(make_step("spark_sql"))
    assert result.status == "GENERATED"
    assert result.metadata["static_code_validation"]["status"] == "PASS"


def test_orchestrator_rejects_wrong_response_language_and_preserves_diagnostics():
    result = AIMigrationOrchestrator(CapturingProvider(language="sql", code="SELECT FROM")).run(make_step())
    assert result.status == "UNRESOLVED"
    codes = {issue["code"] for issue in result.metadata["static_code_validation"]["issues"]}
    assert {"SQL_SYNTAX", "LANGUAGE_MISMATCH"} <= codes


def test_invalid_metadata_returns_schema_diagnostic_instead_of_crashing():
    result = AIMigrationOrchestrator(CapturingProvider(metadata=["not a dictionary"])).run(make_step())
    assert result.status == "UNRESOLVED"
    assert result.metadata["provider_response_validation"] == "FAILED"
    assert "metadata" in result.unresolved_items[0]


def test_static_validation_does_not_execute_provider_code(tmp_path):
    marker = tmp_path / "must-not-exist"
    code = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')"
    result = AIMigrationOrchestrator(CapturingProvider(code=code)).run(make_step())
    assert result.metadata["static_code_validation"]["status"] == "PASS"
    assert not marker.exists()
