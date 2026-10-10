from __future__ import annotations

import re


def extract_relational_sql(procedure_definition: str) -> str:
    """
    Extract the relational SQL statement from a SQL Server
    stored-procedure definition.

    This intentionally handles procedure-level statements separately
    from the relational statement that needs SQL translation.
    """

    if not procedure_definition:
        raise ValueError("Procedure definition is empty.")

    # Normalize line endings so CRLF/LF formatting does not matter.
    definition = (
        procedure_definition
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    # Locate the procedure body.
    body_match = re.search(
        r"\bAS\s+BEGIN\b",
        definition,
        flags=re.IGNORECASE,
    )

    if not body_match:
        raise ValueError(
            "Could not locate SQL Server procedure body "
            "(expected AS ... BEGIN)."
        )

    body = definition[body_match.end():]

    # Remove the procedure-level END.
    end_match = re.search(
        r"\bEND\s*;\s*$",
        body,
        flags=re.IGNORECASE,
    )

    if end_match:
        body = body[:end_match.start()]

    # Remove common SQL Server procedure/session statements.
    body = re.sub(
        r"\bSET\s+NOCOUNT\s+ON\s*;",
        "",
        body,
        flags=re.IGNORECASE,
    )

    body = re.sub(
        r"\bSET\s+XACT_ABORT\s+ON\s*;",
        "",
        body,
        flags=re.IGNORECASE,
    )

    # Remove the conventional terminal return statement.
    body = re.sub(
        r"\bRETURN\s+\d+\s*;",
        "",
        body,
        flags=re.IGNORECASE,
    )

    sql = body.strip()

    if not sql:
        raise ValueError(
            "Procedure body does not contain relational SQL."
        )

    return sql