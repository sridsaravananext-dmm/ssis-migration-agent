"""
Load stored procedure definitions from a pasted SQL file.

The file should contain one or more CREATE PROCEDURE statements.
Each procedure is parsed into a {schema.name: body} entry.

Example input format:

    CREATE PROCEDURE Integration.GetLineageKey
        @LineageKey int OUTPUT,
        @TableName nvarchar(128)
    AS
    BEGIN
        ...
    END;

    CREATE OR ALTER PROCEDURE Integration.MigrateStagedCityData
    AS
    BEGIN
        ...
    END;
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_CREATE_PROC_RE = re.compile(
    r"""
    \bCREATE\s+
    (?:OR\s+ALTER\s+)?
    PROC(?:EDURE)?\s+
    (?:
        \[(?P<schema_a>[^\]]+)\]
        | (?P<schema_b>\w+)
    )
    \s*\.\s*
    (?:
        \[(?P<name_a>[^\]]+)\]
        | (?P<name_b>\w+)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def load_procedures(
    source: str | Path,
) -> dict[str, str]:
    """
    Load procedure definitions from a SQL file or raw SQL string.

    Returns a dict mapping ``schema.name`` to the full procedure body
    (including the CREATE PROCEDURE header).
    """
    path = Path(source)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = str(source)

    procedures: dict[str, str] = {}

    # Split text on CREATE PROCEDURE boundaries
    parts = re.split(
        r"(?=\bCREATE\s+(?:OR\s+ALTER\s+)?PROC(?:EDURE)?)",
        text,
        flags=re.IGNORECASE,
    )

    for part in parts:
        match = _CREATE_PROC_RE.search(part)
        if not match:
            continue

        schema = match.group("schema_a") or match.group("schema_b")
        name = match.group("name_a") or match.group("name_b")
        if not schema or not name:
            continue

        key = f"{schema}.{name}"
        procedures[key] = part.strip()

    return procedures


def list_procedures(source: str | Path) -> list[str]:
    """Return just the procedure names found in the file."""
    return sorted(load_procedures(source).keys())