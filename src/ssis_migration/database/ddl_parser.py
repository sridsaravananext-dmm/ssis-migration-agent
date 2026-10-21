"""
Parse CREATE TABLE statements from a DDL file to extract
column metadata including nullability and primary keys.

When a DDL file is supplied (change #5), the parsed metadata
overrides dtsx-derived columns (which have nullability/keys
set to None).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ssis_migration.database.ir import DatabaseIR, SQLTableIR
from ssis_migration.database.dtsx_metadata_builder import (
    TSQL_TO_SPARK_MAP,
)


# ------------------------------------------------------------
# Regex patterns
# ------------------------------------------------------------

# CREATE TABLE [schema].[table] (
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+"
    r"\[?(?P<schema>\w+)\]?\."
    r"\[?(?P<table>\w+)\]?\s*\(",
    re.IGNORECASE,
)

# Column definition: [Column] type(size) [NULL|NOT NULL] [IDENTITY]
# Skips lines that are constraint definitions (PRIMARY KEY, FOREIGN KEY, etc.)
_COLUMN_RE = re.compile(
    r"""
    ^\s*
    (?:\[(?P<name_bracket>[^\]]+)\]|(?P<name_plain>\w+))
    \s+
    (?P<type>
        \w+
        (?:\s*\(\s*\d+\s*(?:,\s*\d+\s*)?\s*\))?
    )
    (?:\s+COLLATE\s+\w+)?
    (?P<constraints>.*?)
    \s*,?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# PRIMARY KEY constraint: extract column names
_PK_RE = re.compile(
    r"PRIMARY\s+KEY\s+(?:CLUSTERED\s+|NONCLUSTERED\s+)?\(\s*([^)]+)\s*\)",
    re.IGNORECASE,
)

# CONSTRAINT ... PRIMARY KEY (col1, col2)
_CONSTRAINT_PK_RE = re.compile(
    r"CONSTRAINT\s+\[?\w+\]?\s+PRIMARY\s+KEY\s+(?:CLUSTERED\s+|NONCLUSTERED\s+)?\(\s*([^)]+)\s*\)",
    re.IGNORECASE,
)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _is_constraint_line(line: str) -> bool:
    """Check if a line inside CREATE TABLE is a constraint, not a column."""
    stripped = line.strip().rstrip(",")
    upper = stripped.upper()
    return (
        upper.startswith("CONSTRAINT")
        or upper.startswith("PRIMARY KEY")
        or upper.startswith("FOREIGN KEY")
        or upper.startswith("CHECK")
        or upper.startswith("UNIQUE")
        or upper.startswith("CLUSTERED")
        or upper.startswith("NONCLUSTERED")
    )


def _parse_nullability(constraints: str) -> bool | None:
    """Extract NULL/NOT NULL from column constraints."""
    if re.search(r"\bNOT\s+NULL\b", constraints, re.IGNORECASE):
        return False
    if re.search(r"\bNULL\b", constraints, re.IGNORECASE):
        return True
    return None


def _parse_pk_columns(body: str) -> list[str]:
    """Extract primary key column names from CREATE TABLE body."""
    pk_cols: list[str] = []
    for match in _CONSTRAINT_PK_RE.finditer(body):
        cols = match.group(1)
        pk_cols.extend(
            c.strip().strip("[]") for c in cols.split(",")
        )
    if not pk_cols:
        for match in _PK_RE.finditer(body):
            cols = match.group(1)
            pk_cols.extend(
                c.strip().strip("[]") for c in cols.split(",")
            )
    return pk_cols


def _strip_type_paren(tsql_type: str) -> tuple[str, int | None, int | None]:
    """Split 'nvarchar(50)' -> ('nvarchar', 50, None), 'numeric(18,2)' -> ('numeric', 18, 2)."""
    match = re.match(r"(\w+)\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)", tsql_type)
    if not match:
        return tsql_type.strip().lower(), None, None
    base = match.group(1).lower()
    size = int(match.group(2)) if match.group(2) else None
    scale = int(match.group(3)) if match.group(3) else None
    return base, size, scale


def _make_spark_type(tsql_type: str) -> str:
    """Build a Spark type string from a T-SQL type, preserving precision/scale."""
    base, precision, scale = _strip_type_paren(tsql_type)
    if base in ("numeric", "decimal"):
        if scale is not None and precision is not None:
            return f"DECIMAL({precision},{scale})"
        if precision is not None:
            return f"DECIMAL({precision},0)"
        return "DECIMAL(18,0)"
    return TSQL_TO_SPARK_MAP.get(base, "STRING")


