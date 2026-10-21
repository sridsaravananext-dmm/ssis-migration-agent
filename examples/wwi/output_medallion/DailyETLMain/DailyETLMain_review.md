# Migration Review: DailyETLMain

- **Catalog:** dev_catalog
- **Notebook:** `/Workspace/Users/sridsaravanan.ext@deloitte.com/ssis-migration-agent/examples/wwi/output_medallion/DailyETLMain/DailyETLMain_Migration.ipynb`

## Package Summary

- Executables: 94
- Data flows: 13
- Execute SQL tasks: 53
- Precedence constraints: 80
- Variables: 4

## Execute SQL Task Classification

| Classification | Count |
| --- | ---: |
| cutoff_time | 13 |
| lineage_key | 13 |
| migrate_staged | 13 |
| populate_dimension | 1 |
| truncate_staging | 13 |

*All procedures resolved.*

## Control Flow

- Execution edges: 80

## Data Flows

| Flow | Components | Mappings |
| --- | ---: | ---: |
| Extract Updated City Data to Staging | 2 | 12 |
| Extract Updated Customer Data to Staging | 2 | 9 |
| Extract Updated Employee Data to Staging | 2 | 7 |
| Extract Updated Movement Data to Staging | 2 | 10 |
| Extract Updated Order Data to Staging | 2 | 18 |
| Extract Updated Payment Method Data to Staging | 2 | 4 |
| Extract Updated Purchase Data to Staging | 2 | 10 |
| Extract Updated Sale Data to Staging | 2 | 20 |
| Extract All Stock Holding Data to Staging | 2 | 7 |
| Extract Updated Stock Item Data to Staging | 2 | 18 |
| Extract Updated Supplier Data to Staging | 2 | 9 |
| Extract Updated Transaction Data to Staging | 2 | 17 |
| Extract Updated Transaction Type Data to Staging | 2 | 4 |

### Column Lineage Validation

#### Extract Updated City Data to Staging

- **Source:** Integration_GetCityUpdates
- **Destination:** Integration_City_Staging
- **Column mappings:** 12

| Source Column | Destination Column | Type |
| --- | --- | --- |
| WWI City ID | WWI City ID | DIRECT |
| City | City | DIRECT |
| State Province | State Province | DIRECT |
| Country | Country | DIRECT |
| Continent | Continent | DIRECT |
| Sales Territory | Sales Territory | DIRECT |
| Region | Region | DIRECT |
| Subregion | Subregion | DIRECT |
| Location | Location | DIRECT |
| Latest Recorded Population | Latest Recorded Population | DIRECT |
| Valid From | Valid From | DIRECT |
| Valid To | Valid To | DIRECT |


#### Extract Updated Customer Data to Staging

- **Source:** Integration_GetCustomerUpdates
- **Destination:** Integration_Customer_Staging
- **Column mappings:** 9

| Source Column | Destination Column | Type |
| --- | --- | --- |
| Valid From | Valid From | DIRECT |
| Valid To | Valid To | DIRECT |
| WWI Customer ID | WWI Customer ID | DIRECT |
| Customer | Customer | DIRECT |
| Bill To Customer | Bill To Customer | DIRECT |
| Category | Category | DIRECT |
| Buying Group | Buying Group | DIRECT |
| Primary Contact | Primary Contact | DIRECT |
| Postal Code | Postal Code | DIRECT |


#### Extract Updated Employee Data to Staging

- **Source:** Integration_GetEmployeeUpdates
- **Destination:** Integration_Employee_Staging
- **Column mappings:** 7

| Source Column | Destination Column | Type |
| --- | --- | --- |
| Valid From | Valid From | DIRECT |
| Valid To | Valid To | DIRECT |
| WWI Employee ID | WWI Employee ID | DIRECT |
| Employee | Employee | DIRECT |
| Preferred Name | Preferred Name | DIRECT |
| Is Salesperson | Is Salesperson | DIRECT |
| Photo | Photo | DIRECT |


#### Extract Updated Movement Data to Staging

- **Source:** Integration_GetMovementUpdates
- **Destination:** Integration_Movement_Staging
- **Column mappings:** 10

