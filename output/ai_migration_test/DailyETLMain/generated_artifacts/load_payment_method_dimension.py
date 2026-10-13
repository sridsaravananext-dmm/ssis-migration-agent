# Databricks notebook – Load Payment Method Dimension
# ---------------------------------------------------
# This script implements the logical migration step "load_payment_method_dimension".
# It reproduces the behaviour of the source stored procedure Integration.GetPaymentMethodUpdates
# using Spark DataFrames and Delta Lake operations, and then runs the destination logic
# Integration.MigrateStagedPaymentMethodData.
# ---------------------------------------------------

from pyspark.sql import SparkSession, Window
from pyspark.sql.functions import col, lit, when, min as sql_min, max as sql_max, lead, coalesce, current_timestamp
from delta.tables import DeltaTable

spark = SparkSession.builder.getOrCreate()

# ---------------------------------------------------------------------------
# Configuration / constants (assumptions are documented at the end of the file)
# ---------------------------------------------------------------------------
END_OF_TIME = "9999-12-31 23:59:59.9999999"

# ---------------------------------------------------------------------------
# 1. Extract updated payment method data to staging (equivalent to the source
#    procedure Integration.GetPaymentMethodUpdates)
# ---------------------------------------------------------------------------

def get_payment_method_updates(last_cutoff: str, new_cutoff: str):
    """Extracts payment‑method changes between two cut‑off timestamps and writes
    the result to the staging table Integration.PaymentMethod_Staging.

    Parameters
    ----------
    last_cutoff: str
        ISO‑8601 timestamp representing the previous ETL watermark.
    new_cutoff: str
        ISO‑8601 timestamp representing the current ETL watermark.
    """
    # Load current and archive tables (assumed to be Delta tables)
    current_df = spark.table("Application.PaymentMethods")
    archive_df = spark.table("Application.PaymentMethods_Archive")

    # Union current and archive – both contain the validity columns ValidFrom/ValidTo
    all_versions = current_df.select(
        col("PaymentMethodID"),
        col("PaymentMethodName"),
        col("ValidFrom"),
        col("ValidTo")
    ).unionByName(
        archive_df.select(
            col("PaymentMethodID"),
            col("PaymentMethodName"),
            col("ValidFrom"),
            col("ValidTo")
        )
    )

    # -------------------------------------------------------------------
    # Step A – Identify change points (equivalent to the cursor SELECT … UNION ALL)
    # -------------------------------------------------------------------
    change_points = all_versions.filter(
        (col("ValidFrom") > lit(last_cutoff)) & (col("ValidFrom") <= lit(new_cutoff))
    ).select(
        col("PaymentMethodID"), col("ValidFrom")
    ).orderBy(col("ValidFrom"))

    # -------------------------------------------------------------------
    # Step B – For each change point, fetch the row version that was valid at that timestamp.
    # This replaces the procedural loop that used FOR SYSTEM_TIME AS OF.
    # -------------------------------------------------------------------
    # Join change points to the versioned table using the validity window.
    joined = change_points.alias("cp").join(
        all_versions.alias("av"),
        (col("cp.PaymentMethodID") == col("av.PaymentMethodID")) &
        (col("cp.ValidFrom") >= col("av.ValidFrom")) &
        (col("cp.ValidFrom") < col("av.ValidTo")),
        how="inner"
    ).select(
        col("av.PaymentMethodID").alias("WWI Payment Method ID"),
        col("av.PaymentMethodName").alias("Payment Method"),
        col("av.ValidFrom").alias("Valid From"),
        col("av.ValidTo").alias("Valid To")
    )

    # -------------------------------------------------------------------
    # Step C – Re‑calculate the Valid To column so that it reflects the next
    # change point for the same payment‑method ID, or END_OF_TIME if none.
    # -------------------------------------------------------------------
    w = Window.partitionBy(col("WWI Payment Method ID")).orderBy(col("Valid From"))
    final_df = joined.withColumn(
        "Calculated Valid To",
        coalesce(lead(col("Valid From")).over(w), lit(END_OF_TIME))
    ).drop("Valid To").withColumnRenamed("Calculated Valid To", "Valid To")

    # -------------------------------------------------------------------
    # Step D – Write the result to the staging table (append for incremental loads).
    # -------------------------------------------------------------------
    final_df.write.format("delta").mode("append").saveAsTable("Integration.PaymentMethod_Staging")

    return final_df

# ---------------------------------------------------------------------------
# 2. Migrate staged data into the dimension table (equivalent to
#    Integration.MigrateStagedPaymentMethodData)
# ---------------------------------------------------------------------------

