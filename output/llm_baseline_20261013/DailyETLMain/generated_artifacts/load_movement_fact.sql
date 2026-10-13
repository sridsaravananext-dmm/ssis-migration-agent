-- Generated from SSIS migration step: Load Movement Fact
-- Source procedure: Integration.GetMovementUpdates
-- Target staging object: Integration.Movement_Staging
--
-- Deterministic SQL translation from SQL Server
-- to Databricks SQL.

SELECT CAST(sit.TransactionOccurredWhen AS date) AS `Date Key`,
           sit.StockItemTransactionID AS `WWI Stock Item Transaction ID`,
           sit.InvoiceID AS `WWI Invoice ID`,
           sit.PurchaseOrderID AS `WWI Purchase Order ID`,
           CAST(sit.Quantity AS int) AS Quantity,
           sit.StockItemID AS `WWI Stock Item ID`,
           sit.CustomerID AS `WWI Customer ID`,
           sit.SupplierID AS `WWI Supplier ID`,
           sit.TransactionTypeID AS `WWI Transaction Type ID`,
           sit.TransactionOccurredWhen AS `Transaction Occurred When`
    FROM Warehouse.StockItemTransactions AS sit
    WHERE sit.LastEditedWhen > :LastCutoff
    AND sit.LastEditedWhen <= :NewCutoff
    ORDER BY sit.StockItemTransactionID
