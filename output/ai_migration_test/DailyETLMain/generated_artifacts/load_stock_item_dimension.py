from pyspark.sql import SparkSession, functions as F, Window
from delta.tables import DeltaTable

spark = SparkSession.builder.getOrCreate()

# ---------------------------------------------------------------------------
# Configuration / constants
# ---------------------------------------------------------------------------
END_OF_TIME_STR = "9999-12-31 23:59:59.9999999"
END_OF_TIME = F.lit(END_OF_TIME_STR).cast("timestamp")
TABLE_NAME = "Stock Item"

# ---------------------------------------------------------------------------
# 1. Get the last ETL cutoff for the Stock Item dimension
# ---------------------------------------------------------------------------
etl_cutoff_df = (
    spark.table("Integration.ETL_Cutoff")
    .filter(F.col("Table Name") == TABLE_NAME)
    .select(F.col("Cutoff Time"))
)
last_cutoff_row = etl_cutoff_df.first()
if last_cutoff_row is None or last_cutoff_row["Cutoff Time"] is None:
    # If no prior cutoff exists, start from a very early date
    last_cutoff = F.lit("1900-01-01 00:00:00").cast("timestamp")
else:
    last_cutoff = F.lit(last_cutoff_row["Cutoff Time"]).cast("timestamp")

# ---------------------------------------------------------------------------
# 2. Determine the new cutoff (use the current timestamp as a pragmatic default)
# ---------------------------------------------------------------------------
new_cutoff = spark.sql("SELECT current_timestamp() as ts").first()["ts"]

# ---------------------------------------------------------------------------
# 3. Identify StockItem IDs that changed between the two cut‑offs
# ---------------------------------------------------------------------------
def changes_from(table_name: str):
    return (
        spark.table(table_name)
        .filter((F.col("ValidFrom") > last_cutoff) & (F.col("ValidFrom") <= new_cutoff))
        .select(
            F.col("StockItemID").alias("WWI Stock Item ID"),
            F.col("ValidFrom").alias("Valid From")
        )
    )

changes_archive = changes_from("Warehouse.StockItems_Archive")
changes_current = changes_from("Warehouse.StockItems")
changes = (
    changes_archive.unionByName(changes_current)
    .orderBy("Valid From")
)

# ---------------------------------------------------------------------------
# 4. Build a historical view of the source tables using row‑level validity windows
#    (FOR SYSTEM_TIME AS OF @ts  ⇢  ValidFrom <= @ts < ValidTo)
# ---------------------------------------------------------------------------
stock_items_hist = (
    spark.table("Warehouse.StockItems")
    .unionByName(spark.table("Warehouse.StockItems_Archive"))
    .select(*[F.col(c) for c in spark.table("Warehouse.StockItems").columns])
)
package_types_hist = (
    spark.table("Warehouse.PackageTypes")
    .unionByName(spark.table("Warehouse.PackageTypes_Archive"))
    .select(*[F.col(c) for c in spark.table("Warehouse.PackageTypes").columns])
)
colors_hist = (
    spark.table("Warehouse.Colors")
    .unionByName(spark.table("Warehouse.Colors_Archive"))
    .select(*[F.col(c) for c in spark.table("Warehouse.Colors").columns])
)

# ---------------------------------------------------------------------------
# 5. Join the change list with the historical snapshots to produce the staging rows
# ---------------------------------------------------------------------------
changes_alias = changes.alias("c")
si = stock_items_hist.alias("si")
spt = package_types_hist.alias("spt")
bpt = package_types_hist.alias("bpt")
col = colors_hist.alias("col")

staging_df = (
    changes_alias
    .join(
        si,
        (si.StockItemID == changes_alias["WWI Stock Item ID"])
        & (si.ValidFrom <= changes_alias["Valid From"])
        & (si.ValidTo > changes_alias["Valid From"]),
        "inner",
    )
    .join(
        spt,
        (si.UnitPackageID == spt.PackageTypeID)
        & (spt.ValidFrom <= changes_alias["Valid From"])
        & (spt.ValidTo > changes_alias["Valid From"]),
        "inner",
    )
    .join(
        bpt,
        (si.OuterPackageID == bpt.PackageTypeID)
        & (bpt.ValidFrom <= changes_alias["Valid From"])
        & (bpt.ValidTo > changes_alias["Valid From"]),
        "inner",
    )
    .join(
        col,
        (si.ColorID == col.ColorID)
        & (col.ValidFrom <= changes_alias["Valid From"])
        & (col.ValidTo > changes_alias["Valid From"]),
        "left",
    )
    .select(
        si.StockItemID.alias("WWI Stock Item ID"),
        si.StockItemName.alias("Stock Item"),
        col.ColorName.alias("Color"),
        spt.PackageTypeName.alias("Selling Package"),
        bpt.PackageTypeName.alias("Buying Package"),
        si.Brand,
        si.Size,
        si.LeadTimeDays.alias("Lead Time Days"),
        si.QuantityPerOuter.alias("Quantity Per Outer"),
        si.IsChillerStock.alias("Is Chiller Stock"),
        si.Barcode,
        # NOTE: The original procedure maps LeadTimeDays to Tax Rate – we preserve that mapping
        si.LeadTimeDays.alias("Tax Rate"),
        si.UnitPrice,
        si.RecommendedRetailPrice,
        si.TypicalWeightPerUnit,
        si.Photo,
        si.ValidFrom,
        si.ValidTo,
    )
)

