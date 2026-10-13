import datetime
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

def get_transaction_type_updates(spark: SparkSession, last_cutoff: datetime.datetime, new_cutoff: datetime.datetime):
    """Replicates the logic of Integration.GetTransactionTypeUpdates.

    Parameters
    ----------
    spark: SparkSession
        Active Spark session.
    last_cutoff: datetime
        The previous ETL watermark.
    new_cutoff: datetime
        The current ETL watermark (typically the time the job runs).

    Returns
    -------
    DataFrame
        Columns: `WWI Transaction Type ID`, `Transaction Type`, `Valid From`, `Valid To`
    """
    # Load current and archive tables.  The context guarantees they contain
    # TransactionTypeID, TransactionTypeName, ValidFrom, ValidTo.
    df_current = spark.table("Application.TransactionTypes")
    df_archive = spark.table("Application.TransactionTypes_Archive")

    # Union current and archive to emulate system‑time view.
    df_all = df_current.unionByName(df_archive)

    # Identify rows whose ValidFrom falls within the incremental window.
    change_df = (
        df_all
        .select("TransactionTypeID", "ValidFrom")
        .where((F.col("ValidFrom") > F.lit(last_cutoff)) & (F.col("ValidFrom") <= F.lit(new_cutoff)))
        .distinct()
        .orderBy("ValidFrom")
    )

    # For each change timestamp retrieve the row version that was active at that time.
    # This is equivalent to the FOR SYSTEM_TIME AS OF @ValidFrom query.
    # Condition: ValidFrom <= @ValidFrom < ValidTo
    changes = change_df.alias("c")
    all_rows = df_all.alias("a")
    result = (
        changes.join(
            all_rows,
            (F.col("c.TransactionTypeID") == F.col("a.TransactionTypeID")) &
            (F.col("c.ValidFrom") >= F.col("a.ValidFrom")) &
            (F.col("c.ValidFrom") < F.col("a.ValidTo")),
            "inner"
        )
        .select(
            F.col("a.TransactionTypeID").alias("WWI Transaction Type ID"),
            F.col("a.TransactionTypeName").alias("Transaction Type"),
            F.col("c.ValidFrom").alias("Valid From"),
            F.col("a.ValidTo").alias("Valid To")
        )
    )

    # Compute the correct Valid To for each row by looking at the next Valid From for the same ID.
    w = Window.partitionBy("WWI Transaction Type ID").orderBy("Valid From")
    result_with_next = result.withColumn("next_valid_from", F.lead("Valid From").over(w))
    end_of_time_literal = F.lit("9999-12-31 23:59:59.999999")
    final_df = (
        result_with_next
        .withColumn(
            "Valid To",
            F.when(F.col("next_valid_from").isNotNull(), F.col("next_valid_from")).otherwise(end_of_time_literal)
        )
        .drop("next_valid_from")
        .orderBy("Valid From")
    )
    return final_df


def load_transaction_type_dimension(spark: SparkSession):
    """Orchestrates the full load of the Transaction Type dimension.

    Steps (mirroring the original workflow):
    1. GET_LAST_CUTOFF – read the current cutoff for the Transaction Type table.
    2. SET_TABLE_NAME – (implicit in the queries, the table name is 'Transaction Type').
    3. EXTRACT_TO_STAGING – run the extraction logic and write to the staging table.
    4. GET_LINEAGE_KEY – obtain the pending lineage key.
    5. MIGRATE_STAGED_DATA – close off existing rows, insert new rows, update lineage and cutoff.
    6. CLEAR_STAGING – truncate the staging table.
    """
    # ---------------------------------------------------------------------
    # 1. GET_LAST_CUTOFF
    # ---------------------------------------------------------------------
    cutoff_row = (
        spark.table("Integration.ETL Cutoff")
        .filter(F.col("Table Name") == "Transaction Type")
        .select("Cutoff Time")
        .orderBy(F.col("Cutoff Time").desc())
        .limit(1)
        .collect()
    )
    if not cutoff_row:
        raise ValueError("No cutoff record found for 'Transaction Type' in Integration.ETL Cutoff.")
    last_cutoff = cutoff_row[0]["Cutoff Time"]

    # New cutoff – use the current timestamp as the watermark for this run.
    new_cutoff = spark.sql("SELECT current_timestamp() AS now").collect()[0]["now"]

    # ---------------------------------------------------------------------
    # 2. EXTRACT_TO_STAGING
    # ---------------------------------------------------------------------
    updates_df = get_transaction_type_updates(spark, last_cutoff, new_cutoff)
    # Write (overwrite) to the staging table.
    updates_df.write.mode("overwrite").saveAsTable("Integration.TransactionType_Staging")

    # ---------------------------------------------------------------------
    # 3. GET_LINEAGE_KEY
    # ---------------------------------------------------------------------
    lineage_row = (
        spark.table("Integration.Lineage")
        .filter((F.col("Table Name") == "Transaction Type") & (F.col("Data Load Completed").isNull()))
        .orderBy(F.col("Lineage Key").desc())
        .limit(1)
        .collect()
    )
    if not lineage_row:
        raise ValueError("No pending lineage record found for 'Transaction Type' in Integration.Lineage.")
    lineage_key = lineage_row[0]["Lineage Key"]

    # ---------------------------------------------------------------------
    # 4. MIGRATE_STAGED_DATA – translate the destination procedure logic.
    # ---------------------------------------------------------------------
    # a) Close off existing rows in the dimension where they are still open.
    spark.sql("""
        WITH RowsToCloseOff AS (
            SELECT pm.`WWI Transaction Type ID`, MIN(pm.`Valid From`) AS `Valid From`
            FROM Integration.TransactionType_Staging AS pm
            GROUP BY pm.`WWI Transaction Type ID`
        )
        UPDATE Dimension.`Transaction Type` AS d
        SET d.`Valid To` = r.`Valid From`
        FROM RowsToCloseOff AS r
        WHERE d.`WWI Transaction Type ID` = r.`WWI Transaction Type ID`
          AND d.`Valid To` = TIMESTAMP('9999-12-31 23:59:59.999999')
    """)

    # b) Insert new rows from staging into the dimension.
    spark.sql(f"""
        INSERT INTO Dimension.`Transaction Type` (
            `WWI Transaction Type ID`, `Transaction Type`, `Valid From`, `Valid To`, `Lineage Key`
        )
        SELECT `WWI Transaction Type ID`, `Transaction Type`, `Valid From`, `Valid To`, {lineage_key}
        FROM Integration.TransactionType_Staging
    """)

    # c) Mark lineage as completed.
    spark.sql(f"""
        UPDATE Integration.Lineage
        SET `Data Load Completed` = current_timestamp(),
            `Was Successful` = 1
        WHERE `Lineage Key` = {lineage_key}
    """)

    # d) Advance the ETL cutoff.
    spark.sql(f"""
        UPDATE Integration.`ETL Cutoff`
        SET `Cutoff Time` = (
            SELECT `Source System Cutoff Time`
            FROM Integration.Lineage
            WHERE `Lineage Key` = {lineage_key}
        )
        WHERE `Table Name` = 'Transaction Type'
    """)

    # ---------------------------------------------------------------------
    # 5. CLEAR_STAGING
    # ---------------------------------------------------------------------
    spark.sql("TRUNCATE TABLE Integration.TransactionType_Staging")

    # End of load – if this were a real transaction we would commit here.
    # Spark writes are atomic per statement; no explicit COMMIT needed.

# Example entry point (Databricks notebooks typically expose a Spark session as `spark`).
# load_transaction_type_dimension(spark)
