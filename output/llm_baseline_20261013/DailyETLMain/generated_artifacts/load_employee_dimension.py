from pyspark.sql import functions as F, Window

def get_employee_updates(last_cutoff: str, new_cutoff: str):
    """Extract employee changes between two cut‑off timestamps and load them into the staging table.

    This implementation reproduces the logic of the source stored procedure
    `Integration.GetEmployeeUpdates` using Spark DataFrames on Databricks.

    Parameters
    ----------
    last_cutoff : str
        The previous ETL watermark (inclusive lower bound). Expected format
        parsable by Spark's ``timestamp`` type.
    new_cutoff : str
        The current ETL watermark (inclusive upper bound).
    """
    # Constant representing the far‑future "end of time" used in the source procedure
    END_OF_TIME = "9999-12-31 23:59:59.9999999"

    # ---------------------------------------------------------------------
    # 1. Load source tables (current and archive). They are expected to be
    #    Delta tables with the columns described in the migration context.
    # ---------------------------------------------------------------------
    people_cur = spark.table("Application.People")
    people_arc = spark.table("Application.People_Archive")

    # Union the two tables – this is the equivalent of the FOR SYSTEM_TIME AS OF
    # query that the source procedure uses.
    people_all = people_cur.unionByName(people_arc)

    # ---------------------------------------------------------------------
    # 2. Build the list of employee changes that occurred between the cut‑offs.
    #    The source uses a cursor over a UNION ALL of the archive and current
    #    tables, filtered on ValidFrom and IsEmployee.
    # ---------------------------------------------------------------------
    change_cur = (
        people_cur
        .filter(
            (F.col("ValidFrom") > F.lit(last_cutoff)) &
            (F.col("ValidFrom") <= F.lit(new_cutoff)) &
            (F.col("IsEmployee") != 0)
        )
        .select(
            F.col("PersonID").alias("WWI Employee ID"),
            F.col("ValidFrom")
        )
    )

    change_arc = (
        people_arc
        .filter(
            (F.col("ValidFrom") > F.lit(last_cutoff)) &
            (F.col("ValidFrom") <= F.lit(new_cutoff)) &
            (F.col("IsEmployee") != 0)
        )
        .select(
            F.col("PersonID").alias("WWI Employee ID"),
            F.col("ValidFrom")
        )
    )

    change_list = change_cur.unionByName(change_arc).orderBy("ValidFrom")

    # ---------------------------------------------------------------------
    # 3. For each change timestamp retrieve the employee row as it existed at
    #    that point in time. The temporal_context tells us to emulate
    #    "FOR SYSTEM_TIME AS OF @ValidFrom" by joining on the validity window:
    #        @ValidFrom >= ValidFrom AND @ValidFrom < ValidTo
    # ---------------------------------------------------------------------
    snapshot = (
        change_list.alias("c")
        .join(
            people_all.alias("p"),
            (F.col("p.PersonID") == F.col("c.WWI Employee ID")) &
            (F.col("p.ValidFrom") <= F.col("c.ValidFrom")) &
            (F.col("c.ValidFrom") < F.col("p.ValidTo")),
            "inner"
        )
        .select(
            F.col("c.WWI Employee ID"),
            F.col("p.FullName").alias("Employee"),
            F.col("p.PreferredName").alias("Preferred Name"),
            F.col("p.IsSalesperson").alias("Is Salesperson"),
            F.col("p.Photo"),
            F.col("c.ValidFrom").alias("Valid From"),
            F.lit(END_OF_TIME).alias("Valid To")
        )
    )

    # ---------------------------------------------------------------------
    # 4. Compute the correct "Valid To" for each employee version. The source
    #    procedure sets Valid To to the next Valid From for the same employee,
    #    or to the end‑of‑time constant if no later version exists.
    # ---------------------------------------------------------------------
    w = Window.partitionBy("WWI Employee ID").orderBy("Valid From")
    result = (
        snapshot
        .withColumn("next_valid_from", F.lead("Valid From").over(w))
        .withColumn(
            "Valid To",
            F.when(F.col("next_valid_from").isNotNull(), F.col("next_valid_from")).otherwise(F.lit(END_OF_TIME))
        )
        .drop("next_valid_from")
    )

    # ---------------------------------------------------------------------
    # 5. Load the result into the staging table used by the downstream dimension
    #    load. The destination procedure expects the data to be present in
    #    `Integration.Employee_Staging`.
    # ---------------------------------------------------------------------
    result.write.mode("append").saveAsTable("Integration.Employee_Staging")

    return result

# Example usage (uncomment when running in a notebook):
# df = get_employee_updates('2023-01-01 00:00:00', '2023-01-31 23:59:59')
# df.show()