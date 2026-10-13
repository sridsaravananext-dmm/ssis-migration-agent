from ssis_migration.ddl_generator import generate_ddl


def test_generates_every_supported_column():
    metadata = {
        "tables": [
            {
                "schema": "Sales",
                "name": "Orders",
                "columns": [
                    {
                        "name": "Order ID",
                        "data_type": "int",
                        "nullable": False,
                        "ordinal": 1,
                    },
                    {
                        "name": "Customer Name",
                        "data_type": "nvarchar",
                        "nullable": True,
                        "ordinal": 2,
                    },
                    {
                        "name": "Amount",
                        "data_type": "decimal",
                        "precision": 12,
                        "scale": 2,
                        "nullable": True,
                        "ordinal": 3,
                    },
                ],
            }
        ]
    }

    ddl, report = generate_ddl(metadata, "test_catalog")

    assert "`Order ID` INT NOT NULL" in ddl
    assert "`Customer Name` STRING" in ddl
    assert "`Amount` DECIMAL(12,2)" in ddl
    assert report["generated_table_count"] == 1
    assert not any(
        "incomplete" in item["issue"].lower()
        for item in report["review_items"]
    )


def test_unsupported_type_is_reported_not_silently_dropped():
    metadata = {
        "tables": [
            {
                "schema": "Sales",
                "name": "Orders",
                "columns": [
                    {
                        "name": "Order ID",
                        "data_type": "int",
                        "nullable": False,
                        "ordinal": 1,
                    },
                    {
                        "name": "Custom Value",
                        "data_type": "unknown_custom_type",
                        "nullable": True,
                        "ordinal": 2,
                    },
                ],
            }
        ]
    }

    ddl, report = generate_ddl(metadata, "test_catalog")

    assert "CREATE TABLE" not in ddl
    assert report["generated_table_count"] == 0
    assert any(
        item.get("column") == "Custom Value"
        for item in report["review_items"]
    )
    assert any(
        item.get("missing_columns") == ["Custom Value"]
        for item in report["review_items"]
    )


def test_sysname_maps_to_string():
    metadata = {
        "tables": [
            {
                "schema": "Integration",
                "name": "Lineage",
                "columns": [
                    {
                        "name": "Table Name",
                        "data_type": "sysname",
                        "nullable": False,
                        "ordinal": 1,
                    }
                ],
            }
        ]
    }

    ddl, report = generate_ddl(metadata, "test_catalog")

    assert "`Table Name` STRING NOT NULL" in ddl
    assert report["generated_table_count"] == 1

def test_column_mapping_is_opt_in():
    metadata = {
        "tables": [
            {
                "schema": "Sales",
                "name": "Orders",
                "columns": [
                    {
                        "name": "Order ID",
                        "data_type": "int",
                        "nullable": False,
                        "ordinal": 1,
                    }
                ],
            }
        ]
    }

    ddl_default, _ = generate_ddl(metadata, "test_catalog")
    ddl_mapped, _ = generate_ddl(
        metadata,
        "test_catalog",
        column_mapping=True,
    )

    assert "delta.columnMapping.mode" not in ddl_default
    assert "'delta.columnMapping.mode' = 'name'" in ddl_mapped