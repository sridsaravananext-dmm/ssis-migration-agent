"""
Build database metadata (DatabaseIR) from the SSIS package IR alone,
without requiring an external metadata JSON file.

Extracts:
- Target table names from OLE DB Destination OpenRowset properties
- Column names and T-SQL types from OLE DB Source WITH RESULT SETS clauses
- Column names and SSIS internal types from component input/output columns
  (used as fallback when WITH RESULT SETS is unavailable)
- Table references from Execute SQL task SQL statements (DELETE FROM, etc.)
- Stored procedure calls from Execute SQL task SQL statements (EXEC ...)

Nullability, primary keys, and foreign keys are unknown from the .dtsx alone.
A DDL file (change #5) can override these later.
"""

from __future__ import annotations

import re
from typing import Any

from ssis_migration.database.ir import DatabaseIR, SQLObjectIR, SQLTableIR
from ssis_migration.ir.models import PackageIR


# ------------------------------------------------------------
# SSIS internal data type -> T-SQL type name (for reference)
# ------------------------------------------------------------
SSIS_TYPE_MAP: dict[str, str] = {
    "i4": "int",
    "i8": "bigint",
    "i2": "smallint",
    "i1": "tinyint",
    "ui1": "tinyint",
    "ui2": "smallint",
    "ui4": "int",
    "ui8": "bigint",
    "r8": "float",
    "r4": "real",
    "wstr": "nvarchar",
    "str": "varchar",
    "wbytes": "varbinary",
    "bytes": "varbinary",
    "bool": "bit",
    "date": "date",
    "dbdate": "date",
    "dbtime": "time",
    "dbtimestamp": "datetime",
    "dbtimestamp2": "datetime2",
    "filetime": "datetime",
    "image": "varbinary",
    "text": "text",
    "ntext": "ntext",
    "guid": "uniqueidentifier",
    "numeric": "numeric",
    "cy": "money",
    "empty": "varchar",
}

# ------------------------------------------------------------
# T-SQL type -> Spark type
# ------------------------------------------------------------
TSQL_TO_SPARK_MAP: dict[str, str] = {
    "int": "INT",
    "bigint": "BIGINT",
    "smallint": "SMALLINT",
    "tinyint": "TINYINT",
    "float": "DOUBLE",
    "real": "FLOAT",
    "nvarchar": "STRING",
    "varchar": "STRING",
    "nchar": "STRING",
    "char": "STRING",
    "varbinary": "BINARY",
    "binary": "BINARY",
    "bit": "BOOLEAN",
    "date": "DATE",
    "time": "STRING",
    "datetime": "TIMESTAMP",
    "datetime2": "TIMESTAMP",
    "smalldatetime": "TIMESTAMP",
    "datetimeoffset": "STRING",
    "text": "STRING",
    "ntext": "STRING",
    "uniqueidentifier": "STRING",
    "numeric": "DECIMAL(18,0)",
    "decimal": "DECIMAL(18,0)",
    "money": "DECIMAL(19,4)",
    "smallmoney": "DECIMAL(10,4)",
    "image": "BINARY",
    "xml": "STRING",
    "sql_variant": "STRING",
    "geography": "STRING",
    "geometry": "STRING",
}

# ------------------------------------------------------------
# Regex patterns
# ------------------------------------------------------------

# WITH RESULT SETS (( ( col1 type1, col2 type2(50), ... ) ))
_RESULT_SETS_RE = re.compile(
    r"WITH\s+RESULT\s+SETS\s*\(\s*\(\s*(.*?)\s*\)\s*\)",
    re.IGNORECASE | re.DOTALL,
)

# Each column definition inside WITH RESULT SETS:
# [Column Name] type, or Column type(size), or Column type
_RESULT_COL_RE = re.compile(
    r"""
    \[?
    (?P<name>[^\[\],]+?)
    \]?
    \s+
    (?P<type>
        [A-Za-z]+
        (?:\s*\(\s*\d+\s*(?:,\s*\d+\s*)?\s*\))?
    )
    (?:\s*,|\s*$)
    """,
    re.VERBOSE,
)

# Table references in SQL: DELETE FROM schema.table, TRUNCATE TABLE, etc.
_TABLE_REF_RE = re.compile(
    r"(?:DELETE\s+FROM|TRUNCATE\s+TABLE|INSERT\s+INTO|UPDATE|MERGE\s+INTO)\s+"
    r"(?P<ref>\[?\w+\]?\.\[?\w+\]?)",
    re.IGNORECASE,
)

# Stored procedure calls: EXEC schema.proc or EXECUTE schema.proc
_EXEC_RE = re.compile(
    r"(?:EXEC(?:UTE)?)\s+(?P<ref>\[?\w+\]?\.\[?\w+\]?)",
    re.IGNORECASE,
)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _parse_result_sets(sql: str) -> dict[str, str]:
    """Parse WITH RESULT SETS clause -> {column_name: tsql_type}."""
    match = _RESULT_SETS_RE.search(sql)
    if not match:
        return {}

    body = match.group(1)
    columns: dict[str, str] = {}

    for col_match in _RESULT_COL_RE.finditer(body):
        name = col_match.group("name").strip().strip("[]")
        col_type = col_match.group("type").strip()
        if name and col_type:
            columns[name] = col_type

    return columns


def _normalize_table_name(open_rowset: str) -> tuple[str, str]:
    """Parse '[schema].[table]' or 'schema.table' -> (schema, table)."""
    cleaned = open_rowset.strip()
    parts = [p.strip("[]") for p in cleaned.split(".")]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if len(parts) == 1:
        return "dbo", parts[0]
    return "", ""


