CREATE TABLE IF NOT EXISTS `dev_catalog`.`dimension`.`city` (
  `City Key` INT NOT NULL,
  `WWI City ID` INT NOT NULL,
  `City` STRING NOT NULL,
  `State Province` STRING NOT NULL,
  `Country` STRING NOT NULL,
  `Continent` STRING NOT NULL,
  `Sales Territory` STRING NOT NULL,
  `Region` STRING NOT NULL,
  `Subregion` STRING NOT NULL,
  `Location` GEOGRAPHY(4326),
  `Latest Recorded Population` BIGINT NOT NULL,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`dimension`.`customer` (
  `Customer Key` INT NOT NULL,
  `WWI Customer ID` INT NOT NULL,
  `Customer` STRING NOT NULL,
  `Bill To Customer` STRING NOT NULL,
  `Category` STRING NOT NULL,
  `Buying Group` STRING NOT NULL,
  `Primary Contact` STRING NOT NULL,
  `Postal Code` STRING NOT NULL,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`dimension`.`date` (
  `Date` DATE NOT NULL,
  `Day Number` INT NOT NULL,
  `Day` STRING NOT NULL,
  `Month` STRING NOT NULL,
  `Short Month` STRING NOT NULL,
  `Calendar Month Number` INT NOT NULL,
  `Calendar Month Label` STRING NOT NULL,
  `Calendar Year` INT NOT NULL,
  `Calendar Year Label` STRING NOT NULL,
  `Fiscal Month Number` INT NOT NULL,
  `Fiscal Month Label` STRING NOT NULL,
  `Fiscal Year` INT NOT NULL,
  `Fiscal Year Label` STRING NOT NULL,
  `ISO Week Number` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`dimension`.`employee` (
  `Employee Key` INT NOT NULL,
  `WWI Employee ID` INT NOT NULL,
  `Employee` STRING NOT NULL,
  `Preferred Name` STRING NOT NULL,
  `Is Salesperson` BOOLEAN NOT NULL,
  `Photo` BINARY,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`dimension`.`payment_method` (
  `Payment Method Key` INT NOT NULL,
  `WWI Payment Method ID` INT NOT NULL,
  `Payment Method` STRING NOT NULL,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`dimension`.`stock_item` (
  `Stock Item Key` INT NOT NULL,
  `WWI Stock Item ID` INT NOT NULL,
  `Stock Item` STRING NOT NULL,
  `Color` STRING NOT NULL,
  `Selling Package` STRING NOT NULL,
  `Buying Package` STRING NOT NULL,
  `Brand` STRING NOT NULL,
  `Size` STRING NOT NULL,
  `Lead Time Days` INT NOT NULL,
  `Quantity Per Outer` INT NOT NULL,
  `Is Chiller Stock` BOOLEAN NOT NULL,
  `Barcode` STRING,
  `Tax Rate` DECIMAL(18,3) NOT NULL,
  `Unit Price` DECIMAL(18,2) NOT NULL,
  `Recommended Retail Price` DECIMAL(18,2),
  `Typical Weight Per Unit` DECIMAL(18,3) NOT NULL,
  `Photo` BINARY,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`dimension`.`supplier` (
  `Supplier Key` INT NOT NULL,
  `WWI Supplier ID` INT NOT NULL,
  `Supplier` STRING NOT NULL,
  `Category` STRING NOT NULL,
  `Primary Contact` STRING NOT NULL,
  `Supplier Reference` STRING,
  `Payment Days` INT NOT NULL,
  `Postal Code` STRING NOT NULL,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`dimension`.`transaction_type` (
  `Transaction Type Key` INT NOT NULL,
  `WWI Transaction Type ID` INT NOT NULL,
  `Transaction Type` STRING NOT NULL,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`fact`.`movement` (
  `Movement Key` BIGINT NOT NULL,
  `Date Key` DATE NOT NULL,
  `Stock Item Key` INT NOT NULL,
  `Customer Key` INT,
  `Supplier Key` INT,
  `Transaction Type Key` INT NOT NULL,
  `WWI Stock Item Transaction ID` INT NOT NULL,
  `WWI Invoice ID` INT,
  `WWI Purchase Order ID` INT,
  `Quantity` INT NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`fact`.`order` (
  `Order Key` BIGINT NOT NULL,
  `City Key` INT NOT NULL,
  `Customer Key` INT NOT NULL,
  `Stock Item Key` INT NOT NULL,
  `Order Date Key` DATE NOT NULL,
  `Picked Date Key` DATE,
  `Salesperson Key` INT NOT NULL,
  `Picker Key` INT,
  `WWI Order ID` INT NOT NULL,
  `WWI Backorder ID` INT,
  `Description` STRING NOT NULL,
  `Package` STRING NOT NULL,
  `Quantity` INT NOT NULL,
  `Unit Price` DECIMAL(18,2) NOT NULL,
  `Tax Rate` DECIMAL(18,3) NOT NULL,
  `Total Excluding Tax` DECIMAL(18,2) NOT NULL,
  `Tax Amount` DECIMAL(18,2) NOT NULL,
  `Total Including Tax` DECIMAL(18,2) NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`fact`.`purchase` (
  `Purchase Key` BIGINT NOT NULL,
  `Date Key` DATE NOT NULL,
  `Supplier Key` INT NOT NULL,
  `Stock Item Key` INT NOT NULL,
  `WWI Purchase Order ID` INT,
  `Ordered Outers` INT NOT NULL,
  `Ordered Quantity` INT NOT NULL,
  `Received Outers` INT NOT NULL,
  `Package` STRING NOT NULL,
  `Is Order Finalized` BOOLEAN NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`fact`.`sale` (
  `Sale Key` BIGINT NOT NULL,
  `City Key` INT NOT NULL,
  `Customer Key` INT NOT NULL,
  `Bill To Customer Key` INT NOT NULL,
  `Stock Item Key` INT NOT NULL,
  `Invoice Date Key` DATE NOT NULL,
  `Delivery Date Key` DATE,
  `Salesperson Key` INT NOT NULL,
  `WWI Invoice ID` INT NOT NULL,
  `Description` STRING NOT NULL,
  `Package` STRING NOT NULL,
  `Quantity` INT NOT NULL,
  `Unit Price` DECIMAL(18,2) NOT NULL,
  `Tax Rate` DECIMAL(18,3) NOT NULL,
  `Total Excluding Tax` DECIMAL(18,2) NOT NULL,
  `Tax Amount` DECIMAL(18,2) NOT NULL,
  `Profit` DECIMAL(18,2) NOT NULL,
  `Total Including Tax` DECIMAL(18,2) NOT NULL,
  `Total Dry Items` INT NOT NULL,
  `Total Chiller Items` INT NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`fact`.`stock_holding` (
  `Stock Holding Key` BIGINT NOT NULL,
  `Stock Item Key` INT NOT NULL,
  `Quantity On Hand` INT NOT NULL,
  `Bin Location` STRING NOT NULL,
  `Last Stocktake Quantity` INT NOT NULL,
  `Last Cost Price` DECIMAL(18,2) NOT NULL,
  `Reorder Level` INT NOT NULL,
  `Target Stock Level` INT NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`fact`.`transaction` (
  `Transaction Key` BIGINT NOT NULL,
  `Date Key` DATE NOT NULL,
  `Customer Key` INT,
  `Bill To Customer Key` INT,
  `Supplier Key` INT,
  `Transaction Type Key` INT NOT NULL,
  `Payment Method Key` INT,
  `WWI Customer Transaction ID` INT,
  `WWI Supplier Transaction ID` INT,
  `WWI Invoice ID` INT,
  `WWI Purchase Order ID` INT,
  `Supplier Invoice Number` STRING,
  `Total Excluding Tax` DECIMAL(18,2) NOT NULL,
  `Tax Amount` DECIMAL(18,2) NOT NULL,
  `Total Including Tax` DECIMAL(18,2) NOT NULL,
  `Outstanding Balance` DECIMAL(18,2) NOT NULL,
  `Is Finalized` BOOLEAN NOT NULL,
  `Lineage Key` INT NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`city_staging` (
  `City Staging Key` INT NOT NULL,
  `WWI City ID` INT NOT NULL,
  `City` STRING NOT NULL,
  `State Province` STRING NOT NULL,
  `Country` STRING NOT NULL,
  `Continent` STRING NOT NULL,
  `Sales Territory` STRING NOT NULL,
  `Region` STRING NOT NULL,
  `Subregion` STRING NOT NULL,
  `Location` GEOGRAPHY(4326),
  `Latest Recorded Population` BIGINT NOT NULL,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`customer_staging` (
  `Customer Staging Key` INT NOT NULL,
  `WWI Customer ID` INT NOT NULL,
  `Customer` STRING NOT NULL,
  `Bill To Customer` STRING NOT NULL,
  `Category` STRING NOT NULL,
  `Buying Group` STRING NOT NULL,
  `Primary Contact` STRING NOT NULL,
  `Postal Code` STRING NOT NULL,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`employee_staging` (
  `Employee Staging Key` INT NOT NULL,
  `WWI Employee ID` INT NOT NULL,
  `Employee` STRING NOT NULL,
  `Preferred Name` STRING NOT NULL,
  `Is Salesperson` BOOLEAN NOT NULL,
  `Photo` BINARY,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`etl_cutoff` (
  `Table Name` STRING NOT NULL,
  `Cutoff Time` TIMESTAMP NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`lineage` (
  `Lineage Key` INT NOT NULL,
  `Data Load Started` TIMESTAMP NOT NULL,
  `Table Name` STRING NOT NULL,
  `Data Load Completed` TIMESTAMP,
  `Was Successful` BOOLEAN NOT NULL,
  `Source System Cutoff Time` TIMESTAMP NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`movement_staging` (
  `Movement Staging Key` BIGINT NOT NULL,
  `Date Key` DATE,
  `Stock Item Key` INT,
  `Customer Key` INT,
  `Supplier Key` INT,
  `Transaction Type Key` INT,
  `WWI Stock Item Transaction ID` INT,
  `WWI Invoice ID` INT,
  `WWI Purchase Order ID` INT,
  `Quantity` INT,
  `WWI Stock Item ID` INT,
  `WWI Customer ID` INT,
  `WWI Supplier ID` INT,
  `WWI Transaction Type ID` INT,
  `Last Modifed When` TIMESTAMP
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`order_staging` (
  `Order Staging Key` BIGINT NOT NULL,
  `City Key` INT,
  `Customer Key` INT,
  `Stock Item Key` INT,
  `Order Date Key` DATE,
  `Picked Date Key` DATE,
  `Salesperson Key` INT,
  `Picker Key` INT,
  `WWI Order ID` INT,
  `WWI Backorder ID` INT,
  `Description` STRING,
  `Package` STRING,
  `Quantity` INT,
  `Unit Price` DECIMAL(18,2),
  `Tax Rate` DECIMAL(18,3),
  `Total Excluding Tax` DECIMAL(18,2),
  `Tax Amount` DECIMAL(18,2),
  `Total Including Tax` DECIMAL(18,2),
  `Lineage Key` INT,
  `WWI City ID` INT,
  `WWI Customer ID` INT,
  `WWI Stock Item ID` INT,
  `WWI Salesperson ID` INT,
  `WWI Picker ID` INT,
  `Last Modified When` TIMESTAMP
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`paymentmethod_staging` (
  `Payment Method Staging Key` INT NOT NULL,
  `WWI Payment Method ID` INT NOT NULL,
  `Payment Method` STRING NOT NULL,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`purchase_staging` (
  `Purchase Staging Key` BIGINT NOT NULL,
  `Date Key` DATE,
  `Supplier Key` INT,
  `Stock Item Key` INT,
  `WWI Purchase Order ID` INT,
  `Ordered Outers` INT,
  `Ordered Quantity` INT,
  `Received Outers` INT,
  `Package` STRING,
  `Is Order Finalized` BOOLEAN,
  `WWI Supplier ID` INT,
  `WWI Stock Item ID` INT,
  `Last Modified When` TIMESTAMP
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`sale_staging` (
  `Sale Staging Key` BIGINT NOT NULL,
  `City Key` INT,
  `Customer Key` INT,
  `Bill To Customer Key` INT,
  `Stock Item Key` INT,
  `Invoice Date Key` DATE,
  `Delivery Date Key` DATE,
  `Salesperson Key` INT,
  `WWI Invoice ID` INT,
  `Description` STRING,
  `Package` STRING,
  `Quantity` INT,
  `Unit Price` DECIMAL(18,2),
  `Tax Rate` DECIMAL(18,3),
  `Total Excluding Tax` DECIMAL(18,2),
  `Tax Amount` DECIMAL(18,2),
  `Profit` DECIMAL(18,2),
  `Total Including Tax` DECIMAL(18,2),
  `Total Dry Items` INT,
  `Total Chiller Items` INT,
  `WWI City ID` INT,
  `WWI Customer ID` INT,
  `WWI Bill To Customer ID` INT,
  `WWI Stock Item ID` INT,
  `WWI Salesperson ID` INT,
  `Last Modified When` TIMESTAMP
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`stockholding_staging` (
  `Stock Holding Staging Key` BIGINT NOT NULL,
  `Stock Item Key` INT,
  `Quantity On Hand` INT,
  `Bin Location` STRING,
  `Last Stocktake Quantity` INT,
  `Last Cost Price` DECIMAL(18,2),
  `Reorder Level` INT,
  `Target Stock Level` INT,
  `WWI Stock Item ID` INT
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`stockitem_staging` (
  `Stock Item Staging Key` INT NOT NULL,
  `WWI Stock Item ID` INT NOT NULL,
  `Stock Item` STRING NOT NULL,
  `Color` STRING NOT NULL,
  `Selling Package` STRING NOT NULL,
  `Buying Package` STRING NOT NULL,
  `Brand` STRING NOT NULL,
  `Size` STRING NOT NULL,
  `Lead Time Days` INT NOT NULL,
  `Quantity Per Outer` INT NOT NULL,
  `Is Chiller Stock` BOOLEAN NOT NULL,
  `Barcode` STRING,
  `Tax Rate` DECIMAL(18,3) NOT NULL,
  `Unit Price` DECIMAL(18,2) NOT NULL,
  `Recommended Retail Price` DECIMAL(18,2),
  `Typical Weight Per Unit` DECIMAL(18,3) NOT NULL,
  `Photo` BINARY,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`supplier_staging` (
  `Supplier Staging Key` INT NOT NULL,
  `WWI Supplier ID` INT NOT NULL,
  `Supplier` STRING NOT NULL,
  `Category` STRING NOT NULL,
  `Primary Contact` STRING NOT NULL,
  `Supplier Reference` STRING,
  `Payment Days` INT NOT NULL,
  `Postal Code` STRING NOT NULL,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`transaction_staging` (
  `Transaction Staging Key` BIGINT NOT NULL,
  `Date Key` DATE,
  `Customer Key` INT,
  `Bill To Customer Key` INT,
  `Supplier Key` INT,
  `Transaction Type Key` INT,
  `Payment Method Key` INT,
  `WWI Customer Transaction ID` INT,
  `WWI Supplier Transaction ID` INT,
  `WWI Invoice ID` INT,
  `WWI Purchase Order ID` INT,
  `Supplier Invoice Number` STRING,
  `Total Excluding Tax` DECIMAL(18,2),
  `Tax Amount` DECIMAL(18,2),
  `Total Including Tax` DECIMAL(18,2),
  `Outstanding Balance` DECIMAL(18,2),
  `Is Finalized` BOOLEAN,
  `WWI Customer ID` INT,
  `WWI Bill To Customer ID` INT,
  `WWI Supplier ID` INT,
  `WWI Transaction Type ID` INT,
  `WWI Payment Method ID` INT,
  `Last Modified When` TIMESTAMP
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');

CREATE TABLE IF NOT EXISTS `dev_catalog`.`integration`.`transactiontype_staging` (
  `Transaction Type Staging Key` INT NOT NULL,
  `WWI Transaction Type ID` INT NOT NULL,
  `Transaction Type` STRING NOT NULL,
  `Valid From` TIMESTAMP NOT NULL,
  `Valid To` TIMESTAMP NOT NULL
)
USING DELTA
TBLPROPERTIES ('delta.columnMapping.mode' = 'name');
