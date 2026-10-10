
from __future__ import annotations

import pytest

from ssis_migration.validator.sql_semantic import (
    SQLSemanticValidator,
)


SOURCE_SQL = """
SELECT
    CAST(ct.TransactionDate AS date) AS TransactionDate,
    COALESCE(i.CustomerID, ct.CustomerID) AS CustomerID
FROM Sales.CustomerTransactions AS ct
LEFT OUTER JOIN Sales.Invoices AS i
    ON ct.InvoiceID = i.InvoiceID
WHERE ct.LastEditedWhen > @LastCutoff
  AND ct.LastEditedWhen <= @NewCutoff

UNION ALL

SELECT
    CAST(st.TransactionDate AS date) AS TransactionDate,
    CAST(NULL AS int) AS CustomerID
FROM Purchasing.SupplierTransactions AS st
WHERE st.LastEditedWhen > @LastCutoff
  AND st.LastEditedWhen <= @NewCutoff
"""

TARGET_SQL = """
SELECT
    CAST(ct.TransactionDate AS DATE) AS TransactionDate,
    COALESCE(i.CustomerID, ct.CustomerID) AS CustomerID
FROM Sales.CustomerTransactions AS ct
LEFT OUTER JOIN Sales.Invoices AS i
    ON ct.InvoiceID = i.InvoiceID
WHERE ct.LastEditedWhen > ${LastCutoff}
  AND ct.LastEditedWhen <= ${NewCutoff}

UNION ALL

SELECT
    CAST(st.TransactionDate AS DATE) AS TransactionDate,
    CAST(NULL AS INT) AS CustomerID
FROM Purchasing.SupplierTransactions AS st
WHERE st.LastEditedWhen > ${LastCutoff}
  AND st.LastEditedWhen <= ${NewCutoff}
"""

REQUIRED_FEATURES = {
    "UNION_ALL",
    "LEFT_JOIN",
    "COALESCE",
    "INCREMENTAL_CUTOFF",
}


@pytest.fixture
def validator() -> SQLSemanticValidator:
    return SQLSemanticValidator()


def validate(
    validator: SQLSemanticValidator,
    source_sql: str,
    target_sql: str,
):
    return validator.validate(
        source_sql,
        target_sql,
        source_dialect="tsql",
        target_dialect="databricks",
        required_features=REQUIRED_FEATURES,
    )


def test_valid_translation_preserves_required_features(validator):
    result = validate(validator, SOURCE_SQL, TARGET_SQL)

    assert result.status == "PASS", result.failures
    assert result.checks["projection_structure"]
    assert result.checks["union_count"]
    assert result.checks["union_all_semantics"]
    assert result.checks["source_tables"]
    assert result.checks["required_union_all"]
    assert result.checks["required_left_join"]
    assert result.checks["required_coalesce"]
    assert result.checks["incremental_cutoff"]


def test_missing_left_join_fails_validation(validator):
    target_sql = TARGET_SQL.replace(
        "LEFT OUTER JOIN",
        "INNER JOIN",
        1,
    )

    result = validate(validator, SOURCE_SQL, target_sql)

    assert result.status == "FAIL"
    assert result.checks["required_left_join"] is False


def test_missing_coalesce_fails_validation(validator):
    target_sql = TARGET_SQL.replace(
        "COALESCE(i.CustomerID, ct.CustomerID)",
        "ct.CustomerID",
        1,
    )

    result = validate(validator, SOURCE_SQL, target_sql)

    assert result.status == "FAIL"
    assert result.checks["required_coalesce"] is False


def test_union_all_changed_to_union_fails_validation(validator):
    target_sql = TARGET_SQL.replace(
        "UNION ALL",
        "UNION",
        1,
    )

    result = validate(validator, SOURCE_SQL, target_sql)

    assert result.status == "FAIL"
    assert result.checks["union_all_semantics"] is False
    assert result.checks["required_union_all"] is False


def test_missing_upper_cutoff_fails_validation(validator):
    target_sql = TARGET_SQL.replace(
        "AND ct.LastEditedWhen <= ${NewCutoff}",
        "",
        1,
    )

    result = validate(validator, SOURCE_SQL, target_sql)

    assert result.status == "FAIL"
    assert result.checks["incremental_cutoff"] is False


def test_invalid_target_sql_is_unresolved(validator):
    result = validate(
        validator,
        SOURCE_SQL,
        "SELECT FROM",
    )

    assert result.status == "UNRESOLVED"
    assert result.failures