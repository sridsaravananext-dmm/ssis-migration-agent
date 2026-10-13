# Databricks (PySpark) implementation of Integration.GetSupplierUpdates
# This code reproduces the logic of the source stored procedure and writes the result
# to the staging table Integration.Supplier_Staging.
# It assumes that the source tables are available as Delta tables with the same
# schema as in the source system and that temporal history is stored using the
# ValidFrom / ValidTo columns together with archive tables following the pattern
#   <schema>.<table>_Archive.
# The People table is assumed to be non‑temporal (only the current version is used).

from pyspark.sql import SparkSession, functions as F, Window

spark = SparkSession.builder.getOrCreate()

# ---------------------------------------------------------------------------
# Parameters / constants (these would normally be supplied by the orchestration layer)
# ---------------------------------------------------------------------------
# Retrieve the last successful cutoff for the Supplier dimension
# (Integration.ETL Cutoff table is expected to have columns: Table Name, Cutoff Time)
last_cutoff_row = spark.table("Integration.ETL_Cutoff") \
    .filter(F.col("Table Name") == "Supplier") \
    .select(F.col("Cutoff Time").alias("last_cutoff")) \
    .orderBy(F.col("last_cutoff").desc()) \
    .limit(1) \
    .collect()

if not last_cutoff_row:
    raise RuntimeError("Unable to determine last ETL cutoff for Supplier dimension.")

last_cutoff = last_cutoff_row[0]["last_cutoff"]
# New cutoff is the current timestamp at execution time
new_cutoff = F.current_timestamp()

END_OF_TIME = "9999-12-31 23:59:59.9999999"
INITIAL_LOAD_DATE = "2013-01-01"

# ---------------------------------------------------------------------------
# Helper to obtain a point‑in‑time snapshot of a temporal table
# ---------------------------------------------------------------------------
def snapshot(df, ts_col):
    """Return rows that are valid at the timestamp given by ts_col (Column)."""
    return df.filter((F.col("ValidFrom") <= ts_col) & (F.col("ValidTo") > ts_col))

# ---------------------------------------------------------------------------
# Load source tables (current and archive) – they must contain ValidFrom/ValidTo
# ---------------------------------------------------------------------------
suppliers_cur = spark.table("Purchasing.Suppliers")
suppliers_arc = spark.table("Purchasing.Suppliers_Archive")
categories_cur = spark.table("Purchasing.SupplierCategories")
categories_arc = spark.table("Purchasing.SupplierCategories_Archive")
people = spark.table("Application.People")  # assumed non‑temporal

# Union current and archive to obtain the full temporal history
suppliers_hist = suppliers_cur.unionByName(suppliers_arc)
categories_hist = categories_cur.unionByName(categories_arc)

# ---------------------------------------------------------------------------
# 1) Supplier Category changes (the first cursor in the original proc)
# ---------------------------------------------------------------------------
cat_changes = categories_hist.filter(
    (F.col("ValidFrom") > F.lit(last_cutoff)) &
    (F.col("ValidFrom") <= F.lit(new_cutoff)) &
    (F.col("ValidFrom") != F.lit(INITIAL_LOAD_DATE))
).select(
    F.col("SupplierCategoryID").alias("cat_id"),
    F.col("ValidFrom").alias("change_ts")
).orderBy("change_ts")

# Join suppliers and categories as they were at each change timestamp
cat_change_rows = cat_changes.alias("cc") \
    .join(
        snapshot(suppliers_hist, F.col("cc.change_ts")).alias("s"),
        F.col("s.SupplierCategoryID") == F.col("cc.cat_id"),
        "inner"
    ) \
    .join(
        snapshot(categories_hist, F.col("cc.change_ts")).alias("sc"),
        (F.col("sc.SupplierCategoryID") == F.col("cc.cat_id")) &
        (F.col("sc.ValidFrom") <= F.col("cc.change_ts")) &
        (F.col("sc.ValidTo") > F.col("cc.change_ts")),
        "inner"
    ) \
    .join(
        people.alias("p"),
        F.col("s.PrimaryContactPersonID") == F.col("p.PersonID"),
        "inner"
    ) \
    .select(
        F.col("s.SupplierID").alias("WWI Supplier ID"),
        F.col("s.SupplierName").alias("Supplier"),
        F.col("sc.SupplierCategoryName").alias("Category"),
        F.col("p.FullName").alias("Primary Contact"),
        F.col("s.SupplierReference").alias("Supplier Reference"),
        F.col("s.PaymentDays").alias("Payment Days"),
        F.col("s.DeliveryPostalCode").alias("Postal Code"),
        F.col("cc.change_ts").alias("Valid From"),
        F.col("s.ValidTo").alias("Valid To")
    )

# ---------------------------------------------------------------------------
# 2) Supplier changes (the second cursor in the original proc)
# ---------------------------------------------------------------------------
supplier_changes = suppliers_hist.filter(
    (F.col("ValidFrom") > F.lit(last_cutoff)) &
    (F.col("ValidFrom") <= F.lit(new_cutoff))
).select(
    F.col("SupplierID").alias("sup_id"),
    F.col("ValidFrom").alias("change_ts")
).orderBy("change_ts")

supplier_change_rows = supplier_changes.alias("scu") \
    .join(
        snapshot(suppliers_hist, F.col("scu.change_ts")).alias("s"),
        F.col("s.SupplierID") == F.col("scu.sup_id"),
        "inner"
    ) \
    .join(
        snapshot(categories_hist, F.col("scu.change_ts")).alias("sc"),
        F.col("s.SupplierCategoryID") == F.col("sc.SupplierCategoryID"),
        "inner"
    ) \
    .join(
        people.alias("p"),
        F.col("s.PrimaryContactPersonID") == F.col("p.PersonID"),
        "inner"
    ) \
    .select(
        F.col("s.SupplierID").alias("WWI Supplier ID"),
        F.col("s.SupplierName").alias("Supplier"),
        F.col("sc.SupplierCategoryName").alias("Category"),
        F.col("p.FullName").alias("Primary Contact"),
        F.col("s.SupplierReference").alias("Supplier Reference"),
        F.col("s.PaymentDays").alias("Payment Days"),
        F.col("s.DeliveryPostalCode").alias("Postal Code"),
        F.col("scu.change_ts").alias("Valid From"),
        F.col("s.ValidTo").alias("Valid To")
    )

# ---------------------------------------------------------------------------
# Combine both result sets
# ---------------------------------------------------------------------------
combined = cat_change_rows.unionByName(supplier_change_rows)

# ---------------------------------------------------------------------------
# Compute the correct [Valid To] based on the next [Valid From] per supplier
# ---------------------------------------------------------------------------
w = Window.partitionBy("WWI Supplier ID").orderBy("Valid From")
combined_with_to = combined.withColumn(
    "next_valid_from",
    F.lead("Valid From").over(w)
).withColumn(
    "Valid To",
    F.coalesce(F.col("next_valid_from"), F.lit(END_OF_TIME))
).drop("next_valid_from")

# ---------------------------------------------------------------------------
# Write the result to the staging table (append mode)
# ---------------------------------------------------------------------------
combined_with_to.write.format("delta").mode("append").saveAsTable("Integration.Supplier_Staging")

# ---------------------------------------------------------------------------
# End of migration step
# ---------------------------------------------------------------------------