def migrate_staged_payment_method_data():
    """Closes off existing open‑ended dimension rows, inserts the newly staged rows,
    and updates lineage and ETL‑cutoff tables.
    """
    # -------------------------------------------------------------------
    # Retrieve the current lineage key for this load (the row with a NULL
    # Data Load Completed flag, ordered by descending key).
    # -------------------------------------------------------------------
    lineage_key_row = spark.sql(
        """
        SELECT TOP 1 [Lineage Key]
        FROM Integration.Lineage
        WHERE [Table Name] = 'Payment Method' AND [Data Load Completed] IS NULL
        ORDER BY [Lineage Key] DESC
        """
    ).collect()
    if not lineage_key_row:
        raise RuntimeError("No pending lineage record found for 'Payment Method'.")
    lineage_key = lineage_key_row[0][0]

    # -------------------------------------------------------------------
    # Compute the earliest Valid From per WWI Payment Method ID in the staging table.
    # -------------------------------------------------------------------
    rows_to_close = spark.table("Integration.PaymentMethod_Staging") \
        .groupBy(col("WWI Payment Method ID")) \
        .agg(sql_min(col("Valid From")).alias("Valid From"))

    # -------------------------------------------------------------------
    # Update existing dimension rows that are still open (Valid To = END_OF_TIME)
    # to close them at the new Valid From.
    # -------------------------------------------------------------------
    dim_table = DeltaTable.forName(spark, "Dimension.Payment Method")
    dim_table.alias("dim").merge(
        rows_to_close.alias("rtc"),
        "dim.[WWI Payment Method ID] = rtc.[WWI Payment Method ID] AND dim.[Valid To] = '{}'".format(END_OF_TIME)
    ).whenMatchedUpdate(set={"dim.[Valid To]": col("rtc.[Valid From]")}).execute()

    # -------------------------------------------------------------------
    # Insert the staged rows into the dimension, attaching the lineage key.
    # -------------------------------------------------------------------
    staged_df = spark.table("Integration.PaymentMethod_Staging") \
        .withColumn("Lineage Key", lit(lineage_key))
    staged_df.write.format("delta").mode("append").saveAsTable("Dimension.Payment Method")

    # -------------------------------------------------------------------
    # Mark the lineage record as completed and successful.
    # -------------------------------------------------------------------
    spark.sql(
        f"""
        UPDATE Integration.Lineage
        SET [Data Load Completed] = CURRENT_TIMESTAMP(),
            [Was Successful] = 1
        WHERE [Lineage Key] = {lineage_key}
        """
    )

    # -------------------------------------------------------------------
    # Advance the ETL cutoff for the Payment Method table.
    # -------------------------------------------------------------------
    spark.sql(
        f"""
        UPDATE Integration.[ETL Cutoff]
        SET [Cutoff Time] = (
            SELECT [Source System Cutoff Time]
            FROM Integration.Lineage
            WHERE [Lineage Key] = {lineage_key}
        )
        WHERE [Table Name] = 'Payment Method'
        """
    )

# ---------------------------------------------------------------------------
# 3. Orchestrate the full step – this would be called by the workflow engine.
# ---------------------------------------------------------------------------

def run_load_payment_method_dimension(last_cutoff: str, new_cutoff: str):
    """Executes the full load step: extract → stage → migrate.
    """
    # Extract changes to staging
    get_payment_method_updates(last_cutoff, new_cutoff)
    # Migrate staged data into the dimension and update lineage / cutoff
    migrate_staged_payment_method_data()

# ---------------------------------------------------------------------------
# Example usage (the workflow would supply the actual cut‑off values):
# ---------------------------------------------------------------------------
# run_load_payment_method_dimension('2023-01-01T00:00:00', '2023-01-31T23:59:59')

# ---------------------------------------------------------------------------
# Assumptions
# ---------------------------------------------------------------------------
# - All source and destination tables are Delta tables accessible via Spark SQL.
# - The archive table follows the pattern Application.PaymentMethods_Archive and contains
#   the same schema (PaymentMethodID, PaymentMethodName, ValidFrom, ValidTo).
# - ValidFrom and ValidTo columns are of type timestamp and follow the semantics described
#   in the temporal_context (row is valid when timestamp >= ValidFrom AND timestamp < ValidTo).
# - The staging table Integration.PaymentMethod_Staging already exists with the columns:
#   [WWI Payment Method ID], [Payment Method], [Valid From], [Valid To].
# - The dimension table Dimension.Payment Method is a Delta table with the same columns plus
#   a [Lineage Key] column.
# - Integration.Lineage contains columns [Lineage Key] (int), [Table Name] (string),
#   [Data Load Completed] (timestamp, nullable), [Was Successful] (int), and
#   [Source System Cutoff Time] (timestamp).
# - Integration.[ETL Cutoff] contains columns [Table Name] (string) and [Cutoff Time] (timestamp).
# - The workflow supplies ISO‑8601 strings for the cut‑off parameters.
# - DeltaTable.update/merge semantics are used to emulate the UPDATE statements.
# ---------------------------------------------------------------------------
# Validation requirements
# ---------------------------------------------------------------------------
# - Verify that the number of rows written to Integration.PaymentMethod_Staging matches the
#   row count produced by the original stored procedure for the same cut‑off values.
# - After migration, ensure that for each WWI Payment Method ID the validity periods in
#   Dimension.Payment Method are continuous (no gaps) and non‑overlapping.
# - Confirm that the [Lineage Key] recorded in the inserted dimension rows matches the
#   key retrieved from Integration.Lineage.
# - Check that Integration.Lineage.[Data Load Completed] is populated and Integration.Lineage.[Was Successful] = 1.
# - Validate that Integration.[ETL Cutoff].[Cutoff Time] equals the source system cutoff time
#   from the processed lineage record.
# ---------------------------------------------------------------------------
