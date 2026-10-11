
from ssis_migration.resolver.sql_features import (
    detect_required_features,
)


def test_detects_union_all_left_join_coalesce_and_cutoffs():
    sql = """
    SELECT COALESCE(i.CustomerID, ct.CustomerID) AS CustomerID
    FROM Sales.CustomerTransactions AS ct
    LEFT JOIN Sales.Invoices AS i
        ON ct.InvoiceID = i.InvoiceID
    WHERE ct.LastEditedWhen > @LastCutoff
      AND ct.LastEditedWhen <= @NewCutoff

    UNION ALL

    SELECT st.SupplierID AS CustomerID
    FROM Purchasing.SupplierTransactions AS st
    WHERE st.LastEditedWhen > @LastCutoff
      AND st.LastEditedWhen <= @NewCutoff
    """

    features = detect_required_features(
        sql,
        source_dialect="tsql",
    )

    assert features == {
        "UNION_ALL",
        "LEFT_JOIN",
        "COALESCE",
        "INCREMENTAL_CUTOFF",
    }


def test_simple_select_does_not_invent_features():
    sql = "SELECT CustomerID FROM Sales.Customers"

    features = detect_required_features(
        sql,
        source_dialect="tsql",
    )

    assert features == set()


def test_union_without_all_does_not_require_union_all():
    sql = """
    SELECT CustomerID FROM Sales.Customers
    UNION
    SELECT CustomerID FROM Sales.ArchivedCustomers
    """

    features = detect_required_features(
        sql,
        source_dialect="tsql",
    )

    assert "UNION_ALL" not in features

def test_detects_temporal_table_as_of():
    sql = """
        SELECT c.CityID
        FROM [Application].Cities
             FOR SYSTEM_TIME AS OF @ValidFrom AS c
    """

    features = detect_required_features(
        sql,
        source_dialect="tsql",
    )

    assert "TEMPORAL_TABLE_AS_OF" in features