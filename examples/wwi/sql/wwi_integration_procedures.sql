-- WideWorldImporters Integration Schema Stored Procedures
-- Source: Microsoft WideWorldImporters sample database
-- These procedures implement the ETL pipeline for the WWI data warehouse

-- ============================================================
-- Control / Metadata procedures
-- ============================================================

CREATE PROCEDURE Integration.GetLineageKey
    @TableName nvarchar(128),
    @LineageKey int OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SELECT @LineageKey = MAX(LineageKey)
    FROM Integration.Lineage
    WHERE TableName = @TableName;
END;

CREATE PROCEDURE Integration.GetLastETLCutoffTime
    @TableName nvarchar(128)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @CutoffTime datetime2(7) = '2000-01-01';
    SELECT @CutoffTime = MAX(CutoffTime)
    FROM Integration.[ETL Cutoff]
    WHERE TableName = @TableName;
    SELECT @CutoffTime AS CutoffTime;
END;

CREATE PROCEDURE Integration.PopulateDateDimensionForYear
    @YearNumber int
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS(SELECT 1 FROM Dimension.[Date] WHERE [Date] = DATEFROMPARTS(@YearNumber, 1, 1))
    BEGIN
        WITH Dates AS (
            SELECT DATEFROMPARTS(@YearNumber, 1, 1) AS [Date]
            UNION ALL
            SELECT DATEADD(day, 1, [Date])
            FROM Dates
            WHERE [Date] < DATEFROMPARTS(@YearNumber, 12, 31)
        )
        INSERT INTO Dimension.[Date]
        (
            [Date], [Day Number], [Day], [Month], [Short Month], [Calendar Month Number],
            [Calendar Month Label], [Calendar Year], [Calendar Year Label], [Fiscal Month Number],
            [Fiscal Month Label], [Fiscal Year], [Fiscal Year Label], [ISO Week Number]
        )
        SELECT
            d.[Date], DAY(d.[Date]), DATENAME(weekday, d.[Date]), DATENAME(month, d.[Date]),
            LEFT(DATENAME(month, d.[Date]), 3), MONTH(d.[Date]),
            DATENAME(month, d.[Date]) + ' ' + CONVERT(nvarchar(4), YEAR(d.[Date])),
            YEAR(d.[Date]), 'CY' + CONVERT(nvarchar(4), YEAR(d.[Date])),
            CASE WHEN MONTH(d.[Date]) <= 6 THEN MONTH(d.[Date]) + 6 ELSE MONTH(d.[Date]) - 6 END,
            LEFT(DATENAME(month, d.[Date]), 3) + ' FY' + CONVERT(nvarchar(4), YEAR(d.[Date]) + 1),
            CASE WHEN MONTH(d.[Date]) <= 6 THEN YEAR(d.[Date]) ELSE YEAR(d.[Date]) + 1 END,
            'FY' + CONVERT(nvarchar(4), YEAR(d.[Date]) + 1),
            DATEPART(ISO_WEEK, d.[Date])
        FROM Dates d
        OPTION (MAXRECURSION 366);
    END;
END;

-- ============================================================
-- Extract procedures: GetXxxUpdates (INSERT INTO staging)
-- ============================================================

