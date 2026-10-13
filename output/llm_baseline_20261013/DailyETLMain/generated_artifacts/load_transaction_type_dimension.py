from pyspark.sql import SparkSession, Window
import pyspark.sql.functions as F

spark = SparkSession.builder.getOrCreate()

END_OF_TIME = "9999-12-31 23:59:59.9999999"

def load_transaction_type_dimension(last_cutoff: str, new_cutoff: str):
    """Load Transaction Type dimension for the given cutoff window.

    Parameters
    ----------
    last_cutoff: str
        Lower bound (exclusive) of the validity window (ISO‑8601 timestamp string).
    new_cutoff: str
        Upper bound (inclusive) of the validity window (ISO‑8601 timestamp string).
    """
    # ---------------------------------------------------------------------
    # 1. Extract changed transaction‑type rows (source procedure logic)
    # ---------------------------------------------------------------------
    df_current = spark.table("Application.TransactionTypes")
    df_archive = spark.table("Application.TransactionTypes_Archive")
    df_all = df_current.unionByName(df_archive)

    # Identify the change timestamps for each TransactionTypeID
    df_changes = (
        df_all
        .filter((F.col("ValidFrom") > F.lit(last_cutoff)) & (F.col("ValidFrom") <= F.lit(new_cutoff)))
        .select("TransactionTypeID", "ValidFrom")
        .distinct()
    )

    # For each (TransactionTypeID, ValidFrom) retrieve the row that was valid at that moment
    df_as_of = (
        df_changes.alias("c")
        .join(
            df_all.alias("t"),
            (F.col("c.TransactionTypeID") == F.col("t.TransactionTypeID"))
            & (F.col("c.ValidFrom") >= F.col("t.ValidFrom"))
            & (F.col("c.ValidFrom") < F.col("t.ValidTo")),
            "inner",
        )
        .select(
            F.col("t.TransactionTypeID").alias("WWI Transaction Type ID"),
            F.col("t.TransactionTypeName").alias("Transaction Type"),
            F.col("c.ValidFrom").alias("Valid From"),
            F.col("t.ValidTo").alias("TempValidTo"),
        )
    )

    # Compute the final Valid To by looking at the next Valid From for the same ID
    w = Window.partitionBy("WWI Transaction Type ID").orderBy("Valid From")
    df_final = (
        df_as_of
        .withColumn("next_valid_from", F.lead("Valid From").over(w))
        .withColumn("Valid To", F.coalesce(F.col("next_valid_from"), F.lit(END_OF_TIME)))
        .drop("TempValidTo", "next_valid_from")
    )

    # Persist the result to the staging table
    df_final.write.format("delta").mode("append").saveAsTable("Integration.TransactionType_Staging")

    # ---------------------------------------------------------------------
    # 2. Load staged data into the dimension (destination procedure logic)
    # ---------------------------------------------------------------------
    # Retrieve the pending lineage key
    lineage_rows = spark.sql(
        """
        SELECT `Lineage Key`
        FROM Integration.Lineage
        WHERE `Table Name` = 'Transaction Type' AND `Data Load Completed` IS NULL
        ORDER BY `Lineage Key` DESC
        LIMIT 1
        """
    ).collect()
    if not lineage_rows:
        raise RuntimeError("No pending lineage record found for Transaction Type dimension.")
    lineage_key = lineage_rows[0][0]

    # Determine the earliest Valid From per WWI Transaction Type ID in the staging area
    rows_to_close = spark.sql(
        """
        SELECT `WWI Transaction Type ID`, MIN(`Valid From`) AS `Valid From`
        FROM Integration.TransactionType_Staging
        GROUP BY `WWI Transaction Type ID`
        """
    )
    rows_to_close.createOrReplaceTempView("RowsToCloseOff")

    # Close off existing open‑ended rows in the dimension
    spark.sql(
        f"""
        UPDATE Dimension.transaction_type AS dim
        SET dim.`Valid To` = rco.`Valid From`
        FROM RowsToCloseOff rco
        WHERE dim.`WWI Transaction Type ID` = rco.`WWI Transaction Type ID`
          AND dim.`Valid To` = '{END_OF_TIME}'
        """
    )

    # Insert the new rows from staging, attaching the lineage key
    spark.sql(
        f"""
        INSERT INTO Dimension.transaction_type
            (`WWI Transaction Type ID`, `Transaction Type`, `Valid From`, `Valid To`, `Lineage Key`)
        SELECT `WWI Transaction Type ID`, `Transaction Type`, `Valid From`, `Valid To`, {lineage_key}
        FROM Integration.TransactionType_Staging
        """
    )

    # Mark the lineage record as completed successfully
    spark.sql(
        f"""
        UPDATE Integration.Lineage
        SET `Data Load Completed` = current_timestamp(),
            `Was Successful` = 1
        WHERE `Lineage Key` = {lineage_key}
        """
    )

    # Advance the ETL cutoff for this source table
    spark.sql(
        f"""
        UPDATE Integration.`ETL Cutoff`
        SET `Cutoff Time` = (
            SELECT `Source System Cutoff Time`
            FROM Integration.Lineage
            WHERE `Lineage Key` = {lineage_key}
        )
        WHERE `Table Name` = 'Transaction Type'
        """
    )

    # Optional: clear staging if the workflow expects it (not part of original proc)
    # spark.sql("TRUNCATE TABLE Integration.TransactionType_Staging")

    return {
        "lineage_key": lineage_key,
        "rows_inserted": df_final.count()
    }
