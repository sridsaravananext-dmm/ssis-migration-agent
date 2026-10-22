/*
    File: customer_etl_migration.sql
    Purpose: Representative SQL Server ETL script for T-SQL parser development.

    NOTE:
    This is a parser test fixture. It references example source tables and a
    stored procedure that may not exist in your database. Do not execute it
    against production. Parsing does not require a database connection.
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

-- 1. Declare and initialize variables
DECLARE @BatchID INT = 1001;
DECLARE @LoadDate DATETIME2(0) = SYSUTCDATETIME();
DECLARE @SourceSystem NVARCHAR(50) = N'CRM';
DECLARE @RowsInserted INT = 0;
DECLARE @RowsUpdated INT = 0;

-- 2. Create a temporary staging table
IF OBJECT_ID('tempdb..#CustomerStage') IS NOT NULL
    DROP TABLE #CustomerStage;

CREATE TABLE #CustomerStage
(
    CustomerID       INT            NOT NULL,
    CustomerName     NVARCHAR(200)  NOT NULL,
    EmailAddress     NVARCHAR(320)  NULL,
    City             NVARCHAR(100)  NULL,
    StateCode        NVARCHAR(20)   NULL,
    ModifiedDate     DATETIME2(0)   NULL,
    SourceSystem     NVARCHAR(50)   NOT NULL
);

-- 3. Extract and transform data using a CTE and JOIN
;WITH LatestCustomer AS
(
    SELECT
        c.CustomerID,
        c.CustomerName,
        c.EmailAddress,
        a.City,
        a.StateCode,
        c.ModifiedDate,
        ROW_NUMBER() OVER
        (
            PARTITION BY c.CustomerID
            ORDER BY c.ModifiedDate DESC
        ) AS RowNum
    FROM dbo.SourceCustomer AS c
    LEFT JOIN dbo.CustomerAddress AS a
        ON c.CustomerID = a.CustomerID
    WHERE c.IsActive = 1
)
INSERT INTO #CustomerStage
(
    CustomerID,
    CustomerName,
    EmailAddress,
    City,
    StateCode,
    ModifiedDate,
    SourceSystem
)
SELECT
    CustomerID,
    CustomerName,
    EmailAddress,
    City,
    StateCode,
    ModifiedDate,
    @SourceSystem
FROM LatestCustomer
WHERE RowNum = 1;

-- 4. Capture how many rows were staged
SELECT @RowsInserted = COUNT(*)
FROM #CustomerStage;

-- 5. Update existing target records and insert new records
BEGIN TRY
    BEGIN TRANSACTION;

    UPDATE target
    SET
        target.CustomerName = source.CustomerName,
        target.EmailAddress = source.EmailAddress,
        target.City = source.City,
        target.StateCode = source.StateCode,
        target.ModifiedDate = @LoadDate,
        target.SourceSystem = source.SourceSystem
    FROM dbo.DimCustomer AS target
    INNER JOIN #CustomerStage AS source
        ON target.CustomerID = source.CustomerID
    WHERE
        ISNULL(target.CustomerName, N'') <> ISNULL(source.CustomerName, N'')
        OR ISNULL(target.EmailAddress, N'') <> ISNULL(source.EmailAddress, N'')
        OR ISNULL(target.City, N'') <> ISNULL(source.City, N'')
        OR ISNULL(target.StateCode, N'') <> ISNULL(source.StateCode, N'');

    SET @RowsUpdated = @@ROWCOUNT;

    INSERT INTO dbo.DimCustomer
    (
        CustomerID,
        CustomerName,
        EmailAddress,
        City,
        StateCode,
        CreatedDate,
        ModifiedDate,
        SourceSystem
    )
    SELECT
        source.CustomerID,
        source.CustomerName,
        source.EmailAddress,
        source.City,
        source.StateCode,
        @LoadDate,
        @LoadDate,
        source.SourceSystem
    FROM #CustomerStage AS source
    WHERE NOT EXISTS
    (
        SELECT 1
        FROM dbo.DimCustomer AS target
        WHERE target.CustomerID = source.CustomerID
    );

    SET @RowsInserted = @@ROWCOUNT;

    -- 6. Record batch-level audit information
    INSERT INTO dbo.ETLBatchAudit
    (
        BatchID,
        SourceSystem,
        LoadDate,
        RowsInserted,
        RowsUpdated,
        Status
    )
    VALUES
    (
        @BatchID,
        @SourceSystem,
        @LoadDate,
        @RowsInserted,
        @RowsUpdated,
        N'SUCCESS'
    );

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;

    INSERT INTO dbo.ETLBatchAudit
    (
        BatchID,
        SourceSystem,
        LoadDate,
        RowsInserted,
        RowsUpdated,
        Status,
        ErrorNumber,
        ErrorMessage
    )
    VALUES
    (
        @BatchID,
        @SourceSystem,
        @LoadDate,
        @RowsInserted,
        @RowsUpdated,
        N'FAILED',
        ERROR_NUMBER(),
        ERROR_MESSAGE()
    );

    THROW;
END CATCH;

-- 7. Example stored procedure call
EXEC dbo.usp_RebuildCustomerSummary
    @BatchID = @BatchID,
    @LoadDate = @LoadDate;

-- 8. Example conditional logic
IF @RowsInserted > 0 OR @RowsUpdated > 0
BEGIN
    PRINT N'Customer ETL completed with changes.';
END
ELSE
BEGIN
    PRINT N'Customer ETL completed with no changes.';
END;

-- 9. Example delete statement for expired staging data
DELETE FROM dbo.CustomerStageArchive
WHERE ArchiveDate < DATEADD(DAY, -90, @LoadDate);

-- 10. Example MERGE statement for a reference table
MERGE dbo.DimState AS target
USING dbo.SourceState AS source
    ON target.StateCode = source.StateCode
WHEN MATCHED AND target.StateName <> source.StateName THEN
    UPDATE SET
        target.StateName = source.StateName,
        target.ModifiedDate = @LoadDate
WHEN NOT MATCHED BY TARGET THEN
    INSERT (StateCode, StateName, ModifiedDate)
    VALUES (source.StateCode, source.StateName, @LoadDate);

-- 11. Example dynamic SQL (the parser should preserve, not execute, this)
DECLARE @DynamicSQL NVARCHAR(MAX);

SET @DynamicSQL =
    N'SELECT CustomerID, CustomerName FROM dbo.DimCustomer WHERE SourceSystem = @System;';

EXEC sys.sp_executesql
    @DynamicSQL,
    N'@System NVARCHAR(50)',
    @System = @SourceSystem;

-- 12. Clean up temporary resources
IF OBJECT_ID('tempdb..#CustomerStage') IS NOT NULL
    DROP TABLE #CustomerStage;

SET NOCOUNT OFF;
