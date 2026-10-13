import pyspark.sql.functions as F
from pyspark.sql import Window, SparkSession

# ---------------------------------------------------------------------------
# Load Payment Method Dimension
# ---------------------------------------------------------------------------
# This function implements the full logical step "load_payment_method_dimension"
#   * Extracts incremental payment‑method changes between two cut‑off timestamps
#   * Resolves the point‑in‑time view using row‑level ValidFrom/ValidTo columns
#   * Computes the correct ValidTo for each change (next ValidFrom or EndOfTime)
#   * Writes the result to the staging table Integration.PaymentMethod_Staging
#   * Merges the staged rows into the target dimension table, closes off prior
#     rows, updates lineage and ETL cutoff information.
# ---------------------------------------------------------------------------

def load_payment_method_dimension(last_cutoff: str, new_cutoff: str, spark: SparkSession = None):
    """Execute the Load Payment Method Dimension step.

    Parameters
    ----------
    last_cutoff: str
        ISO‑8601 timestamp representing the previous successful ETL watermark.
    new_cutoff: str
        ISO‑8601 timestamp representing the current ETL watermark.
    spark: SparkSession, optional
        Existing Spark session; if omitted, ``SparkSession.builder.getOrCreate()`` is used.
    """
    if spark is None:
        spark = SparkSession.builder.getOrCreate()

    # ---------------------------------------------------------------------
    # 1. Constants & helper tables
    # ---------------------------------------------------------------------
    END_OF_TIME = "9999-12-31 23:59:59.9999999"

    # Source tables (assumed to be Delta tables with system‑versioned columns)
    CURRENT_TABLE = "Application.PaymentMethods"
    ARCHIVE_TABLE = "Application.PaymentMethods_Archive"

    # ---------------------------------------------------------------------
    # 2. Build the change list (equivalent to the cursor in the original proc)
    # ---------------------------------------------------------------------
    change_list_sql = f"""
        SELECT PaymentMethodID, ValidFrom
        FROM {ARCHIVE_TABLE}
        WHERE ValidFrom > TIMESTAMP('{last_cutoff}')
          AND ValidFrom <= TIMESTAMP('{new_cutoff}')
        UNION ALL
        SELECT PaymentMethodID, ValidFrom
        FROM {CURRENT_TABLE}
        WHERE ValidFrom > TIMESTAMP('{last_cutoff}')
          AND ValidFrom <= TIMESTAMP('{new_cutoff}')
        ORDER BY ValidFrom
    """
    change_df = spark.sql(change_list_sql)

    # ---------------------------------------------------------------------
    # 3. Resolve the point‑in‑time view for each change using row‑level validity
    #    (FOR SYSTEM_TIME AS OF @ValidFrom  =>  ValidFrom <= @ValidFrom < ValidTo)
    # ---------------------------------------------------------------------
    # Union of current and archive tables provides the full history.
    history_sql = f"""
        SELECT PaymentMethodID,
               PaymentMethodName,
               ValidFrom,
               ValidTo
        FROM {CURRENT_TABLE}
        UNION ALL
        SELECT PaymentMethodID,
               PaymentMethodName,
               ValidFrom,
               ValidTo
        FROM {ARCHIVE_TABLE}
    """
    history_df = spark.sql(history_sql)

    # Join change list with history on ID and timestamp window
    as_of_df = (
        change_df.alias("cl")
        .join(
            history_df.alias("h"),
            (F.col("cl.PaymentMethodID") == F.col("h.PaymentMethodID"))
            & (F.col("cl.ValidFrom") >= F.col("h.ValidFrom"))
            & (F.col("cl.ValidFrom") < F.col("h.ValidTo")),
            "inner",
        )
        .select(
            F.col("h.PaymentMethodID").alias("WWI Payment Method ID"),
            F.col("h.PaymentMethodName").alias("Payment Method"),
            F.col("h.ValidFrom").alias("Valid From"),
            F.col("h.ValidTo").alias("Valid To"),
        )
    )

    # ---------------------------------------------------------------------
    # 4. Re‑compute Valid To based on the next Valid From for the same ID
    # ---------------------------------------------------------------------
    w = Window.partitionBy("WWI Payment Method ID").orderBy("Valid From")
    staged_df = (
        as_of_df
        .withColumn(
            "Valid To",
            F.when(
                F.lead("Valid From").over(w).isNotNull(),
                F.lead("Valid From").over(w)
            ).otherwise(F.lit(END_OF_TIME))
        )
    )

    # ---------------------------------------------------------------------
    # 5. Write to staging table (append – the surrounding workflow will clear it
    #    before the next run)
    # ---------------------------------------------------------------------
    staged_df.write.format("delta").mode("append").saveAsTable("Integration.PaymentMethod_Staging")

    # ---------------------------------------------------------------------
    # 6. Migrate staged data into the dimension table (logic from
    #    Integration.MigrateStagedPaymentMethodData)
    # ---------------------------------------------------------------------
    # 6.1 Retrieve the current lineage key for this load
    lineage_key_sql = """
        SELECT LineageKey
        FROM Integration.Lineage
        WHERE `Table Name` = 'payment_method' AND `Data Load Completed` IS NULL
        ORDER BY LineageKey DESC
        LIMIT 1
    """
    lineage_key_row = spark.sql(lineage_key_sql).collect()
    if not lineage_key_row:
        raise RuntimeError("No pending lineage record found for 'payment_method'.")
    lineage_key = lineage_key_row[0]["LineageKey"]

    # 6.2 Close off existing rows in the dimension that are still open (Valid To = EndOfTime)
    rows_to_close_sql = """
        SELECT `WWI Payment Method ID`, MIN(`Valid From`) AS `Valid From`
        FROM Integration.PaymentMethod_Staging
        GROUP BY `WWI Payment Method ID`
    """
    rows_to_close_df = spark.sql(rows_to_close_sql)
    rows_to_close_df.createOrReplaceTempView("RowsToCloseOff")

    close_off_sql = f"""
        UPDATE Dimension.payment_method AS pm
        SET pm.`Valid To` = r.`Valid From`
        FROM RowsToCloseOff r
        WHERE pm.`WWI Payment Method ID` = r.`WWI Payment Method ID`
          AND pm.`Valid To` = TIMESTAMP('{END_OF_TIME}')
    """
    spark.sql(close_off_sql)

    # 6.3 Insert new rows from staging
    insert_sql = f"""
        INSERT INTO Dimension.payment_method (
            `WWI Payment Method ID`, `Payment Method`, `Valid From`, `Valid To`, `Lineage Key`
        )
        SELECT `WWI Payment Method ID`, `Payment Method`, `Valid From`, `Valid To`, {lineage_key}
        FROM Integration.PaymentMethod_Staging
    """
    spark.sql(insert_sql)

    # 6.4 Mark lineage as completed
    update_lineage_sql = f"""
        UPDATE Integration.Lineage
        SET `Data Load Completed` = CURRENT_TIMESTAMP(),
            `Was Successful` = 1
        WHERE LineageKey = {lineage_key}
    """
    spark.sql(update_lineage_sql)

    # 6.5 Advance ETL cutoff for this table
    update_cutoff_sql = f"""
        UPDATE Integration.`ETL Cutoff`
        SET `Cutoff Time` = (
            SELECT `Source System Cutoff Time`
            FROM Integration.Lineage
            WHERE LineageKey = {lineage_key}
        )
        WHERE `Table Name` = 'payment_method'
    """
    spark.sql(update_cutoff_sql)

    # ---------------------------------------------------------------------
    # 7. End of step – callers can now clear the staging table if required.
    # ---------------------------------------------------------------------
    return {
        "staged_rows": staged_df.count(),
        "lineage_key": lineage_key
    }

# Example usage (uncomment for real runs):
# result = load_payment_method_dimension('2023-01-01T00:00:00', '2023-01-31T23:59:59')
# print(result)