CREATE PROCEDURE Integration.GetCityUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.City_Staging ([WWI City ID], City, [State Province], Country, Continent, [Sales Territory], Region, Subregion, [Location], [Latest Recorded Population], [Valid From], [Valid To])
    SELECT c.CityID, c.CityName, sp.StateProvinceName, sp.CountryName, sp.Continent, sp.SalesTerritory, sp.Region, sp.Subregion, c.Location, c.LatestRecordedPopulation, @LastCutoffTime, @NewCutoffTime
    FROM Application.Cities c
    INNER JOIN Application.StateProvinces sp ON c.StateProvinceID = sp.StateProvinceID
    WHERE c.LastEditedWhen > @LastCutoffTime AND c.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetCustomerUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.Customer_Staging ([WWI Customer ID], Customer, [Bill To Customer], Category, [Buying Group], [Primary Contact], [Postal Code], [Valid From], [Valid To])
    SELECT c.CustomerID, c.CustomerName, bt.CustomerName, c.CustomerCategoryName, bg.BuyingGroupName, p.FullName, c.DeliveryPostalCode, @LastCutoffTime, @NewCutoffTime
    FROM Sales.Customers c
    LEFT OUTER JOIN Sales.Customers bt ON c.BillToCustomerID = bt.CustomerID
    LEFT OUTER JOIN Sales.BuyingGroups bg ON c.BuyingGroupID = bg.BuyingGroupID
    LEFT OUTER JOIN Application.People p ON c.PrimaryContactPersonID = p.PersonID
    WHERE c.LastEditedWhen > @LastCutoffTime AND c.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetEmployeeUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.Employee_Staging ([WWI Employee ID], Employee, [Preferred Name], [Is Salesperson], Photo, [Valid From], [Valid To])
    SELECT p.PersonID, p.FullName, p.PreferredName, p.IsSalesperson, p.Photo, @LastCutoffTime, @NewCutoffTime
    FROM Application.People p
    WHERE p.IsEmployee = 1 AND p.LastEditedWhen > @LastCutoffTime AND p.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetPaymentMethodUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.PaymentMethod_Staging ([WWI Payment Method ID], [Payment Method], [Valid From], [Valid To])
    SELECT pm.PaymentMethodID, pm.PaymentMethodName, @LastCutoffTime, @NewCutoffTime
    FROM Application.PaymentMethods pm
    WHERE pm.LastEditedWhen > @LastCutoffTime AND pm.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetStockItemUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.StockItem_Staging ([WWI Stock Item ID], [Stock Item], Color, [Selling Package], [Buying Package], Brand, Size, [Lead Time Days], [Quantity Per Outer], [Is Chiller Stock], Barcode, [Tax Rate], [Unit Price], [Recommended Retail Price], [Typical Weight Per Unit], Photo, [Valid From], [Valid To])
    SELECT si.StockItemID, si.StockItemName, c.ColorName, sp.PackageTypeName, bp.PackageTypeName, si.Brand, si.Size, si.LeadTimeDays, si.QuantityPerOuter, si.IsChillerStock, si.Barcode, si.TaxRate, si.UnitPrice, si.RecommendedRetailPrice, si.TypicalWeightPerUnit, si.Photo, @LastCutoffTime, @NewCutoffTime
    FROM Warehouse.StockItems si
    LEFT OUTER JOIN Warehouse.Colors c ON si.ColorID = c.ColorID
    LEFT OUTER JOIN Warehouse.PackageTypes sp ON si.UnitPackageID = sp.PackageTypeID
    LEFT OUTER JOIN Warehouse.PackageTypes bp ON si.OuterPackageID = bp.PackageTypeID
    WHERE si.LastEditedWhen > @LastCutoffTime AND si.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetSupplierUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.Supplier_Staging ([WWI Supplier ID], Supplier, Category, [Primary Contact], [Supplier Reference], [Payment Days], [Postal Code], [Valid From], [Valid To])
    SELECT s.SupplierID, s.SupplierName, sc.SupplierCategoryName, p.FullName, s.SupplierReference, s.PaymentDays, s.DeliveryPostalCode, @LastCutoffTime, @NewCutoffTime
    FROM Purchasing.Suppliers s
    LEFT OUTER JOIN Purchasing.SupplierCategories sc ON s.SupplierCategoryID = sc.SupplierCategoryID
    LEFT OUTER JOIN Application.People p ON s.PrimaryContactPersonID = p.PersonID
    WHERE s.LastEditedWhen > @LastCutoffTime AND s.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetTransactionTypeUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.TransactionType_Staging ([WWI Transaction Type ID], [Transaction Type], [Valid From], [Valid To])
    SELECT tt.TransactionTypeID, tt.TransactionTypeName, @LastCutoffTime, @NewCutoffTime
    FROM Application.TransactionTypes tt
    WHERE tt.LastEditedWhen > @LastCutoffTime AND tt.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetMovementUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.Movement_Staging ([Date Key], [WWI Stock Item Transaction ID], [WWI Invoice ID], [WWI Purchase Order ID], [Quantity], [WWI Stock Item ID], [WWI Customer ID], [WWI Supplier ID], [WWI Transaction Type ID], [Last Modified When])
    SELECT CAST(sit.TransactionOccurredWhen AS date), sit.StockItemTransactionID, sit.InvoiceID, sit.PurchaseOrderID, sit.Quantity, sit.StockItemID, NULL, NULL, sit.TransactionTypeID, sit.LastEditedWhen
    FROM Warehouse.StockItemTransactions sit
    WHERE sit.LastEditedWhen > @LastCutoffTime AND sit.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetOrderUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.Order_Staging ([Order Date Key], [Picked Date Key], [WWI Order ID], [WWI Backorder ID], [Description], [Package], [Quantity], [Unit Price], [Tax Rate], [Total Excluding Tax], [Tax Amount], [Total Including Tax], [WWI City ID], [WWI Customer ID], [WWI Stock Item ID], [WWI Salesperson ID], [WWI Picker ID], [Last Modified When])
    SELECT CAST(o.OrderDate AS date), CAST(o.PickingCompletedWhen AS date), ol.OrderID, ol.BackorderOrderID, si.StockItemName, pt.PackageTypeName, ol.Quantity, ol.UnitPrice, ol.TaxRate, ol.LineProfit, ol.TaxAmount, ol.ExtendedPrice, c.DeliveryCityID, o.CustomerID, ol.StockItemID, o.SalespersonPersonID, o.PickedByPersonID, ol.LastEditedWhen
    FROM Sales.OrderLines ol
    INNER JOIN Sales.Orders o ON ol.OrderID = o.OrderID
    INNER JOIN Warehouse.StockItems si ON ol.StockItemID = si.StockItemID
    INNER JOIN Warehouse.PackageTypes pt ON si.UnitPackageID = pt.PackageTypeID
    LEFT OUTER JOIN Sales.Customers c ON o.CustomerID = c.CustomerID
    WHERE ol.LastEditedWhen > @LastCutoffTime AND ol.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetPurchaseUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.Purchase_Staging ([Date Key], [WWI Purchase Order ID], [Ordered Outers], [Ordered Quantity], [Received Outers], [Package], [Is Order Line Finalized], [WWI Supplier ID], [WWI Stock Item ID], [Last Modified When])
    SELECT CAST(po.OrderDate AS date), pol.PurchaseOrderID, pol.OrderedOuters, pol.OrderedQuantity, pol.ReceivedOuters, pt.PackageTypeName, pol.IsOrderLineFinalized, po.SupplierID, pol.StockItemID, pol.LastEditedWhen
    FROM Purchasing.PurchaseOrderLines pol
    INNER JOIN Purchasing.PurchaseOrders po ON pol.PurchaseOrderID = po.PurchaseOrderID
    INNER JOIN Warehouse.StockItems si ON pol.StockItemID = si.StockItemID
    INNER JOIN Warehouse.PackageTypes pt ON si.UnitPackageID = pt.PackageTypeID
    WHERE pol.LastEditedWhen > @LastCutoffTime AND pol.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetSaleUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.Sale_Staging ([Invoice Date Key], [Delivery Date Key], [WWI Invoice ID], [Description], [Package], [Quantity], [Unit Price], [Tax Rate], [Total Excluding Tax], [Tax Amount], [Profit], [Total Including Tax], [Total Dry Items], [Total Chiller Items], [WWI City ID], [WWI Customer ID], [WWI Bill To Customer ID], [WWI Stock Item ID], [WWI Salesperson ID], [Last Modified When])
    SELECT CAST(i.InvoiceDate AS date), CAST(i.DeliveryDate AS date), il.InvoiceID, si.StockItemName, pt.PackageTypeName, il.Quantity, il.UnitPrice, il.TaxRate, il.LineProfit, il.TaxAmount, il.LineProfit, il.ExtendedPrice, il.TotalDryItems, il.TotalChillerItems, c.DeliveryCityID, i.CustomerID, i.BillToCustomerID, il.StockItemID, i.SalespersonPersonID, il.LastEditedWhen
    FROM Sales.InvoiceLines il
    INNER JOIN Sales.Invoices i ON il.InvoiceID = i.InvoiceID
    INNER JOIN Warehouse.StockItems si ON il.StockItemID = si.StockItemID
    INNER JOIN Warehouse.PackageTypes pt ON si.UnitPackageID = pt.PackageTypeID
    LEFT OUTER JOIN Sales.Customers c ON i.CustomerID = c.CustomerID
    WHERE il.LastEditedWhen > @LastCutoffTime AND il.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetStockHoldingUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.StockHolding_Staging ([Quantity On Hand], [Bin Location], [Last Stocktake Quantity], [Last Cost Price], [Reorder Level], [Target Stock Level], [WWI Stock Item ID])
    SELECT sih.QuantityOnHand, sih.BinLocation, sih.LastStocktakeQuantity, sih.LastCostPrice, sih.ReorderLevel, sih.TargetStockLevel, sih.StockItemID
    FROM Warehouse.StockItemHoldings sih
    WHERE sih.LastEditedWhen > @LastCutoffTime AND sih.LastEditedWhen <= @NewCutoffTime;
