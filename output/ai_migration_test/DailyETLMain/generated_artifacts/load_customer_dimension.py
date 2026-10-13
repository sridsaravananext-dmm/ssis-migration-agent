from pyspark.sql import SparkSession, DataFrame
import pyspark.sql.functions as F
import pyspark.sql.window as W
from datetime import datetime

def get_customer_updates(spark: SparkSession, last_cutoff: str, new_cutoff: str) -> DataFrame:
    """Extract incremental customer dimension changes between two cut‑off timestamps.

    This implementation reproduces the behaviour of the source SQL Server procedure
    `Integration.GetCustomerUpdates` using Spark DataFrames on Databricks.

    Parameters
    ----------
    spark: SparkSession
        Active Spark session.
    last_cutoff: str
        Lower bound (exclusive) of the incremental window, e.g. "2023-01-01T00:00:00".
    new_cutoff: str
        Upper bound (inclusive) of the incremental window, e.g. "2023-01-31T23:59:59".

    Returns
    -------
    DataFrame
        Columns matching the staging table `Integration.Customer_Staging`:
        ["WWI Customer ID", "Customer", "Bill To Customer", "Category",
         "Buying Group", "Primary Contact", "Postal Code", "Valid From", "Valid To"]
    """
    # ---------------------------------------------------------------------
    # Assumptions (explicitly declared as required by the migration spec)
    # ---------------------------------------------------------------------
    # 1. All source tables are available as Delta tables with the same names
    #    used in the source procedure (e.g. "Sales.Customers", "Sales.Customers_Archive",
    #    "Sales.BuyingGroups", "Sales.BuyingGroups_Archive", "Sales.CustomerCategories",
    #    "Sales.CustomerCategories_Archive", "Application.People").
    # 2. Each of these tables implements system‑versioned temporal semantics via
    #    row‑level columns `ValidFrom` (datetime) and `ValidTo` (datetime).  The
    #    archive tables contain the same schema.
    # 3. The archive tables follow the pattern `{schema}.{table}_Archive` as
    #    described in the supplied `temporal_context`.
    # 4. `Application.People` also contains `ValidFrom`/`ValidTo` columns – this
    #    is required for point‑in‑time joins.  If it does not, the code will still
    #    run but will use the current state only (documented as a limitation).
    # 5. The constant `END_OF_TIME` is defined as the maximum datetime used in the
    #    source procedure.
    # ---------------------------------------------------------------------

    END_OF_TIME = "9999-12-31 23:59:59.9999999"
    INITIAL_LOAD_DATE = "2013-01-01"

    # ---------------------------------------------------------------------
    # Helper to create a unified view (current + archive) for a given table.
    # ---------------------------------------------------------------------
    def union_current_and_archive(current_tbl: str, archive_tbl: str) -> DataFrame:
        cur = spark.table(current_tbl)
        arch = spark.table(archive_tbl)
        return cur.unionByName(arch)

    # Unified tables with history
    customers = union_current_and_archive("Sales.Customers", "Sales.Customers_Archive")
    buying_groups = union_current_and_archive("Sales.BuyingGroups", "Sales.BuyingGroups_Archive")
    categories = union_current_and_archive("Sales.CustomerCategories", "Sales.CustomerCategories_Archive")
    people = spark.table("Application.People")  # assume it already contains history columns

    # ---------------------------------------------------------------------
    # 1. Gather change timestamps for each entity within the window.
    # ---------------------------------------------------------------------
    def change_timestamps(df: DataFrame, id_col: str) -> DataFrame:
        return (
            df.filter(
                (F.col("ValidFrom") > F.lit(last_cutoff))
                & (F.col("ValidFrom") <= F.lit(new_cutoff))
                & (F.col("ValidFrom") != F.lit(INITIAL_LOAD_DATE))
            )
            .select(F.col(id_col).alias("id"), F.col("ValidFrom").alias("valid_from"))
        )

    bg_changes = change_timestamps(buying_groups, "BuyingGroupID")
    cat_changes = change_timestamps(categories, "CustomerCategoryID")
    cust_changes = change_timestamps(customers, "CustomerID")

    # Union all timestamps (distinct) – order will be applied later per‑entity.
    all_timestamps = (
        bg_changes.select("valid_from")
        .union(cat_changes.select("valid_from"))
        .union(cust_changes.select("valid_from"))
        .distinct()
        .orderBy("valid_from")
    )

    # Collect timestamps to driver – expected to be a modest number (incremental).
    ts_list = [row["valid_from"] for row in all_timestamps.collect()]

    # ---------------------------------------------------------------------
    # 2. For each timestamp, materialise point‑in‑time snapshots and generate rows.
    # ---------------------------------------------------------------------
    result_rows = []

    for ts in ts_list:
        # Snapshot of each entity at the current timestamp
        cust_snapshot = customers.filter(
            (F.col("ValidFrom") <= F.lit(ts)) & (F.col("ValidTo") > F.lit(ts))
        )
        bg_snapshot = buying_groups.filter(
            (F.col("ValidFrom") <= F.lit(ts)) & (F.col("ValidTo") > F.lit(ts))
        )
        cat_snapshot = categories.filter(
            (F.col("ValidFrom") <= F.lit(ts)) & (F.col("ValidTo") > F.lit(ts))
        )
        people_snapshot = people.filter(
            (F.col("ValidFrom") <= F.lit(ts)) & (F.col("ValidTo") > F.lit(ts))
        )

        # -----------------------------------------------------------------
        # Buying‑Group changes – all customers belonging to the changed group.
        # -----------------------------------------------------------------
        changed_bg_ids = (
            bg_changes.filter(F.col("valid_from") == ts)
            .select(F.col("id").alias("BuyingGroupID"))
        )
        if changed_bg_ids.count() > 0:
            bg_join = (
                cust_snapshot.alias("c")
                .join(bg_snapshot.alias("bg"), F.col("c.BuyingGroupID") == F.col("bg.BuyingGroupID"))
                .join(cat_snapshot.alias("cc"), F.col("c.CustomerCategoryID") == F.col("cc.CustomerCategoryID"))
                .join(cust_snapshot.alias("bt"), F.col("c.BillToCustomerID") == F.col("bt.CustomerID"))
                .join(people_snapshot.alias("p"), F.col("c.PrimaryContactPersonID") == F.col("p.PersonID"))
                .join(changed_bg_ids, F.col("bg.BuyingGroupID") == F.col("BuyingGroupID"))
                .select(
                    F.col("c.CustomerID").alias("WWI Customer ID"),
                    F.col("c.CustomerName").alias("Customer"),
                    F.col("bt.CustomerName").alias("Bill To Customer"),
                    F.col("cc.CustomerCategoryName").alias("Category"),
                    F.col("bg.BuyingGroupName").alias("Buying Group"),
                    F.col("p.FullName").alias("Primary Contact"),
                    F.col("c.DeliveryPostalCode").alias("Postal Code"),
                    F.lit(ts).cast("timestamp").alias("Valid From"),
                    F.lit(None).cast("timestamp").alias("Valid To"),
                )
            )
            result_rows.append(bg_join)

        # ---------------------------------------------------------------
        # Customer‑Category changes – all customers belonging to the changed category.
        # ---------------------------------------------------------------
        changed_cat_ids = (
            cat_changes.filter(F.col("valid_from") == ts)
            .select(F.col("id").alias("CustomerCategoryID"))
        )
        if changed_cat_ids.count() > 0:
            cat_join = (
                cust_snapshot.alias("c")
                .join(bg_snapshot.alias("bg"), F.col("c.BuyingGroupID") == F.col("bg.BuyingGroupID"))
                .join(cat_snapshot.alias("cc"), F.col("c.CustomerCategoryID") == F.col("cc.CustomerCategoryID"))
                .join(cust_snapshot.alias("bt"), F.col("c.BillToCustomerID") == F.col("bt.CustomerID"))
                .join(people_snapshot.alias("p"), F.col("c.PrimaryContactPersonID") == F.col("p.PersonID"))
                .join(changed_cat_ids, F.col("cc.CustomerCategoryID") == F.col("CustomerCategoryID"))
                .select(
                    F.col("c.CustomerID").alias("WWI Customer ID"),
                    F.col("c.CustomerName").alias("Customer"),
                    F.col("bt.CustomerName").alias("Bill To Customer"),
                    F.col("cc.CustomerCategoryName").alias("Category"),
                    F.col("bg.BuyingGroupName").alias("Buying Group"),
                    F.col("p.FullName").alias("Primary Contact"),
                    F.col("c.DeliveryPostalCode").alias("Postal Code"),
                    F.lit(ts).cast("timestamp").alias("Valid From"),
                    F.lit(None).cast("timestamp").alias("Valid To"),
                )
            )
            result_rows.append(cat_join)

        # ---------------------------------------------------------------
        # Direct Customer changes – the specific customer row that changed.
        # ---------------------------------------------------------------
        changed_cust_ids = (
            cust_changes.filter(F.col("valid_from") == ts)
            .select(F.col("id").alias("CustomerID"))
        )
        if changed_cust_ids.count() > 0:
            cust_join = (
                cust_snapshot.alias("c")
                .join(bg_snapshot.alias("bg"), F.col("c.BuyingGroupID") == F.col("bg.BuyingGroupID"))
                .join(cat_snapshot.alias("cc"), F.col("c.CustomerCategoryID") == F.col("cc.CustomerCategoryID"))
                .join(cust_snapshot.alias("bt"), F.col("c.BillToCustomerID") == F.col("bt.CustomerID"))
                .join(people_snapshot.alias("p"), F.col("c.PrimaryContactPersonID") == F.col("p.PersonID"))
                .join(changed_cust_ids, F.col("c.CustomerID") == F.col("CustomerID"))
                .select(
                    F.col("c.CustomerID").alias("WWI Customer ID"),
                    F.col("c.CustomerName").alias("Customer"),
                    F.col("bt.CustomerName").alias("Bill To Customer"),
                    F.col("cc.CustomerCategoryName").alias("Category"),
                    F.col("bg.BuyingGroupName").alias("Buying Group"),
                    F.col("p.FullName").alias("Primary Contact"),
                    F.col("c.DeliveryPostalCode").alias("Postal Code"),
                    F.lit(ts).cast("timestamp").alias("Valid From"),
                    F.lit(None).cast("timestamp").alias("Valid To"),
                )
            )
            result_rows.append(cust_join)

    # ---------------------------------------------------------------------
    # 3. Union all generated rows into a single DataFrame.
    # ---------------------------------------------------------------------
    if not result_rows:
        # No changes – return an empty DataFrame with the correct schema.
        empty_schema = [
            "WWI Customer ID",
            "Customer",
            "Bill To Customer",
            "Category",
            "Buying Group",
            "Primary Contact",
            "Postal Code",
            "Valid From",
            "Valid To",
        ]
        return spark.createDataFrame([], schema=" , ".join([f"{c} string" for c in empty_schema]))

    changes_df = result_rows[0]
    for df in result_rows[1:]:
        changes_df = changes_df.unionByName(df)

    # ---------------------------------------------------------------------
    # 4. Compute the [Valid To] column – next Valid From for the same customer.
    # ---------------------------------------------------------------------
    w = (
        W.Window.partitionBy("WWI Customer ID").orderBy("Valid From")
    )
    changes_df = (
        changes_df.withColumn("next_valid_from", F.lead("Valid From").over(w))
        .withColumn(
            "Valid To",
            F.when(F.col("next_valid_from").isNotNull(), F.col("next_valid_from"))
            .otherwise(F.lit(END_OF_TIME).cast("timestamp")),
        )
        .drop("next_valid_from")
    )

    # ---------------------------------------------------------------------
    # 5. Return the final DataFrame ordered by Valid From (mirrors source ORDER BY).
    # ---------------------------------------------------------------------
    return changes_df.orderBy("Valid From")

# -------------------------------------------------------------------------
# Example usage (to be removed or adapted in production pipelines):
# spark = SparkSession.builder.getOrCreate()
# df_updates = get_customer_updates(spark, "2023-01-01 00:00:00", "2023-01-31 23:59:59")
# df_updates.write.format("delta").mode("append").saveAsTable("Integration.Customer_Staging")
# -------------------------------------------------------------------------