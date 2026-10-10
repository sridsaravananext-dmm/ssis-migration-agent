import sqlglot


SQL = r"""
SELECT
    CAST(ct.TransactionDate AS date) AS [Date Key],
    ct.CustomerTransactionID AS [WWI Customer Transaction ID],
    CAST(NULL AS int) AS [WWI Supplier Transaction ID],
    ct.InvoiceID AS [WWI Invoice ID],
    CAST(NULL AS int) AS [WWI Purchase Order ID],
    CAST(NULL AS nvarchar(20)) AS [Supplier Invoice Number],
    ct.AmountExcludingTax AS [Total Excluding Tax],
    ct.TaxAmount AS [Tax Amount],
    ct.TransactionAmount AS [Total Including Tax],
    ct.OutstandingBalance AS [Outstanding Balance],
    ct.IsFinalized AS [Is Finalized],
    COALESCE(i.CustomerID, ct.CustomerID) AS [WWI Customer ID],
    ct.CustomerID AS [WWI Bill To Customer ID],
    CAST(NULL AS int) AS [WWI Supplier ID],
    ct.TransactionTypeID AS [WWI Transaction Type ID],
    ct.PaymentMethodID AS [WWI Payment Method ID],
    ct.LastEditedWhen AS [Last Modified When]
FROM Sales.CustomerTransactions AS ct
LEFT OUTER JOIN Sales.Invoices AS i
    ON ct.InvoiceID = i.InvoiceID
WHERE
    ct.LastEditedWhen > @LastCutoff
    AND ct.LastEditedWhen <= @NewCutoff

UNION ALL

SELECT
    CAST(st.TransactionDate AS date) AS [Date Key],
    CAST(NULL AS int) AS [WWI Customer Transaction ID],
    st.SupplierTransactionID AS [WWI Supplier Transaction ID],
    CAST(NULL AS int) AS [WWI Invoice ID],
    st.PurchaseOrderID AS [WWI Purchase Order ID],
    st.SupplierInvoiceNumber AS [Supplier Invoice Number],
    st.AmountExcludingTax AS [Total Excluding Tax],
    st.TaxAmount AS [Tax Amount],
    st.TransactionAmount AS [Total Including Tax],
    st.OutstandingBalance AS [Outstanding Balance],
    st.IsFinalized AS [Is Finalized],
    CAST(NULL AS int) AS [WWI Customer ID],
    CAST(NULL AS int) AS [WWI Bill To Customer ID],
    st.SupplierID AS [WWI Supplier ID],
    st.TransactionTypeID AS [WWI Transaction Type ID],
    st.PaymentMethodID AS [WWI Payment Method ID],
    st.LastEditedWhen AS [Last Modified When]
FROM Purchasing.SupplierTransactions AS st
WHERE
    st.LastEditedWhen > @LastCutoff
    AND st.LastEditedWhen <= @NewCutoff
"""


print("=" * 100)
print("PHASE 2A — ACTUAL WWI PROCEDURE → SQLGLOT")
print("=" * 100)

print()
print("SQLGlot version:", sqlglot.__version__)

print()
print("=" * 100)
print("1. PARSE ACTUAL PROCEDURE")
print("=" * 100)

try:
    expressions = sqlglot.parse(
        SQL,
        read="tsql",
    )

    print("✓ T-SQL PARSE SUCCESS")
    print("Statements:", len(expressions))

    for index, expression in enumerate(expressions, start=1):
        print()
        print(f"Expression {index}:")
        print("-" * 80)
        print(expression)

except Exception as e:
    print("✗ T-SQL PARSE FAILED")
    print(type(e).__name__)
    print(e)
    raise


print()
print("=" * 100)
print("2. T-SQL → DATABRICKS")
print("=" * 100)

try:
    converted = sqlglot.transpile(
        SQL,
        read="tsql",
        write="databricks",
    )

    print("✓ TRANSPILATION SUCCESS")
    print("Output statements:", len(converted))

    for index, statement in enumerate(converted, start=1):
        print()
        print(f"Databricks statement {index}:")
        print("-" * 80)
        print(statement)

except Exception as e:
    print("✗ TRANSPILATION FAILED")
    print(type(e).__name__)
    print(e)
    raise


print()
print("=" * 100)
print("PHASE 2A COMPLETE")
print("=" * 100)
