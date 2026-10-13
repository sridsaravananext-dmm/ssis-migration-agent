from __future__ import annotations

import json

# Data validation test suite

import pytest

from ssis_migration.validator.data_validation import (
    DataValidator,
    ValidationQuery,
    ValidationReport,
)


# ---------------------------------------------------------
# Fixtures
# ---------------------------------------------------------


@pytest.fixture
def validator() -> DataValidator:
    return DataValidator()


@pytest.fixture
def simple_plan() -> dict:
    return {
        "package_name": "TestPkg",
        "steps": [
            {
                "step_id": "load_city_dimension",
                "name": "Load City",
                "step_type": "DIMENSION_LOAD",
                "source_objects": ["Integration.GetCityUpdates"],
                "target_objects": ["Integration.City_Staging"],
                "dependencies": [],
                "metadata": {},
            },
            {
                "step_id": "load_movement_fact",
                "name": "Load Movement",
                "step_type": "FACT_LOAD",
                "source_objects": ["Integration.GetMovementUpdates"],
                "target_objects": ["Integration.Movement_Staging"],
                "dependencies": ["load_city_dimension"],
                "metadata": {},
            },
        ],
        "unresolved": [],
        "metadata": {},
    }


@pytest.fixture
def target_metadata() -> dict:
    return {
        "tables": [
            {
                "schema": "Dimension",
                "name": "City",
                "columns": [
                    {"name": "City Key", "data_type": "int", "nullable": False, "ordinal": 1},
                    {"name": "WWI City ID", "data_type": "int", "nullable": False, "ordinal": 2},
                    {"name": "City", "data_type": "nvarchar", "nullable": False, "ordinal": 3},
                    {"name": "State Province", "data_type": "nvarchar", "nullable": True, "ordinal": 4},
                ],
            },
            {
                "schema": "Fact",
                "name": "Movement",
                "columns": [
                    {"name": "Movement Key", "data_type": "int", "nullable": False, "ordinal": 1},
                    {"name": "City Key", "data_type": "int", "nullable": False, "ordinal": 2},
                    {"name": "Quantity", "data_type": "decimal", "nullable": True, "ordinal": 3},
                ],
            },
            {
                "schema": "Integration",
                "name": "City_Staging",
                "columns": [
                    {"name": "WWI City ID", "data_type": "int", "nullable": False, "ordinal": 1},
                    {"name": "City", "data_type": "nvarchar", "nullable": False, "ordinal": 2},
                    {"name": "State Province", "data_type": "nvarchar", "nullable": True, "ordinal": 3},
                ],
            },
            {
                "schema": "Integration",
                "name": "Movement_Staging",
                "columns": [
                    {"name": "Movement Key", "data_type": "int", "nullable": False, "ordinal": 1},
                    {"name": "City Key", "data_type": "int", "nullable": False, "ordinal": 2},
                    {"name": "Quantity", "data_type": "decimal", "nullable": True, "ordinal": 3},
                ],
            },
        ],
    }


@pytest.fixture
def source_metadata() -> dict:
    return {"tables": []}


# ---------------------------------------------------------
# Tests
# ---------------------------------------------------------


def test_generate_produces_report(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata)
    assert isinstance(report, ValidationReport)
    assert report.package_name == "TestPkg"
    assert len(report.queries) > 0


def test_generate_creates_table_mappings(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata)
    assert len(report.table_mappings) == 2
    city_map = next(m for m in report.table_mappings if m["step_id"] == "load_city_dimension")
    assert city_map["target_table"] == "Dimension.City"
    assert city_map["staging_table"] == "Integration.City_Staging"
    assert len(city_map["target_columns"]) == 4


def test_generate_includes_row_count_checks(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata, catalog="uc")
    row_counts = [q for q in report.queries if q.check_type == "row_count"]
    assert len(row_counts) == 2
    assert "uc.Dimension.City" in row_counts[0].sql


def test_generate_includes_column_coverage_checks(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata)
    col_checks = [q for q in report.queries if q.check_type == "column_coverage"]
    assert len(col_checks) == 2
    assert "information_schema" in col_checks[0].sql


