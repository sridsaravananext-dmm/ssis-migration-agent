from __future__ import annotations

from typing import Any


TYPE_MAP = {
    "bigint": "BIGINT",
    "binary": "BINARY",
    "bit": "BOOLEAN",
    "char": "STRING",
    "date": "DATE",
    "datetime": "TIMESTAMP",
    "datetime2": "TIMESTAMP",
    "decimal": "DECIMAL",
    "numeric": "DECIMAL",
    "float": "DOUBLE",
    "int": "INT",
    "money": "DECIMAL(19,4)",
    "nchar": "STRING",
    "ntext": "STRING",
    "nvarchar": "STRING",
    "real": "FLOAT",
    "smalldatetime": "TIMESTAMP",
    "smallint": "SMALLINT",
    "smallmoney": "DECIMAL(10,4)",
    "sysname": "STRING",
    "text": "STRING",
    "time": "STRING",
    "tinyint": "SMALLINT",
    "uniqueidentifier": "STRING",
    "varbinary": "BINARY",
    "varchar": "STRING",
    "xml": "STRING",
}

REVIEW_TYPES = {
    "geography": "Spatial representation requires review.",
    "geometry": "Spatial representation requires review.",
    "hierarchyid": "Hierarchy representation requires review.",
}


def quote_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def map_sql_type(
    column: dict[str, Any],
) -> tuple[str | None, str | None]:
    source_type = str(column.get("data_type", "")).lower().strip()

    if source_type == "geography":
        return (
        "GEOGRAPHY(4326)",
        "Target supports SRID 4326; verify source SRID and "
        "spatial conversion in the migration execution path.",
        )

    if source_type in REVIEW_TYPES:
        return "STRING", REVIEW_TYPES[source_type]

    if source_type in {"decimal", "numeric"}:
        precision = int(column.get("precision") or 38)
        scale = int(column.get("scale") or 0)

        if not 1 <= precision <= 38 or not 0 <= scale <= precision:
            return None, f"Invalid decimal precision/scale: {precision},{scale}"

        return f"DECIMAL({precision},{scale})", None

    if source_type in TYPE_MAP:
        return TYPE_MAP[source_type], None

    return None, f"Unsupported source type: {source_type}"


def generate_ddl(
    metadata: dict[str, Any],
    catalog: str,
    *,
    column_mapping: bool = False,
) -> tuple[str, dict[str, Any]]:
    tables = metadata.get("tables")

    if not isinstance(tables, list):
        raise ValueError("Metadata must contain a 'tables' list.")

    statements = []
    review_items = []

    for table in tables:
        table_schema = table.get("schema")
        table_name = table.get("name")
        columns = table.get("columns")

        if not table_schema or not table_name or not isinstance(columns, list):
            review_items.append({
                "object": str(table_name or "<unknown>"),
                "issue": "Missing table schema, name, or column definitions.",
            })
            continue

        definitions = []
        emitted_columns = []

        for column in sorted(
            columns,
            key=lambda item: int(item.get("ordinal") or 0),
        ):
            name = column.get("name")

            if not name:
                review_items.append({
                    "object": f"{table_schema}.{table_name}",
                    "issue": "Unnamed column cannot be generated.",
                })
                continue

            target_type, reason = map_sql_type(column)

            if target_type is None:
                review_items.append({
                    "object": f"{table_schema}.{table_name}",
                    "column": name,
                    "source_type": column.get("data_type"),
                    "issue": reason,
                })
                continue

            if reason:
                review_items.append({
                    "object": f"{table_schema}.{table_name}",
                    "column": name,
                    "source_type": column.get("data_type"),
                    "target_type": target_type,
                    "issue": reason,
                })

            nullable = column.get("nullable", True)
            not_null = "" if nullable else " NOT NULL"

            definitions.append(
                f"  {quote_identifier(name)} {target_type}{not_null}"
            )
            emitted_columns.append(name)

        expected_columns = [
            column["name"]
            for column in columns
            if column.get("name")
        ]
        missing = [
            name for name in expected_columns
            if name not in emitted_columns
        ]

        if missing:
            review_items.append({
                "object": f"{table_schema}.{table_name}",
                "issue": "DDL omitted one or more columns; table is incomplete.",
                "missing_columns": missing,
            })

        # Never present a partial table as a complete definition.
        if missing or not definitions:
            continue

        target = ".".join([
            quote_identifier(catalog),
            quote_identifier(str(table_schema).lower()),
            quote_identifier(str(table_name).lower()),
        ])

        statement = (
            f"CREATE TABLE IF NOT EXISTS {target} (\n"
            + ",\n".join(definitions)
            + "\n)\nUSING DELTA"
        )

        if column_mapping:
            statement += (
                "\nTBLPROPERTIES "
                "('delta.columnMapping.mode' = 'name')"
            )

        statement += ";"
        statements.append(statement)

    ddl = "\n\n".join(statements)
    if ddl:
        ddl += "\n"

    report = {
        "status": "REVIEW_REQUIRED" if review_items else "GENERATED",
        "table_count": len(tables),
        "generated_table_count": len(statements),
        "review_items": review_items,
    }

    return ddl, report
