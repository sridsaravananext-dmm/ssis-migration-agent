from __future__ import annotations

import pytest

from ssis_migration.validator.readiness import (
    summarize_readiness,
    validate_operations,
)


def validate(code: str):
    return validate_operations({
        "load_step": [{"operation_id": "extract", "code": code}]
    })[0]


@pytest.mark.parametrize("code", ["", "# source body unavailable", "# AI_REQUIRED", "# REVIEW_REQUIRED", "# TODO: translate", "# placeholder"])
def test_missing_implementation_never_passes(code):
    result = validate(code)
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["scope"] == "STATIC_CODE"
    assert result["issues"]


def test_generation_error_is_rejected_and_retained():
    result = validate_operations({
        "load_step": [{"operation_id": "extract", "error": "missing procedure"}]
    })[0]
    assert result["status"] == "REJECTED"
    assert result["operation_id"] == "extract"
    assert result["step_name"] == "load_step"
    assert "missing procedure" in result["issues"][0]["message"]


def test_invalid_python_is_rejected():
    assert validate("for x in") ["status"] == "REJECTED"


def test_literal_spark_sql_is_checked():
    assert validate('spark.sql("SELECT 1 AS value")')["status"] == "PASS"
    result = validate('spark.sql("SELECT FROM")')
    assert result["status"] == "REJECTED"


def test_all_literal_sql_statements_are_checked():
    result = validate('spark.sql("SELECT 1; SELECT FROM")')
    assert result["status"] == "REJECTED"


def test_sql_parser_command_fallback_is_not_passed():
    assert validate('spark.sql("frobnicate value")')["status"] != "PASS"


@pytest.mark.parametrize("sql_argument", ['f"SELECT * FROM {_catalog}.source"', "query_text"])
def test_dynamic_sql_remains_unverified(sql_argument):
    result = validate(f"spark.sql({sql_argument})")
    assert result["status"] == "REVIEW_REQUIRED"
    assert any(issue["code"] == "DYNAMIC_SQL_UNVERIFIED" for issue in result["issues"])


def test_validation_never_executes_generated_code(tmp_path):
    target = tmp_path / "must-not-exist"
    code = f"open({str(target)!r}, 'w').write('executed')"
    result = validate(code)
    assert result["status"] == "PASS"
    assert not target.exists()


def test_individual_control_operation_does_not_inherit_city_semantics():
    result = validate_operations({
        "load_city_dimension": [{"operation_id": "table_name", "code": 'table_name = "Cities"'}]
    })[0]
    assert result["status"] == "PASS"


def test_static_pass_never_establishes_migration_acceptance():
    result = summarize_readiness([{"status": "PASS"}], generated_artifact_count=1)
    assert result["result_version"] == "1.0"
    assert result["lifecycle_status"] == "VALIDATED"
    assert result["validation_scope"] == "STATIC_ONLY"
    assert result["acceptance"]["status"] == "NOT_EVALUATED"
    assert result["acceptance"]["accepted"] is False
    assert result["acceptance"]["required_semantic_coverage_percent"] == 80
    assert result["coverage"]["semantic"] == {"status": "NOT_VERIFIED", "verified_percent": None}
    assert result["coverage"]["execution"]["status"] == "NOT_RUN"
    assert result["coverage"]["data_equivalence"]["status"] == "NOT_RUN"


def test_nested_ai_rejection_cannot_be_hidden_by_generated_status():
    result = summarize_readiness([{
        "step_id": "critical_rule",
        "status": "GENERATED",
        "code_validation": {
            "status": "REJECTED",
            "issues": [{"severity": "ERROR", "message": "invalid SQL"}],
        },
    }], generated_artifact_count=1)
    assert result["lifecycle_status"] == "BLOCKED"
    assert result["coverage"]["structural"]["rejected_checks"] == 1
    assert any("invalid SQL" in problem for problem in result["blocking_issues"])


def test_unresolved_critical_rule_blocks_even_with_passing_code():
    result = summarize_readiness([{
        "status": "GENERATED",
        "code_validation": {"status": "PASS", "issues": []},
        "unresolved_items": ["Missing critical business rule"],
    }], generated_artifact_count=1)
    assert result["acceptance"]["status"] == "BLOCKED"
    assert result["coverage"]["structural"]["passed_checks"] == 0
    assert any("Missing critical business rule" in item for item in result["blocking_issues"])


def test_generation_claim_without_independent_validation_is_blocked():
    result = summarize_readiness([{"status": "GENERATED"}], generated_artifact_count=1)
    assert result["lifecycle_status"] == "BLOCKED"


@pytest.mark.parametrize("static_result", [{}, {"status": "unknown"}])
def test_empty_or_unknown_static_evidence_does_not_validate_generation(static_result):
    result = summarize_readiness([{
        "status": "GENERATED", "code_validation": static_result,
    }], generated_artifact_count=1)
    assert result["lifecycle_status"] == "BLOCKED"
    assert result["coverage"]["structural"]["passed_checks"] == 0


def test_provider_metadata_static_rejection_is_not_lost():
    result = summarize_readiness([{
        "status": "GENERATED",
        "metadata": {"static_code_validation": {
            "status": "REJECTED", "issues": [],
        }},
    }], generated_artifact_count=1)
    assert result["lifecycle_status"] == "BLOCKED"
    assert result["coverage"]["structural"]["rejected_checks"] == 1


def test_artifact_ai_unresolved_metadata_is_retained():
    result = summarize_readiness([{
        "status": "PASS", "metadata": {"ai_unresolved": ["Missing rule"]},
    }], generated_artifact_count=1)
    assert result["lifecycle_status"] == "BLOCKED"
    assert any("Missing rule" in problem for problem in result["blocking_issues"])


def test_mixed_results_do_not_turn_review_records_into_passes():
    result = summarize_readiness([
        {"status": "PASS"}, {"status": "REVIEW_REQUIRED"}, {"status": "REJECTED"},
    ], generated_artifact_count=3)
    assert result["lifecycle_status"] == "BLOCKED"
    assert result["coverage"]["structural"]["passed_checks"] == 1
    assert result["coverage"]["structural"]["review_required_checks"] == 1
    assert result["coverage"]["structural"]["rejected_checks"] == 1


@pytest.mark.parametrize("records,count", [([], 1), ([{"status": "PASS"}], 0), ([], 0)])
def test_empty_generation_or_missing_checks_cannot_validate(records, count):
    result = summarize_readiness(records, generated_artifact_count=count)
    assert result["lifecycle_status"] == "BLOCKED"
    assert result["acceptance"]["accepted"] is False


def test_external_unresolved_evidence_is_retained():
    result = summarize_readiness(
        [{"status": "PASS"}], generated_artifact_count=1,
        unresolved_items=["Missing source schema", "Missing source schema"],
    )
    assert result["blocking_issues"] == ["Missing source schema"]