# ------------------------------------------------------------
# Main parser
# ------------------------------------------------------------

def parse_ddl_file(ddl_path: str | Path) -> DatabaseIR:
    """
    Parse CREATE TABLE statements from a DDL file.

    Returns a DatabaseIR with tables, columns, nullability, and
    primary keys extracted from the DDL.
    """
    text = Path(ddl_path).read_text(encoding="utf-8")
    return parse_ddl_text(text)


def parse_ddl_text(text: str) -> DatabaseIR:
    """Parse DDL text and return a DatabaseIR."""
    database_ir = DatabaseIR()
    schemas_seen: set[str] = set()

    # Find all CREATE TABLE statements
    for match in _CREATE_TABLE_RE.finditer(text):
        schema = match.group("schema")
        table = match.group("table")
        schemas_seen.add(schema)

        # Find the matching closing parenthesis
        start = match.end()
        depth = 1
        pos = start
        while pos < len(text) and depth > 0:
            if text[pos] == "(":
                depth += 1
            elif text[pos] == ")":
                depth -= 1
            pos += 1

        body = text[start : pos - 1]

        # Parse columns and constraints from the body
        lines = _split_body_lines(body)
        columns: list[dict[str, Any]] = []
        pk_columns: list[str] = []

        for line in lines:
            stripped = line.strip().rstrip(",")
            if not stripped:
                continue

            if _is_constraint_line(stripped):
                # Extract PK columns from constraint lines
                pk_match = _PK_RE.search(stripped)
                if pk_match:
                    cols = pk_match.group(1)
                    for c in cols.split(","):
                        name = c.strip()
                        name = re.sub(
                            r"\s+(ASC|DESC)\s*$",
                            "",
                            name,
                            flags=re.IGNORECASE,
                        )
                        pk_columns.append(
                            name.strip("[]")
                        )
                continue

            col_match = _COLUMN_RE.match(stripped)
            if not col_match:
                continue

            col_name = (
                col_match.group("name_bracket")
                or col_match.group("name_plain")
            ).strip("[]")
            tsql_type = col_match.group("type").strip()
            constraints = col_match.group("constraints") or ""

            nullable = _parse_nullability(constraints)

            base_type, max_length, scale = _strip_type_paren(tsql_type)

            columns.append({
                "name": col_name,
                "data_type": tsql_type.lower(),
                "spark_type": _make_spark_type(tsql_type),
                "max_length": max_length,
                "precision": None,
                "scale": scale,
                "nullable": nullable,
                "ordinal": None,
                "is_primary_key": False,
            })

        # Mark PK columns
        pk_set = {c.lower() for c in pk_columns}
        for col in columns:
            if col["name"].lower() in pk_set:
                col["is_primary_key"] = True

        table_ir = SQLTableIR(
            schema=schema,
            name=table,
            object_type="TABLE",
        )
        table_ir.columns = columns
        table_ir.primary_keys = pk_columns
        database_ir.tables.append(table_ir)

    database_ir.schemas = sorted(schemas_seen)
    database_ir.database = {
        "name": "ddl_file",
        "source": "ddl_file",
        "table_count": len(database_ir.tables),
    }

    return database_ir


def merge_ddl_into_dtsx(
    dtsx_ir: DatabaseIR,
    ddl_ir: DatabaseIR,
) -> DatabaseIR:
    """
    Override dtsx-derived columns with DDL-derived columns.

    For each table present in the DDL, replace the dtsx-derived
    columns with DDL columns (which have nullability and keys).
    Tables not in the DDL keep their dtsx-derived columns.
    """
    ddl_by_key: dict[str, SQLTableIR] = {
        f"{t.schema}.{t.name}".lower(): t for t in ddl_ir.tables
    }

    for table in dtsx_ir.tables:
        key = f"{table.schema}.{table.name}".lower()
        if key in ddl_by_key:
            ddl_table = ddl_by_key[key]
            table.columns = ddl_table.columns
            table.primary_keys = ddl_table.primary_keys

    return dtsx_ir


def _split_body_lines(body: str) -> list[str]:
    """
    Split CREATE TABLE body into individual column/constraint lines,
    respecting nested parentheses.
    """
    lines: list[str] = []
    current: list[str] = []
    depth = 0

    for char in body:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            lines.append("".join(current))
            current = []
        else:
            current.append(char)

    if current:
        lines.append("".join(current))

    return lines