| Source Column | Destination Column | Type |
| --- | --- | --- |
| WWI Transaction Type ID | WWI Transaction Type ID | DIRECT |
| Date Key | Date Key | DIRECT |
| WWI Stock Item Transaction ID | WWI Stock Item Transaction ID | DIRECT |
| WWI Invoice ID | WWI Invoice ID | DIRECT |
| WWI Purchase Order ID | WWI Purchase Order ID | DIRECT |
| Quantity | Quantity | DIRECT |
| WWI Stock Item ID | WWI Stock Item ID | DIRECT |
| WWI Customer ID | WWI Customer ID | DIRECT |
| WWI Supplier ID | WWI Supplier ID | DIRECT |
| Last Modified When | Last Modified When | DIRECT |


#### Extract Updated Order Data to Staging

- **Source:** Integration_GetOrderUpdates
- **Destination:** Integration_Order_Staging
- **Column mappings:** 18

| Source Column | Destination Column | Type |
| --- | --- | --- |
| Quantity | Quantity | DIRECT |
| WWI Stock Item ID | WWI Stock Item ID | DIRECT |
| WWI Customer ID | WWI Customer ID | DIRECT |
| Order Date Key | Order Date Key | DIRECT |
| Picked Date Key | Picked Date Key | DIRECT |
| WWI Order ID | WWI Order ID | DIRECT |
| WWI Backorder ID | WWI Backorder ID | DIRECT |
| Description | Description | DIRECT |
| Package | Package | DIRECT |
| Unit Price | Unit Price | DIRECT |
| Tax Rate | Tax Rate | DIRECT |
| Total Excluding Tax | Total Excluding Tax | DIRECT |
| Tax Amount | Tax Amount | DIRECT |
| Total Including Tax | Total Including Tax | DIRECT |
| WWI City ID | WWI City ID | DIRECT |
| WWI Salesperson ID | WWI Salesperson ID | DIRECT |
| WWI Picker ID | WWI Picker ID | DIRECT |
| Last Modified When | Last Modified When | DIRECT |


#### Extract Updated Payment Method Data to Staging

- **Source:** Integration_GetPaymentMethodUpdates
- **Destination:** Integration_PaymentMethod_Staging
- **Column mappings:** 4

| Source Column | Destination Column | Type |
| --- | --- | --- |
| Valid From | Valid From | DIRECT |
| Valid To | Valid To | DIRECT |
| WWI Payment Method ID | WWI Payment Method ID | DIRECT |
| Payment Method | Payment Method | DIRECT |


#### Extract Updated Purchase Data to Staging

- **Source:** Integration_GetPurchaseUpdates
- **Destination:** Integration_Purchase_Staging
- **Column mappings:** 10

| Source Column | Destination Column | Type |
| --- | --- | --- |
| WWI Stock Item ID | WWI Stock Item ID | DIRECT |
| Package | Package | DIRECT |
| Last Modified When | Last Modified When | DIRECT |
| Date Key | Date Key | DIRECT |
| WWI Purchase Order ID | WWI Purchase Order ID | DIRECT |
| Ordered Outers | Ordered Outers | DIRECT |
| Ordered Quantity | Ordered Quantity | DIRECT |
| Received Outers | Received Outers | DIRECT |
| Is Order Line Finalized | Is Order Line Finalized | DIRECT |
| WWI Supplier ID | WWI Supplier ID | DIRECT |


#### Extract Updated Sale Data to Staging

- **Source:** Integration_GetSaleUpdates
- **Destination:** Integration_Sale_Staging
- **Column mappings:** 20

| Source Column | Destination Column | Type |
| --- | --- | --- |
| WWI Stock Item ID | WWI Stock Item ID | DIRECT |
| Package | Package | DIRECT |
| Last Modified When | Last Modified When | DIRECT |
| Invoice Date Key | Invoice Date Key | DIRECT |
| Delivery Date Key | Delivery Date Key | DIRECT |
| WWI Invoice ID | WWI Invoice ID | DIRECT |
| Description | Description | DIRECT |
| Quantity | Quantity | DIRECT |
| Unit Price | Unit Price | DIRECT |
| Tax Rate | Tax Rate | DIRECT |
| Total Excluding Tax | Total Excluding Tax | DIRECT |
| Tax Amount | Tax Amount | DIRECT |
| Profit | Profit | DIRECT |
| Total Including Tax | Total Including Tax | DIRECT |
| Total Dry Items | Total Dry Items | DIRECT |
| Total Chiller Items | Total Chiller Items | DIRECT |
| WWI City ID | WWI City ID | DIRECT |
| WWI Customer ID | WWI Customer ID | DIRECT |
| WWI Bill To Customer ID | WWI Bill To Customer ID | DIRECT |
| WWI Salesperson ID | WWI Salesperson ID | DIRECT |