def _ssis_type_to_tsql(
    ssis_type: str,
    length: str | None = None,
    scale: str | None = None,
) -> str:
    """Convert SSIS internal data type to a T-SQL type string."""
    base = SSIS_TYPE_MAP.get(ssis_type.lower(), "varchar")
    if base in ("nvarchar", "varchar", "varbinary") and length:
        return f"{base}({length})"
    if base == "datetime2" and scale:
        return f"{base}({scale})"
    if base == "numeric" and scale:
        return f"{base}(18,{scale})"
    return base


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if s.isdigit():
        return int(s)
    return None


def _tsql_to_spark(tsql_type: str) -> str:
    """Convert a T-SQL type string to a Spark type string."""
    base = tsql_type.strip().lower()
    # Strip parentheses: nvarchar(50) -> nvarchar
    base = re.sub(r"\(.*\)", "", base).strip()
    return TSQL_TO_SPARK_MAP.get(base, "STRING")


# ------------------------------------------------------------
# Main builder
# ------------------------------------------------------------

def build_database_ir_from_dtsx(package_ir: PackageIR) -> DatabaseIR:
    """
    Build a DatabaseIR from the SSIS package IR alone.

    Extracts target tables and their columns from data flow components
    and Execute SQL task references. Column types are derived from
    WITH RESULT SETS clauses (T-SQL types) when available, falling back
    to SSIS internal types from component columns.

    Nullability, primary keys, and foreign keys are unknown (None).
    """
    database_ir = DatabaseIR()

    tables_by_key: dict[str, SQLTableIR] = {}
    schemas_seen: set[str] = set()

    def _get_or_create_table(schema: str, name: str) -> SQLTableIR:
        key = f"{schema}.{name}".lower()
        if key not in tables_by_key:
            table_ir = SQLTableIR(
                schema=schema,
                name=name,
                object_type="TABLE",
            )
            tables_by_key[key] = table_ir
            database_ir.tables.append(table_ir)
            schemas_seen.add(schema)
        return tables_by_key[key]

    # 1. Extract target tables and columns from data flows
    for data_flow in package_ir.data_flows:
        result_set_types: dict[str, str] = {}
        for component in data_flow.components:
            if component.detected_type == "OLE_DB_SOURCE":
                sql = (component.properties or {}).get("SqlCommand", "")
                if sql:
                    result_set_types = _parse_result_sets(sql)
                    break

        for component in data_flow.components:
            if component.detected_type != "OLE_DB_DESTINATION":
                continue

            open_rowset = (component.properties or {}).get("OpenRowset", "")
            if not open_rowset:
                continue

            schema, name = _normalize_table_name(open_rowset)
            if not schema or not name:
                continue

            table_ir = _get_or_create_table(schema, name)

            for inp in component.inputs:
                for col in inp.get("columns", []):
                    col_name = col.get("name", "")
                    if not col_name:
                        continue
                    if any(c.get("name") == col_name for c in table_ir.columns):
                        continue

                    tsql_type = result_set_types.get(col_name)
                    if not tsql_type:
                        ssis_type = col.get("data_type", "")
                        if ssis_type:
                            tsql_type = _ssis_type_to_tsql(
                                ssis_type,
                                col.get("length"),
                                col.get("scale"),
                            )

                    table_ir.columns.append({
                        "name": col_name,
                        "data_type": tsql_type or "varchar",
                        "spark_type": _tsql_to_spark(tsql_type or "varchar"),
                        "max_length": _safe_int(col.get("length")),
                        "precision": None,
                        "scale": _safe_int(col.get("scale")),
                        "nullable": None,
                        "ordinal": None,
                    })

    # 2. Extract table references from Execute SQL tasks
    for executable in package_ir.executables:
        if executable.executable_type != "Microsoft.ExecuteSQLTask":
            continue
        sql = (executable.properties or {}).get("SqlStatementSource", "")
        if not sql:
            continue
        for match in _TABLE_REF_RE.finditer(sql):
            ref = match.group("ref")
            parts = [p.strip("[]") for p in ref.split(".")]
            if len(parts) >= 2:
                schema, name = parts[-2], parts[-1]
                _get_or_create_table(schema, name)
                schemas_seen.add(schema)

    # 3. Extract stored procedure calls as SQL objects
    proc_names_seen: set[str] = set()
    for executable in package_ir.executables:
        if executable.executable_type != "Microsoft.ExecuteSQLTask":
            continue
        sql = (executable.properties or {}).get("SqlStatementSource", "")
        if not sql:
            continue
        for match in _EXEC_RE.finditer(sql):
            ref = match.group("ref")
            parts = [p.strip("[]") for p in ref.split(".")]
            if len(parts) >= 2:
                schema, name = parts[-2], parts[-1]
                proc_key = f"{schema}.{name}".lower()
                if proc_key not in proc_names_seen:
                    proc_names_seen.add(proc_key)
                    database_ir.objects.append(
                        SQLObjectIR(
                            schema=schema,
                            name=name,
                            object_type="PROCEDURE",
                            definition=None,
                            metadata={"source": "dtsx_exec_reference"},
                        )
                    )
                    schemas_seen.add(schema)

    # 4. Populate database metadata
    database_ir.schemas = sorted(schemas_seen)
    database_ir.database = {
        "name": "dtsx_derived",
        "source": "package_ir",
        "table_count": len(database_ir.tables),
        "procedure_count": len(proc_names_seen),
    }

    return database_ir