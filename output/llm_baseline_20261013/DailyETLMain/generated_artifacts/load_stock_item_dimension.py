import pyspark.sql.functions as F
import pyspark.sql.window as W
from datetime import datetime

END_OF_TIME = "9999-12-31 23:59:59.9999999"

def _snapshot_at_timestamp(df_current, df_archive, timestamp_col, valid_from_col="ValidFrom", valid_to_col="ValidTo"):
    """Return a DataFrame representing the union of the current and archive tables filtered to rows valid at the supplied timestamp.
    
    Parameters
    ----------
    df_current: DataFrame
        The live (current) table.
    df_archive: DataFrame
        The archive table (historical rows).
    timestamp_col: Column (or literal) containing the point‑in‑time timestamp.
    valid_from_col: Name of the column that stores the start of validity.
    valid_to_col: Name of the column that stores the end of validity.
    """
    # Union current and archive rows
    df_union = df_current.unionByName(df_archive)
    # Keep rows where timestamp is within the validity window
    return df_union.filter(
        (F.col(valid_from_col) <= timestamp_col) & (F.col(valid_to_col) > timestamp_col)
    )

def load_stock_item_dimension(spark, last_cutoff: datetime, new_cutoff: datetime):
    """Extract changed StockItem rows between two cut‑off timestamps and write them to the staging table.
    
    This function reproduces the behaviour of the source stored procedure
    `Integration.GetStockItemUpdates` using Spark DataFrames on Databricks.
    """
    # ---------------------------------------------------------------------
    # 1. Identify StockItem change points (StockItemID, ValidFrom) within the window
    # ---------------------------------------------------------------------
    stock_items_cur = spark.table("Warehouse.StockItems")
    stock_items_arc = spark.table("Warehouse.StockItems_Archive")

    changes_cur = stock_items_cur.filter(
        (F.col("ValidFrom") > F.lit(last_cutoff)) & (F.col("ValidFrom") <= F.lit(new_cutoff))
    ).select(F.col("StockItemID").alias("WWI_Stock_Item_ID"), F.col("ValidFrom"))

    changes_arc = stock_items_arc.filter(
        (F.col("ValidFrom") > F.lit(last_cutoff)) & (F.col("ValidFrom") <= F.lit(new_cutoff))
    ).select(F.col("StockItemID").alias("WWI_Stock_Item_ID"), F.col("ValidFrom"))

    # UNION ALL and order by ValidFrom
    change_points = changes_cur.unionByName(changes_arc).orderBy("ValidFrom")

    # Collect change points to driver for procedural iteration (preserves original cursor/loop semantics)
    change_list = change_points.select("WWI_Stock_Item_ID", "ValidFrom").collect()

    # ---------------------------------------------------------------------
    # 2. For each change point, materialise the snapshot of related tables at that timestamp
    # ---------------------------------------------------------------------
    rows = []
    for row in change_list:
        stock_item_id = row["WWI_Stock_Item_ID"]
        valid_from_ts = row["ValidFrom"]
        ts_lit = F.lit(valid_from_ts)

        # Snapshot of StockItems at @ValidFrom
        stock_item_snapshot = _snapshot_at_timestamp(
            spark.table("Warehouse.StockItems"),
            spark.table("Warehouse.StockItems_Archive"),
            ts_lit
        ).filter(F.col("StockItemID") == stock_item_id)

        # Snapshot of PackageTypes (unit) at @ValidFrom
        unit_pkg_snapshot = _snapshot_at_timestamp(
            spark.table("Warehouse.PackageTypes"),
            spark.table("Warehouse.PackageTypes_Archive"),
            ts_lit
        )

        # Snapshot of PackageTypes (outer) at @ValidFrom
        outer_pkg_snapshot = _snapshot_at_timestamp(
            spark.table("Warehouse.PackageTypes"),
            spark.table("Warehouse.PackageTypes_Archive"),
            ts_lit
        )

        # Snapshot of Colors at @ValidFrom (LEFT OUTER – may be null)
        colors_snapshot = _snapshot_at_timestamp(
            spark.table("Warehouse.Colors"),
            spark.table("Warehouse.Colors_Archive"),
            ts_lit
        )

        # Join the snapshots to produce the required columns
        joined = (
            stock_item_snapshot.alias("si")
            .join(
                unit_pkg_snapshot.alias("spt"),
                F.col("si.UnitPackageID") == F.col("spt.PackageTypeID"),
                "inner",
            )
            .join(
                outer_pkg_snapshot.alias("bpt"),
                F.col("si.OuterPackageID") == F.col("bpt.PackageTypeID"),
                "inner",
            )
            .join(
                colors_snapshot.alias("c"),
                F.col("si.ColorID") == F.col("c.ColorID"),
                "left",
            )
            .select(
                F.col("si.StockItemID").alias("WWI Stock Item ID"),
                F.col("si.StockItemName").alias("Stock Item"),
                F.col("c.ColorName").alias("Color"),
                F.col("spt.PackageTypeName").alias("Selling Package"),
                F.col("bpt.PackageTypeName").alias("Buying Package"),
                F.col("si.Brand").alias("Brand"),
                F.col("si.Size").alias("Size"),
                F.col("si.LeadTimeDays").alias("Lead Time Days"),
                F.col("si.QuantityPerOuter").alias("Quantity Per Outer"),
                F.col("si.IsChillerStock").alias("Is Chiller Stock"),
                F.col("si.Barcode").alias("Barcode"),
                F.col("si.LeadTimeDays").alias("Tax Rate"),  # NOTE: original proc uses LeadTimeDays for Tax Rate – preserving semantics
                F.col("si.UnitPrice").alias("Unit Price"),
                F.col("si.RecommendedRetailPrice").alias("Recommended Retail Price"),
                F.col("si.TypicalWeightPerUnit").alias("Typical Weight Per Unit"),
                F.col("si.Photo").alias("Photo"),
                F.col("si.ValidFrom").alias("Valid From"),
                F.col("si.ValidTo").alias("Valid To"),
            )
        )

        # Collect the single row (the source proc expects exactly one row per change point)
        for result_row in joined.collect():
            rows.append(result_row.asDict())

    # ---------------------------------------------------------------------
    # 3. Build a DataFrame from the collected rows (equivalent to #StockItemChanges temp table)
    # ---------------------------------------------------------------------
    if not rows:
        # No changes – create an empty DataFrame with the correct schema
        schema = "[WWI Stock Item ID] INT, [Stock Item] STRING, Color STRING, [Selling Package] STRING, " \
                 "[Buying Package] STRING, Brand STRING, Size STRING, [Lead Time Days] INT, " \
                 "[Quantity Per Outer] INT, [Is Chiller Stock] BOOLEAN, Barcode STRING, " \
                 "[Tax Rate] DECIMAL(18,3), [Unit Price] DECIMAL(18,2), " \
                 "[Recommended Retail Price] DECIMAL(18,2), [Typical Weight Per Unit] DECIMAL(18,3), " \
                 "Photo BINARY, [Valid From] TIMESTAMP, [Valid To] TIMESTAMP"
        stock_changes_df = spark.createDataFrame([], schema)
    else:
        stock_changes_df = spark.createDataFrame(rows)

    # ---------------------------------------------------------------------
    # 4. Compute the correct [Valid To] for each row (next Valid From per WWI Stock Item ID)
    # ---------------------------------------------------------------------
    w = W.Window.partitionBy("WWI Stock Item ID").orderBy("Valid From")
    stock_changes_df = stock_changes_df.withColumn(
        "NextValidFrom", F.lead("Valid From").over(w)
    ).withColumn(
        "Valid To",
        F.coalesce(F.col("NextValidFrom"), F.lit(END_OF_TIME).cast("timestamp"))
    ).drop("NextValidFrom")

    # ---------------------------------------------------------------------
    # 5. Write the result to the staging table Integration.StockItem_Staging
    # ---------------------------------------------------------------------
    # Assuming the staging table already exists as a Delta table.
    stock_changes_df.write.mode("append").format("delta").saveAsTable("Integration.StockItem_Staging")

    # The original procedure returns 0 on success – we mimic that with a Python return value.
    return 0

# Example usage (to be removed or adapted in production):
# from datetime import datetime
# spark = SparkSession.builder.getOrCreate()
# load_stock_item_dimension(spark, datetime(2023, 1, 1), datetime(2023, 1, 31))