#### Extract All Stock Holding Data to Staging

- **Source:** Integration_GetStockHoldingUpdates
- **Destination:** Integration_StockHolding_Staging
- **Column mappings:** 7

| Source Column | Destination Column | Type |
| --- | --- | --- |
| WWI Stock Item ID | WWI Stock Item ID | DIRECT |
| Quantity On Hand | Quantity On Hand | DIRECT |
| Bin Location | Bin Location | DIRECT |
| Last Stocktake Quantity | Last Stocktake Quantity | DIRECT |
| Last Cost Price | Last Cost Price | DIRECT |
| Reorder Level | Reorder Level | DIRECT |
| Target Stock Level | Target Stock Level | DIRECT |


#### Extract Updated Stock Item Data to Staging

- **Source:** Integration_GetStockItemUpdates
- **Destination:** Integration_StockItem_Staging
- **Column mappings:** 18

| Source Column | Destination Column | Type |
| --- | --- | --- |
| Valid From | Valid From | DIRECT |
| Valid To | Valid To | DIRECT |
| WWI Stock Item ID | WWI Stock Item ID | DIRECT |
| Stock Item | Stock Item | DIRECT |
| Color | Color | DIRECT |
| Selling Package | Selling Package | DIRECT |
| Buying Package | Buying Package | DIRECT |
| Brand | Brand | DIRECT |
| Size | Size | DIRECT |
| Lead Time Days | Lead Time Days | DIRECT |
| Quantity Per Outer | Quantity Per Outer | DIRECT |
| Is Chiller Stock | Is Chiller Stock | DIRECT |
| Barcode | Barcode | DIRECT |
| Tax Rate | Tax Rate | DIRECT |
| Unit Price | Unit Price | DIRECT |
| Recommended Retail Price | Recommended Retail Price | DIRECT |
| Typical Weight Per Unit | Typical Weight Per Unit | DIRECT |
| Photo | Photo | DIRECT |


#### Extract Updated Supplier Data to Staging

- **Source:** Integration_GetSupplierUpdates
- **Destination:** Integration_Supplier_Staging
- **Column mappings:** 9

| Source Column | Destination Column | Type |
| --- | --- | --- |
| Valid From | Valid From | DIRECT |
| Valid To | Valid To | DIRECT |
| WWI Supplier ID | WWI Supplier ID | DIRECT |
| Supplier | Supplier | DIRECT |
| Category | Category | DIRECT |
| Primary Contact | Primary Contact | DIRECT |
| Supplier Reference | Supplier Reference | DIRECT |
| Payment Days | Payment Days | DIRECT |
| Postal Code | Postal Code | DIRECT |


#### Extract Updated Transaction Data to Staging

- **Source:** Integration_GetTransactionUpdates
- **Destination:** Integration_Transaction_Staging
- **Column mappings:** 17

| Source Column | Destination Column | Type |
| --- | --- | --- |
| Last Modified When | Last Modified When | DIRECT |
| WWI Invoice ID | WWI Invoice ID | DIRECT |
| Total Excluding Tax | Total Excluding Tax | DIRECT |
| Tax Amount | Tax Amount | DIRECT |
| Total Including Tax | Total Including Tax | DIRECT |
| WWI Customer ID | WWI Customer ID | DIRECT |
| WWI Bill To Customer ID | WWI Bill To Customer ID | DIRECT |
| Date Key | Date Key | DIRECT |
| WWI Customer Transaction ID | WWI Customer Transaction ID | DIRECT |
| WWI Supplier Transaction ID | WWI Supplier Transaction ID | DIRECT |
| WWI Purchase Order ID | WWI Purchase Order ID | DIRECT |
| Supplier Invoice Number | Supplier Invoice Number | DIRECT |
| Outstanding Balance | Outstanding Balance | DIRECT |
| Is Finalized | Is Finalized | DIRECT |
| WWI Supplier ID | WWI Supplier ID | DIRECT |
| WWI Transaction Type ID | WWI Transaction Type ID | DIRECT |
| WWI Payment Method ID | WWI Payment Method ID | DIRECT |