END;

CREATE PROCEDURE Integration.GetTransactionUpdates @LastCutoffTime datetime2(7), @NewCutoffTime datetime2(7) AS BEGIN
    SET NOCOUNT ON;
    INSERT INTO Integration.Transaction_Staging ([Date Key], [WWI Customer Transaction ID], [WWI Supplier Transaction ID], [WWI Invoice ID], [WWI Purchase Order ID], [Supplier Invoice Number], [Total Excluding Tax], [Tax Amount], [Total Including Tax], [Outstanding Balance], [Is Finalized], [WWI Customer ID], [WWI Bill To Customer ID], [WWI Supplier ID], [WWI Transaction Type ID], [WWI Payment Method ID], [Last Modified When])
    SELECT CAST(ct.TransactionDate AS date), ct.CustomerTransactionID, NULL, ct.InvoiceID, NULL, NULL, ct.TransactionAmount, ct.TaxAmount, ct.TransactionAmount + ct.TaxAmount, ct.OutstandingBalance, ct.IsFinalized, ct.CustomerID, NULL, NULL, NULL, NULL, ct.LastEditedWhen
    FROM Sales.CustomerTransactions ct
    WHERE ct.LastEditedWhen > @LastCutoffTime AND ct.LastEditedWhen <= @NewCutoffTime;
END;

-- ============================================================
-- Migrate procedures: MigrateStagedXxxData (MERGE SCD Type 2)
-- ============================================================

