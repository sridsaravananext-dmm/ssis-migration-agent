import pyspark.sql.functions as F
from pyspark.sql import Window

def get_supplier_updates(spark, last_cutoff: str, new_cutoff: str):
    """Extract supplier changes between two cutoff timestamps and load them into the staging table.

    This implementation reproduces the logic of the source SQL Server procedure
    `Integration.GetSupplierUpdates` using Spark DataFrames on Databricks.
    It relies on row‑level temporal columns (ValidFrom, ValidTo) and archive tables
    following the `{schema}.{table}_Archive` pattern as described in the migration context.

    Parameters
    ----------
    spark : SparkSession
        Active Spark session.
    last_cutoff : str
        Lower bound of the incremental window (exclusive). Expected format: 'yyyy-MM-dd HH:mm:ss.SSSSSSS'.
    new_cutoff : str
        Upper bound of the incremental window (inclusive). Same format as ``last_cutoff``.
    """
    # ---------------------------------------------------------------------
    # 1. Prepare constants and helper functions
    # ---------------------------------------------------------------------
    end_of_time = F.lit('9999-12-31 23:59:59.9999999').cast('timestamp')
    initial_load_date = F.lit('2013-01-01').cast('date')
    last_cutoff_ts = F.to_timestamp(F.lit(last_cutoff))
    new_cutoff_ts = F.to_timestamp(F.lit(new_cutoff))

    def as_of(df, ts_col):
        """Return rows that are valid for the supplied timestamp (row‑level temporal filter)."""
        return df.filter((F.col('ValidFrom') <= ts_col) & (F.col('ValidTo') > ts_col))

    # ---------------------------------------------------------------------
    # 2. Load source tables (current + archive) – they must contain ValidFrom/ValidTo
    # ---------------------------------------------------------------------
    # Supplier categories (current + archive)
    supplier_categories_cur = spark.table('Purchasing.SupplierCategories')
    supplier_categories_arc = spark.table('Purchasing.SupplierCategories_Archive')
    supplier_categories = supplier_categories_cur.unionByName(supplier_categories_arc)

    # Suppliers (current + archive)
    suppliers_cur = spark.table('Purchasing.Suppliers')
    suppliers_arc = spark.table('Purchasing.Suppliers_Archive')
    suppliers = suppliers_cur.unionByName(suppliers_arc)

    # People – the context does not explicitly list an archive for Application.People.
    # We assume the current table contains the required validity columns.
    people = spark.table('Application.People')

    # ---------------------------------------------------------------------
    # 3. Build the list of supplier‑category changes (cursor replacement)
    # ---------------------------------------------------------------------
    cat_change_list = (
        supplier_categories
        .filter(
            (F.col('ValidFrom') > last_cutoff_ts) &
            (F.col('ValidFrom') <= new_cutoff_ts) &
            (F.col('ValidFrom').cast('date') != initial_load_date)
        )
        .select('SupplierCategoryID', 'ValidFrom')
        .orderBy('ValidFrom')
    )

    # Collect timestamps – expected to be a modest number, safe for driver side loop.
    cat_changes = cat_change_list.select('SupplierCategoryID', 'ValidFrom').collect()

    # ---------------------------------------------------------------------
    # 4. Build the list of supplier changes (second cursor replacement)
    # ---------------------------------------------------------------------
    supplier_change_list = (
        suppliers
        .filter(
            (F.col('ValidFrom') > last_cutoff_ts) &
            (F.col('ValidFrom') <= new_cutoff_ts)
        )
        .select('SupplierID', 'ValidFrom')
        .orderBy('ValidFrom')
    )
    supplier_changes = supplier_change_list.select('SupplierID', 'ValidFrom').collect()

    # ---------------------------------------------------------------------
    # 5. For each timestamp, materialise the rows that would have been inserted
    #    into the #SupplierChanges temp table in the original procedure.
    # ---------------------------------------------------------------------
    rows = []

    for row in cat_changes:
        cat_id = row['SupplierCategoryID']
        ts = row['ValidFrom']
        # point‑in‑time views of the three tables
        sup = as_of(suppliers, ts).alias('s')
        cat = as_of(supplier_categories, ts).alias('sc')
        per = as_of(people, ts).alias('p')
        joined = (
            sup.join(cat, sup['SupplierCategoryID'] == cat['SupplierCategoryID'])
               .join(per, sup['PrimaryContactPersonID'] == per['PersonID'])
               .filter(cat['SupplierCategoryID'] == cat_id)
               .select(
                   sup['SupplierID'].alias('WWI Supplier ID'),
                   sup['SupplierName'].alias('Supplier'),
                   cat['SupplierCategoryName'].alias('Category'),
                   per['FullName'].alias('Primary Contact'),
                   sup['SupplierReference'].alias('Supplier Reference'),
                   sup['PaymentDays'].alias('Payment Days'),
                   sup['DeliveryPostalCode'].alias('Postal Code'),
                   sup['ValidFrom'].alias('Valid From'),
                   sup['ValidTo'].alias('Valid To')
               )
        )
        rows.append(joined)

    for row in supplier_changes:
        sup_id = row['SupplierID']
        ts = row['ValidFrom']
        sup = as_of(suppliers, ts).alias('s')
        cat = as_of(supplier_categories, ts).alias('sc')
        per = as_of(people, ts).alias('p')
        joined = (
            sup.join(cat, sup['SupplierCategoryID'] == cat['SupplierCategoryID'])
               .join(per, sup['PrimaryContactPersonID'] == per['PersonID'])
               .filter(sup['SupplierID'] == sup_id)
               .select(
                   sup['SupplierID'].alias('WWI Supplier ID'),
                   sup['SupplierName'].alias('Supplier'),
                   cat['SupplierCategoryName'].alias('Category'),
                   per['FullName'].alias('Primary Contact'),
                   sup['SupplierReference'].alias('Supplier Reference'),
                   sup['PaymentDays'].alias('Payment Days'),
                   sup['DeliveryPostalCode'].alias('Postal Code'),
                   sup['ValidFrom'].alias('Valid From'),
                   sup['ValidTo'].alias('Valid To')
               )
        )
        rows.append(joined)

    # Union all rows collected from the two loops
    if rows:
        supplier_changes_df = rows[0]
        for df in rows[1:]:
            supplier_changes_df = supplier_changes_df.unionByName(df)
    else:
        # No changes – create an empty DataFrame with the expected schema
        supplier_changes_df = spark.createDataFrame([], schema='WWI Supplier ID INT, Supplier STRING, Category STRING, `Primary Contact` STRING, `Supplier Reference` STRING, `Payment Days` INT, `Postal Code` STRING, `Valid From` TIMESTAMP, `Valid To` TIMESTAMP')

    # ---------------------------------------------------------------------
    # 6. Compute the [Valid To] column as the next [Valid From] for the same supplier
    # ---------------------------------------------------------------------
    w = Window.partitionBy('WWI Supplier ID').orderBy('Valid From')
    supplier_changes_df = (
        supplier_changes_df
        .withColumn('next_valid_from', F.lead('Valid From').over(w))
        .withColumn('Valid To', F.coalesce(F.col('next_valid_from'), end_of_time))
        .drop('next_valid_from')
    )

    # ---------------------------------------------------------------------
    # 7. Load into the staging table (append semantics – the downstream procedure
    #    will handle deduplication / merging).
    # ---------------------------------------------------------------------
    supplier_changes_df.write.format('delta').mode('append').saveAsTable('Integration.Supplier_Staging')

    return supplier_changes_df

# -------------------------------------------------------------------------
# Example usage (to be removed or adapted in production pipelines):
# -------------------------------------------------------------------------
# from pyspark.sql import SparkSession
# spark = SparkSession.builder.getOrCreate()
# df = get_supplier_updates(spark, '2023-01-01 00:00:00.0000000', '2023-01-31 23:59:59.9999999')
# df.show()