#### Extract Updated Transaction Type Data to Staging

- **Source:** Integration_GetTransactionTypeUpdates
- **Destination:** Integration_TransactionType_Staging
- **Column mappings:** 4

| Source Column | Destination Column | Type |
| --- | --- | --- |
| Valid From | Valid From | DIRECT |
| Valid To | Valid To | DIRECT |
| WWI Transaction Type ID | WWI Transaction Type ID | DIRECT |
| Transaction Type | Transaction Type | DIRECT |


## AI Utilization Summary

- **Total steps:** 13
- **Deterministic:** 5 (38.5%)
- **AI-Assisted:** 8 (61.5%)
- **Review required:** 0 (0.0%)

**Deterministic steps:** Load Movement Fact, Load Order Fact, Load Purchase Fact, Load Sale Fact, Load Stock Holding Fact

**AI-Assisted steps:** Load City Dimension, Load Customer Dimension, Load Employee Dimension, Load Payment Method Dimension, Load Stock Item Dimension, Load Supplier Dimension, Load Transaction Fact, Load Transaction Type Dimension

### Operation-Level Breakdown

- **Total operations generated:** 120
- **Deterministic operations:** 120 (100.0%) — control flow, staging, lineage, cutoff
- **AI-required operations:** 0 (0.0%) — extract & migrate SQL

## Migration Plan Steps

| Step | Strategy | Language | Source Objects | Target Objects | Status |
| --- | --- | --- | --- | --- | --- |
| Load City Dimension | AI_ASSISTED | ? | Integration.GetCityUpdates | Integration.City_Staging | 🤖 AI needed |
| Load Customer Dimension | AI_ASSISTED | ? | Integration.GetCustomerUpdates | Integration.Customer_Staging | 🤖 AI needed |
| Load Employee Dimension | AI_ASSISTED | ? | Integration.GetEmployeeUpdates | Integration.Employee_Staging | 🤖 AI needed |
| Load Movement Fact | DETERMINISTIC | ? | Integration.GetMovementUpdates | Integration.Movement_Staging | ✅ Ready |
| Load Order Fact | DETERMINISTIC | ? | Integration.GetOrderUpdates | Integration.Order_Staging | ✅ Ready |
| Load Payment Method Dimension | AI_ASSISTED | ? | Integration.GetPaymentMethodUpdates | Integration.PaymentMethod_Staging | 🤖 AI needed |
| Load Purchase Fact | DETERMINISTIC | ? | Integration.GetPurchaseUpdates | Integration.Purchase_Staging | ✅ Ready |
| Load Sale Fact | DETERMINISTIC | ? | Integration.GetSaleUpdates | Integration.Sale_Staging | ✅ Ready |
| Load Stock Holding Fact | DETERMINISTIC | ? | Integration.GetStockHoldingUpdates | Integration.StockHolding_Staging | ✅ Ready |
| Load Stock Item Dimension | AI_ASSISTED | ? | Integration.GetStockItemUpdates | Integration.StockItem_Staging | 🤖 AI needed |
| Load Supplier Dimension | AI_ASSISTED | ? | Integration.GetSupplierUpdates | Integration.Supplier_Staging | 🤖 AI needed |
| Load Transaction Fact | AI_ASSISTED | ? | Integration.GetTransactionUpdates | Integration.Transaction_Staging | 🤖 AI needed |
| Load Transaction Type Dimension | AI_ASSISTED | ? | Integration.GetTransactionTypeUpdates | Integration.TransactionType_Staging | 🤖 AI needed |

## Validation Recommendations

### Static Validation
- Review report generated with 13 steps
- 5 steps have deterministic code (control flow + SQL)
- 8 steps require AI generation or manual review
- Supply stored procedure definitions to enable AI generation

### Runtime Validation
- Execute the generated notebook on Databricks
- Compare row counts between source and target tables
- Verify column types match the original schema
- Check execution order matches the original SSIS control flow

## Component Types

- OLE_DB_DESTINATION: 13
- OLE_DB_SOURCE: 13

---

*Generated by ssis-migration-agent*