def test_generate_includes_null_checks(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata)
    null_checks = [q for q in report.queries if q.check_type == "null_check"]
    # City has 3 non-nullable cols, Movement has 2 non-nullable cols = 5 total
    assert len(null_checks) == 5
    null_city = [
        q for q in null_checks
        if q.step_id == "load_city_dimension"
        and "City Key" in q.query_id
    ]
    assert len(null_city) == 1
    assert "IS NULL" in null_city[0].sql


def test_generate_includes_key_uniqueness_checks(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata)
    key_checks = [q for q in report.queries if q.check_type == "key_uniqueness"]
    assert len(key_checks) == 2
    assert "HAVING COUNT(*) > 1" in key_checks[0].sql


def test_generate_includes_checksum_checks(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata)
    checksums = [q for q in report.queries if q.check_type == "checksum"]
    assert len(checksums) == 2
    assert "HASH" in checksums[0].sql
    assert "checksums_match" in checksums[0].sql


def test_generate_qualifies_table_names(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata, catalog="my_cat", schema="my_schema")
    row_count = next(q for q in report.queries if q.check_type == "row_count")
    assert "my_cat.Dimension.City" in row_count.sql


def test_generate_summary(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata)
    assert report.summary["total_checks"] > 0
    assert report.summary["table_count"] == 2
    assert "row_count" in report.summary["check_types"]
    assert "null_check" in report.summary["check_types"]
    assert "checksum" in report.summary["check_types"]


def test_generate_sql_file(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata)
    sql = validator.generate_sql_file(report)
    assert "-- Data Validation for TestPkg" in sql
    assert "SELECT COUNT(*)" in sql
    assert "-- Table Mappings" in sql


def test_generate_notebook(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata)
    nb = validator.generate_notebook(report)
    assert nb["nbformat"] == 4
    assert "cells" in nb
    assert len(nb["cells"]) > 0
    code_cells = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert len(code_cells) > 0
    assert "%sql" in "".join(code_cells[0]["source"])


def test_report_to_json(validator, simple_plan, source_metadata, target_metadata):
    report = validator.generate(simple_plan, source_metadata, target_metadata)
    raw = report.to_json()
    parsed = json.loads(raw)
    assert parsed["package_name"] == "TestPkg"
    assert len(parsed["queries"]) > 0


def test_derive_target_table_dimension(validator, target_metadata):
    tables = validator._index_tables(target_metadata)
    result = validator._derive_target_table("load_city_dimension", "DIMENSION_LOAD", tables)
    assert result == "Dimension.City"


def test_derive_target_table_fact(validator, target_metadata):
    tables = validator._index_tables(target_metadata)
    result = validator._derive_target_table("load_movement_fact", "FACT_LOAD", tables)
    assert result == "Fact.Movement"


def test_derive_target_table_unknown_type(validator, target_metadata):
    tables = validator._index_tables(target_metadata)
    result = validator._derive_target_table("load_something_other", "OTHER", tables)
    assert result == ""


def test_qualify_two_parts(validator):
    assert validator._qualify("Dimension.City", "cat", "sch") == "cat.Dimension.City"


def test_qualify_one_part(validator):
    assert validator._qualify("MyTable", "cat", "sch") == "cat.sch.MyTable"


def test_qualify_empty(validator):
    assert validator._qualify("", "cat", "sch") == ""


def test_generate_with_empty_plan(validator, source_metadata, target_metadata):
    plan = {"package_name": "Empty", "steps": []}
    report = validator.generate(plan, source_metadata, target_metadata)
    assert report.summary["total_checks"] == 0
    assert report.summary["table_count"] == 0


def test_generate_handles_missing_target_table(validator, source_metadata, target_metadata):
    plan = {
        "package_name": "Test",
        "steps": [
            {
                "step_id": "load_unknown_dimension",
                "name": "Unknown",
                "step_type": "DIMENSION_LOAD",
                "source_objects": [],
                "target_objects": [],
                "dependencies": [],
                "metadata": {},
            },
        ],
    }
    report = validator.generate(plan, source_metadata, target_metadata)
    # No target table derived -> no queries for this step
    step_queries = [q for q in report.queries if q.step_id == "load_unknown_dimension"]
    assert len(step_queries) == 0