# ---------------------------------------------------------------------------
# 6. Write the result to the staging table (overwrite for idempotency)
# ---------------------------------------------------------------------------
staging_df.write.format("delta").mode("overwrite").saveAsTable("Integration.StockItem_Staging")

# ---------------------------------------------------------------------------
# 7. Migration from staging to the dimension (close‑off + insert)
# ---------------------------------------------------------------------------
# 7a. Retrieve the pending lineage key
lineage_key_row = (
    spark.table("Integration.Lineage")
    .filter((F.col("Table Name") == TABLE_NAME) & (F.col("Data Load Completed").isNull()))
    .orderBy(F.col("Lineage Key").desc())
    .limit(1)
    .select("Lineage Key")
    .first()
)
if lineage_key_row is None:
    raise RuntimeError("No pending lineage record found for table 'Stock Item'.")
lineage_key = lineage_key_row["Lineage Key"]

# 7b. Determine the earliest Valid From per WWI Stock Item ID in the staging data (RowsToCloseOff)
rows_to_close = (
    spark.table("Integration.StockItem_Staging")
    .groupBy("WWI Stock Item ID")
    .agg(F.min("Valid From").alias("Valid From"))
)

# 7c. Close off existing dimension rows (set Valid To = new Valid From where currently open)
#    Using Delta MERGE for an in‑place update

dim_delta = DeltaTable.forName(spark, "Dimension.Stock Item")

# Build a temporary view that contains the rows to be updated with the new Valid To value
rows_to_update = (
    dim_delta.toDF().alias("d")
    .join(rows_to_close.alias("r"), dim_delta.toDF()["WWI Stock Item ID"] == rows_to_close["WWI Stock Item ID"], "inner")
    .filter(dim_delta.toDF()["Valid To"] == END_OF_TIME_STR)
    .select(
        "d.*",
        rows_to_close["Valid From"].alias("new_valid_to")
    )
)

# Perform the update via MERGE (match on primary key and open‑ended rows)
(dim_delta.alias("t")
 .merge(
     rows_to_update.alias("s"),
     "t.[WWI Stock Item ID] = s.[WWI Stock Item ID] AND t.[Valid To] = '{}'".format(END_OF_TIME_STR)
 )
 .whenMatchedUpdate(set={"Valid To": "s.new_valid_to"})
 .execute())

# 7d. Insert the new rows from staging, attaching the lineage key
staging_with_lineage = (
    spark.table("Integration.StockItem_Staging")
    .withColumn("Lineage Key", F.lit(lineage_key))
)

(dim_delta.alias("t")
 .merge(
     staging_with_lineage.alias("s"),
     "false"  # force insert‑only path
 )
 .whenNotMatchedInsertAll()
 .execute())

# ---------------------------------------------------------------------------
# 8. Mark the lineage record as completed
# ---------------------------------------------------------------------------
lineage_delta = DeltaTable.forName(spark, "Integration.Lineage")
lineage_delta.update(
    condition = F.col("Lineage Key") == lineage_key,
    set = {
        "Data Load Completed": F.current_timestamp(),
        "Was Successful": F.lit(1)
    }
)

# ---------------------------------------------------------------------------
# 9. Advance the ETL cutoff to the source‑system cutoff stored in the lineage record
# ---------------------------------------------------------------------------
source_cutoff = (
    spark.table("Integration.Lineage")
    .filter(F.col("Lineage Key") == lineage_key)
    .select(F.col("Source System Cutoff Time").alias("src_cutoff"))
    .first()["src_cutoff"]
)

etl_delta = DeltaTable.forName(spark, "Integration.ETL_Cutoff")
etl_delta.update(
    condition = F.col("Table Name") == TABLE_NAME,
    set = {"Cutoff Time": F.lit(source_cutoff)}
)

# ---------------------------------------------------------------------------
# End of migration step
# ---------------------------------------------------------------------------
print("Load Stock Item Dimension step completed successfully.")