CREATE PROCEDURE Integration.MigrateStagedCityData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'City', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Dimension.City AS tgt
    USING (SELECT [WWI City ID], City, [State Province], Country, Continent, [Sales Territory], Region, Subregion, [Location], [Latest Recorded Population], [Valid From], [Valid To] FROM Integration.City_Staging) AS src
    ON (tgt.[WWI City ID] = src.[WWI City ID] AND tgt.[Valid To] = src.[Valid From])
    WHEN MATCHED AND EXISTS (SELECT src.City, src.[State Province], src.Country, src.Continent, src.[Sales Territory], src.Region, src.Subregion, src.[Location], src.[Latest Recorded Population] EXCEPT SELECT tgt.City, tgt.[State Province], tgt.Country, tgt.Continent, tgt.[Sales Territory], tgt.Region, tgt.Subregion, tgt.[Location], tgt.[Latest Recorded Population])
    THEN UPDATE SET tgt.[Valid To] = src.[Valid To], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET AND src.[Valid To] = '9999-12-31 23:59:59.9999999'
    THEN INSERT ([WWI City ID], City, [State Province], Country, Continent, [Sales Territory], Region, Subregion, [Location], [Latest Recorded Population], [Valid From], [Valid To], [Lineage Key])
    VALUES (src.[WWI City ID], src.City, src.[State Province], src.Country, src.Continent, src.[Sales Territory], src.Region, src.Subregion, src.[Location], src.[Latest Recorded Population], src.[Valid From], src.[Valid To], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedCustomerData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Customer', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Dimension.Customer AS tgt
    USING (SELECT [WWI Customer ID], Customer, [Bill To Customer], Category, [Buying Group], [Primary Contact], [Postal Code], [Valid From], [Valid To] FROM Integration.Customer_Staging) AS src
    ON (tgt.[WWI Customer ID] = src.[WWI Customer ID] AND tgt.[Valid To] = src.[Valid From])
    WHEN MATCHED AND EXISTS (SELECT src.Customer, src.[Bill To Customer], src.Category, src.[Buying Group], src.[Primary Contact], src.[Postal Code] EXCEPT SELECT tgt.Customer, tgt.[Bill To Customer], tgt.Category, tgt.[Buying Group], tgt.[Primary Contact], tgt.[Postal Code])
    THEN UPDATE SET tgt.[Valid To] = src.[Valid To], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET AND src.[Valid To] = '9999-12-31 23:59:59.9999999'
    THEN INSERT ([WWI Customer ID], Customer, [Bill To Customer], Category, [Buying Group], [Primary Contact], [Postal Code], [Valid From], [Valid To], [Lineage Key])
    VALUES (src.[WWI Customer ID], src.Customer, src.[Bill To Customer], src.Category, src.[Buying Group], src.[Primary Contact], src.[Postal Code], src.[Valid From], src.[Valid To], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedEmployeeData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Employee', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Dimension.Employee AS tgt
    USING (SELECT [WWI Employee ID], Employee, [Preferred Name], [Is Salesperson], Photo, [Valid From], [Valid To] FROM Integration.Employee_Staging) AS src
    ON (tgt.[WWI Employee ID] = src.[WWI Employee ID] AND tgt.[Valid To] = src.[Valid From])
    WHEN MATCHED AND EXISTS (SELECT src.Employee, src.[Preferred Name], src.[Is Salesperson], src.Photo EXCEPT SELECT tgt.Employee, tgt.[Preferred Name], tgt.[Is Salesperson], tgt.Photo)
    THEN UPDATE SET tgt.[Valid To] = src.[Valid To], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET AND src.[Valid To] = '9999-12-31 23:59:59.9999999'
    THEN INSERT ([WWI Employee ID], Employee, [Preferred Name], [Is Salesperson], Photo, [Valid From], [Valid To], [Lineage Key])
    VALUES (src.[WWI Employee ID], src.Employee, src.[Preferred Name], src.[Is Salesperson], src.Photo, src.[Valid From], src.[Valid To], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedPaymentMethodData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Payment Method', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Dimension.[Payment Method] AS tgt
    USING (SELECT [WWI Payment Method ID], [Payment Method], [Valid From], [Valid To] FROM Integration.PaymentMethod_Staging) AS src
    ON (tgt.[WWI Payment Method ID] = src.[WWI Payment Method ID] AND tgt.[Valid To] = src.[Valid From])
    WHEN MATCHED AND EXISTS (SELECT src.[Payment Method] EXCEPT SELECT tgt.[Payment Method])
    THEN UPDATE SET tgt.[Valid To] = src.[Valid To], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET AND src.[Valid To] = '9999-12-31 23:59:59.9999999'
    THEN INSERT ([WWI Payment Method ID], [Payment Method], [Valid From], [Valid To], [Lineage Key])
    VALUES (src.[WWI Payment Method ID], src.[Payment Method], src.[Valid From], src.[Valid To], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedStockItemData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Stock Item', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Dimension.[Stock Item] AS tgt
    USING (SELECT [WWI Stock Item ID], [Stock Item], Color, [Selling Package], [Buying Package], Brand, Size, [Lead Time Days], [Quantity Per Outer], [Is Chiller Stock], Barcode, [Tax Rate], [Unit Price], [Recommended Retail Price], [Typical Weight Per Unit], Photo, [Valid From], [Valid To] FROM Integration.StockItem_Staging) AS src
    ON (tgt.[WWI Stock Item ID] = src.[WWI Stock Item ID] AND tgt.[Valid To] = src.[Valid From])
    WHEN MATCHED AND EXISTS (SELECT src.[Stock Item], src.Color, src.[Selling Package], src.[Buying Package], src.Brand, src.Size, src.[Lead Time Days], src.[Quantity Per Outer], src.[Is Chiller Stock], src.Barcode, src.[Tax Rate], src.[Unit Price], src.[Recommended Retail Price], src.[Typical Weight Per Unit], src.Photo EXCEPT SELECT tgt.[Stock Item], tgt.Color, tgt.[Selling Package], tgt.[Buying Package], tgt.Brand, tgt.Size, tgt.[Lead Time Days], tgt.[Quantity Per Outer], tgt.[Is Chiller Stock], tgt.Barcode, tgt.[Tax Rate], tgt.[Unit Price], tgt.[Recommended Retail Price], tgt.[Typical Weight Per Unit], tgt.Photo)
    THEN UPDATE SET tgt.[Valid To] = src.[Valid To], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET AND src.[Valid To] = '9999-12-31 23:59:59.9999999'
    THEN INSERT ([WWI Stock Item ID], [Stock Item], Color, [Selling Package], [Buying Package], Brand, Size, [Lead Time Days], [Quantity Per Outer], [Is Chiller Stock], Barcode, [Tax Rate], [Unit Price], [Recommended Retail Price], [Typical Weight Per Unit], Photo, [Valid From], [Valid To], [Lineage Key])
    VALUES (src.[WWI Stock Item ID], src.[Stock Item], src.Color, src.[Selling Package], src.[Buying Package], src.Brand, src.Size, src.[Lead Time Days], src.[Quantity Per Outer], src.[Is Chiller Stock], src.Barcode, src.[Tax Rate], src.[Unit Price], src.[Recommended Retail Price], src.[Typical Weight Per Unit], src.Photo, src.[Valid From], src.[Valid To], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedSupplierData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Supplier', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Dimension.Supplier AS tgt
    USING (SELECT [WWI Supplier ID], Supplier, Category, [Primary Contact], [Supplier Reference], [Payment Days], [Postal Code], [Valid From], [Valid To] FROM Integration.Supplier_Staging) AS src
    ON (tgt.[WWI Supplier ID] = src.[WWI Supplier ID] AND tgt.[Valid To] = src.[Valid From])
    WHEN MATCHED AND EXISTS (SELECT src.Supplier, src.Category, src.[Primary Contact], src.[Supplier Reference], src.[Payment Days], src.[Postal Code] EXCEPT SELECT tgt.Supplier, tgt.Category, tgt.[Primary Contact], tgt.[Supplier Reference], tgt.[Payment Days], tgt.[Postal Code])
    THEN UPDATE SET tgt.[Valid To] = src.[Valid To], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET AND src.[Valid To] = '9999-12-31 23:59:59.9999999'
    THEN INSERT ([WWI Supplier ID], Supplier, Category, [Primary Contact], [Supplier Reference], [Payment Days], [Postal Code], [Valid From], [Valid To], [Lineage Key])
    VALUES (src.[WWI Supplier ID], src.Supplier, src.Category, src.[Primary Contact], src.[Supplier Reference], src.[Payment Days], src.[Postal Code], src.[Valid From], src.[Valid To], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedTransactionTypeData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Transaction Type', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Dimension.[Transaction Type] AS tgt
    USING (SELECT [WWI Transaction Type ID], [Transaction Type], [Valid From], [Valid To] FROM Integration.TransactionType_Staging) AS src
    ON (tgt.[WWI Transaction Type ID] = src.[WWI Transaction Type ID] AND tgt.[Valid To] = src.[Valid From])
    WHEN MATCHED AND EXISTS (SELECT src.[Transaction Type] EXCEPT SELECT tgt.[Transaction Type])
    THEN UPDATE SET tgt.[Valid To] = src.[Valid To], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET AND src.[Valid To] = '9999-12-31 23:59:59.9999999'
    THEN INSERT ([WWI Transaction Type ID], [Transaction Type], [Valid From], [Valid To], [Lineage Key])
    VALUES (src.[WWI Transaction Type ID], src.[Transaction Type], src.[Valid From], src.[Valid To], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedMovementData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Movement', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Fact.Movement AS tgt
    USING (SELECT [Date Key], [WWI Stock Item Transaction ID], [WWI Invoice ID], [WWI Purchase Order ID], [Quantity], [WWI Stock Item ID], [WWI Customer ID], [WWI Supplier ID], [WWI Transaction Type ID], [Last Modified When] FROM Integration.Movement_Staging) AS src
    ON (tgt.[WWI Stock Item Transaction ID] = src.[WWI Stock Item Transaction ID])
    WHEN MATCHED AND EXISTS (SELECT src.[Date Key], src.[Quantity], src.[WWI Stock Item ID], src.[WWI Customer ID], src.[WWI Supplier ID], src.[WWI Transaction Type ID] EXCEPT SELECT tgt.[Date Key], tgt.[Quantity], tgt.[WWI Stock Item ID], tgt.[WWI Customer ID], tgt.[WWI Supplier ID], tgt.[WWI Transaction Type ID])
    THEN UPDATE SET tgt.[Date Key] = src.[Date Key], tgt.[Quantity] = src.[Quantity], tgt.[WWI Stock Item ID] = src.[WWI Stock Item ID], tgt.[WWI Customer ID] = src.[WWI Customer ID], tgt.[WWI Supplier ID] = src.[WWI Supplier ID], tgt.[WWI Transaction Type ID] = src.[WWI Transaction Type ID], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET
    THEN INSERT ([Date Key], [WWI Stock Item Transaction ID], [WWI Invoice ID], [WWI Purchase Order ID], [Quantity], [WWI Stock Item ID], [WWI Customer ID], [WWI Supplier ID], [WWI Transaction Type ID], [Lineage Key])
    VALUES (src.[Date Key], src.[WWI Stock Item Transaction ID], src.[WWI Invoice ID], src.[WWI Purchase Order ID], src.[Quantity], src.[WWI Stock Item ID], src.[WWI Customer ID], src.[WWI Supplier ID], src.[WWI Transaction Type ID], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedOrderData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Order', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Fact.[Order] AS tgt
    USING (SELECT [Order Date Key], [Picked Date Key], [WWI Order ID], [WWI Backorder ID], [Description], [Package], [Quantity], [Unit Price], [Tax Rate], [Total Excluding Tax], [Tax Amount], [Total Including Tax], [WWI City ID], [WWI Customer ID], [WWI Stock Item ID], [WWI Salesperson ID], [WWI Picker ID], [Last Modified When] FROM Integration.Order_Staging) AS src
    ON (tgt.[WWI Order ID] = src.[WWI Order ID])
    WHEN MATCHED AND EXISTS (SELECT src.[Order Date Key], src.[Picked Date Key], src.[Description], src.[Package], src.[Quantity], src.[Unit Price], src.[Tax Rate], src.[Total Excluding Tax], src.[Tax Amount], src.[Total Including Tax], src.[WWI City ID], src.[WWI Customer ID], src.[WWI Stock Item ID], src.[WWI Salesperson ID], src.[WWI Picker ID] EXCEPT SELECT tgt.[Order Date Key], tgt.[Picked Date Key], tgt.[Description], tgt.[Package], tgt.[Quantity], tgt.[Unit Price], tgt.[Tax Rate], tgt.[Total Excluding Tax], tgt.[Tax Amount], tgt.[Total Including Tax], tgt.[WWI City ID], tgt.[WWI Customer ID], tgt.[WWI Stock Item ID], tgt.[WWI Salesperson ID], tgt.[WWI Picker ID])
    THEN UPDATE SET tgt.[Order Date Key] = src.[Order Date Key], tgt.[Picked Date Key] = src.[Picked Date Key], tgt.[Description] = src.[Description], tgt.[Package] = src.[Package], tgt.[Quantity] = src.[Quantity], tgt.[Unit Price] = src.[Unit Price], tgt.[Tax Rate] = src.[Tax Rate], tgt.[Total Excluding Tax] = src.[Total Excluding Tax], tgt.[Tax Amount] = src.[Tax Amount], tgt.[Total Including Tax] = src.[Total Including Tax], tgt.[WWI City ID] = src.[WWI City ID], tgt.[WWI Customer ID] = src.[WWI Customer ID], tgt.[WWI Stock Item ID] = src.[WWI Stock Item ID], tgt.[WWI Salesperson ID] = src.[WWI Salesperson ID], tgt.[WWI Picker ID] = src.[WWI Picker ID], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET
    THEN INSERT ([Order Date Key], [Picked Date Key], [WWI Order ID], [WWI Backorder ID], [Description], [Package], [Quantity], [Unit Price], [Tax Rate], [Total Excluding Tax], [Tax Amount], [Total Including Tax], [WWI City ID], [WWI Customer ID], [WWI Stock Item ID], [WWI Salesperson ID], [WWI Picker ID], [Lineage Key])
    VALUES (src.[Order Date Key], src.[Picked Date Key], src.[WWI Order ID], src.[WWI Backorder ID], src.[Description], src.[Package], src.[Quantity], src.[Unit Price], src.[Tax Rate], src.[Total Excluding Tax], src.[Tax Amount], src.[Total Including Tax], src.[WWI City ID], src.[WWI Customer ID], src.[WWI Stock Item ID], src.[WWI Salesperson ID], src.[WWI Picker ID], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedPurchaseData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Purchase', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Fact.Purchase AS tgt
    USING (SELECT [Date Key], [WWI Purchase Order ID], [Ordered Outers], [Ordered Quantity], [Received Outers], [Package], [Is Order Line Finalized], [WWI Supplier ID], [WWI Stock Item ID], [Last Modified When] FROM Integration.Purchase_Staging) AS src
    ON (tgt.[WWI Purchase Order ID] = src.[WWI Purchase Order ID] AND tgt.[WWI Stock Item ID] = src.[WWI Stock Item ID])
    WHEN MATCHED AND EXISTS (SELECT src.[Date Key], src.[Ordered Outers], src.[Ordered Quantity], src.[Received Outers], src.[Package], src.[Is Order Line Finalized], src.[WWI Supplier ID], src.[WWI Stock Item ID] EXCEPT SELECT tgt.[Date Key], tgt.[Ordered Outers], tgt.[Ordered Quantity], tgt.[Received Outers], tgt.[Package], tgt.[Is Order Line Finalized], tgt.[WWI Supplier ID], tgt.[WWI Stock Item ID])
    THEN UPDATE SET tgt.[Date Key] = src.[Date Key], tgt.[Ordered Outers] = src.[Ordered Outers], tgt.[Ordered Quantity] = src.[Ordered Quantity], tgt.[Received Outers] = src.[Received Outers], tgt.[Package] = src.[Package], tgt.[Is Order Line Finalized] = src.[Is Order Line Finalized], tgt.[WWI Supplier ID] = src.[WWI Supplier ID], tgt.[WWI Stock Item ID] = src.[WWI Stock Item ID], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET
    THEN INSERT ([Date Key], [WWI Purchase Order ID], [Ordered Outers], [Ordered Quantity], [Received Outers], [Package], [Is Order Line Finalized], [WWI Supplier ID], [WWI Stock Item ID], [Lineage Key])
    VALUES (src.[Date Key], src.[WWI Purchase Order ID], src.[Ordered Outers], src.[Ordered Quantity], src.[Received Outers], src.[Package], src.[Is Order Line Finalized], src.[WWI Supplier ID], src.[WWI Stock Item ID], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedSaleData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Sale', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Fact.Sale AS tgt
    USING (SELECT [Invoice Date Key], [Delivery Date Key], [WWI Invoice ID], [Description], [Package], [Quantity], [Unit Price], [Tax Rate], [Total Excluding Tax], [Tax Amount], [Profit], [Total Including Tax], [Total Dry Items], [Total Chiller Items], [WWI City ID], [WWI Customer ID], [WWI Bill To Customer ID], [WWI Stock Item ID], [WWI Salesperson ID], [Last Modified When] FROM Integration.Sale_Staging) AS src
    ON (tgt.[WWI Invoice ID] = src.[WWI Invoice ID] AND tgt.[WWI Stock Item ID] = src.[WWI Stock Item ID])
    WHEN MATCHED AND EXISTS (SELECT src.[Invoice Date Key], src.[Delivery Date Key], src.[Description], src.[Package], src.[Quantity], src.[Unit Price], src.[Tax Rate], src.[Total Excluding Tax], src.[Tax Amount], src.[Profit], src.[Total Including Tax], src.[Total Dry Items], src.[Total Chiller Items], src.[WWI City ID], src.[WWI Customer ID], src.[WWI Bill To Customer ID], src.[WWI Stock Item ID], src.[WWI Salesperson ID] EXCEPT SELECT tgt.[Invoice Date Key], tgt.[Delivery Date Key], tgt.[Description], tgt.[Package], tgt.[Quantity], tgt.[Unit Price], tgt.[Tax Rate], tgt.[Total Excluding Tax], tgt.[Tax Amount], tgt.[Profit], tgt.[Total Including Tax], tgt.[Total Dry Items], tgt.[Total Chiller Items], tgt.[WWI City ID], tgt.[WWI Customer ID], tgt.[WWI Bill To Customer ID], tgt.[WWI Stock Item ID], tgt.[WWI Salesperson ID])
    THEN UPDATE SET tgt.[Invoice Date Key] = src.[Invoice Date Key], tgt.[Delivery Date Key] = src.[Delivery Date Key], tgt.[Description] = src.[Description], tgt.[Package] = src.[Package], tgt.[Quantity] = src.[Quantity], tgt.[Unit Price] = src.[Unit Price], tgt.[Tax Rate] = src.[Tax Rate], tgt.[Total Excluding Tax] = src.[Total Excluding Tax], tgt.[Tax Amount] = src.[Tax Amount], tgt.[Profit] = src.[Profit], tgt.[Total Including Tax] = src.[Total Including Tax], tgt.[Total Dry Items] = src.[Total Dry Items], tgt.[Total Chiller Items] = src.[Total Chiller Items], tgt.[WWI City ID] = src.[WWI City ID], tgt.[WWI Customer ID] = src.[WWI Customer ID], tgt.[WWI Bill To Customer ID] = src.[WWI Bill To Customer ID], tgt.[WWI Stock Item ID] = src.[WWI Stock Item ID], tgt.[WWI Salesperson ID] = src.[WWI Salesperson ID], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET
    THEN INSERT ([Invoice Date Key], [Delivery Date Key], [WWI Invoice ID], [Description], [Package], [Quantity], [Unit Price], [Tax Rate], [Total Excluding Tax], [Tax Amount], [Profit], [Total Including Tax], [Total Dry Items], [Total Chiller Items], [WWI City ID], [WWI Customer ID], [WWI Bill To Customer ID], [WWI Stock Item ID], [WWI Salesperson ID], [Lineage Key])
    VALUES (src.[Invoice Date Key], src.[Delivery Date Key], src.[WWI Invoice ID], src.[Description], src.[Package], src.[Quantity], src.[Unit Price], src.[Tax Rate], src.[Total Excluding Tax], src.[Tax Amount], src.[Profit], src.[Total Including Tax], src.[Total Dry Items], src.[Total Chiller Items], src.[WWI City ID], src.[WWI Customer ID], src.[WWI Bill To Customer ID], src.[WWI Stock Item ID], src.[WWI Salesperson ID], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedStockHoldingData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Stock Holding', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Fact.[Stock Holding] AS tgt
    USING (SELECT [Quantity On Hand], [Bin Location], [Last Stocktake Quantity], [Last Cost Price], [Reorder Level], [Target Stock Level], [WWI Stock Item ID] FROM Integration.StockHolding_Staging) AS src
    ON (tgt.[WWI Stock Item ID] = src.[WWI Stock Item ID])
    WHEN MATCHED AND EXISTS (SELECT src.[Quantity On Hand], src.[Bin Location], src.[Last Stocktake Quantity], src.[Last Cost Price], src.[Reorder Level], src.[Target Stock Level] EXCEPT SELECT tgt.[Quantity On Hand], tgt.[Bin Location], tgt.[Last Stocktake Quantity], tgt.[Last Cost Price], tgt.[Reorder Level], tgt.[Target Stock Level])
    THEN UPDATE SET tgt.[Quantity On Hand] = src.[Quantity On Hand], tgt.[Bin Location] = src.[Bin Location], tgt.[Last Stocktake Quantity] = src.[Last Stocktake Quantity], tgt.[Last Cost Price] = src.[Last Cost Price], tgt.[Reorder Level] = src.[Reorder Level], tgt.[Target Stock Level] = src.[Target Stock Level], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET
    THEN INSERT ([WWI Stock Item ID], [Quantity On Hand], [Bin Location], [Last Stocktake Quantity], [Last Cost Price], [Reorder Level], [Target Stock Level], [Lineage Key])
    VALUES (src.[WWI Stock Item ID], src.[Quantity On Hand], src.[Bin Location], src.[Last Stocktake Quantity], src.[Last Cost Price], src.[Reorder Level], src.[Target Stock Level], @LineageKey);
END;

CREATE PROCEDURE Integration.MigrateStagedTransactionData AS BEGIN
    SET NOCOUNT ON;
    DECLARE @LineageKey int;
    EXEC Integration.GetLineageKey @TableName = N'Transaction', @LineageKey = @LineageKey OUTPUT;
    MERGE INTO Fact.Transaction AS tgt
    USING (SELECT [Date Key], [WWI Customer Transaction ID], [WWI Supplier Transaction ID], [WWI Invoice ID], [WWI Purchase Order ID], [Supplier Invoice Number], [Total Excluding Tax], [Tax Amount], [Total Including Tax], [Outstanding Balance], [Is Finalized], [WWI Customer ID], [WWI Bill To Customer ID], [WWI Supplier ID], [WWI Transaction Type ID], [WWI Payment Method ID], [Last Modified When] FROM Integration.Transaction_Staging) AS src
    ON (tgt.[WWI Customer Transaction ID] = src.[WWI Customer Transaction ID] OR tgt.[WWI Supplier Transaction ID] = src.[WWI Supplier Transaction ID])
    WHEN MATCHED AND EXISTS (SELECT src.[Date Key], src.[Total Excluding Tax], src.[Tax Amount], src.[Total Including Tax], src.[Outstanding Balance], src.[Is Finalized], src.[WWI Customer ID], src.[WWI Supplier ID], src.[WWI Transaction Type ID], src.[WWI Payment Method ID] EXCEPT SELECT tgt.[Date Key], tgt.[Total Excluding Tax], tgt.[Tax Amount], tgt.[Total Including Tax], tgt.[Outstanding Balance], tgt.[Is Finalized], tgt.[WWI Customer ID], tgt.[WWI Supplier ID], tgt.[WWI Transaction Type ID], tgt.[WWI Payment Method ID])
    THEN UPDATE SET tgt.[Date Key] = src.[Date Key], tgt.[Total Excluding Tax] = src.[Total Excluding Tax], tgt.[Tax Amount] = src.[Tax Amount], tgt.[Total Including Tax] = src.[Total Including Tax], tgt.[Outstanding Balance] = src.[Outstanding Balance], tgt.[Is Finalized] = src.[Is Finalized], tgt.[WWI Customer ID] = src.[WWI Customer ID], tgt.[WWI Supplier ID] = src.[WWI Supplier ID], tgt.[WWI Transaction Type ID] = src.[WWI Transaction Type ID], tgt.[WWI Payment Method ID] = src.[WWI Payment Method ID], tgt.[Lineage Key] = @LineageKey
    WHEN NOT MATCHED BY TARGET
    THEN INSERT ([Date Key], [WWI Customer Transaction ID], [WWI Supplier Transaction ID], [WWI Invoice ID], [WWI Purchase Order ID], [Supplier Invoice Number], [Total Excluding Tax], [Tax Amount], [Total Including Tax], [Outstanding Balance], [Is Finalized], [WWI Customer ID], [WWI Bill To Customer ID], [WWI Supplier ID], [WWI Transaction Type ID], [WWI Payment Method ID], [Lineage Key])
    VALUES (src.[Date Key], src.[WWI Customer Transaction ID], src.[WWI Supplier Transaction ID], src.[WWI Invoice ID], src.[WWI Purchase Order ID], src.[Supplier Invoice Number], src.[Total Excluding Tax], src.[Tax Amount], src.[Total Including Tax], src.[Outstanding Balance], src.[Is Finalized], src.[WWI Customer ID], src.[WWI Bill To Customer ID], src.[WWI Supplier ID], src.[WWI Transaction Type ID], src.[WWI Payment Method ID], @LineageKey);
END;
