from __future__ import annotations

import re
from typing import Any


class SQLTranslationResult:

    def __init__(
        self,
        *,
        status: str,
        sql: str | None = None,
        parameters: list[str] | None = None,
        source_objects: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.sql = sql
        self.parameters = parameters or []
        self.source_objects = source_objects or []
        self.evidence = evidence or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sql": self.sql,
            "parameters": self.parameters,
            "source_objects": self.source_objects,
            "evidence": self.evidence,
        }


class SQLServerToSparkSQLTranslator:
    """
    Deterministic translation of supported relational SQL Server
    procedure bodies into Databricks/Spark SQL.

    This class deliberately does not attempt to translate arbitrary
    procedural T-SQL.
    """

    PARAMETER_PATTERN = re.compile(
        r"""
        @(?P<name>[A-Za-z_][A-Za-z0-9_]*)
        \s+
        (?P<type>
            [A-Za-z_][A-Za-z0-9_]*
            (?:\s*\([^)]*\))?
        )
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    UNSUPPORTED_PATTERNS = {
    "CURSOR": r"\bCURSOR\b",
    "WHILE": r"\bWHILE\b",
    "TRY_CATCH": r"\bBEGIN\s+TRY\b",
    "TEMP_TABLE": r"(?:CREATE\s+TABLE\s+#\w+|#\w+)",
    "TABLE_VARIABLE": r"\bDECLARE\s+@\w+\s+TABLE\b",
    "DYNAMIC_SQL": r"\bEXEC(?:UTE)?\s*\(",
    "MERGE": r"\bMERGE\b",
    }   

    def translate(
        self,
        sql: str,
    ) -> SQLTranslationResult:

        if not sql or not str(sql).strip():
            return SQLTranslationResult(
                status="MISSING_SQL"
            )

        source_sql = str(sql)

        unsupported = self._detect_unsupported(source_sql)

        if unsupported:
            return SQLTranslationResult(
                status="UNSUPPORTED_PROCEDURAL_SQL",
                evidence={
                    "unsupported_features": unsupported,
                },
            )

        parameters = self._extract_parameters(
            source_sql
        )

        query = self._extract_main_query(
            source_sql
        )

        if not query:
            return SQLTranslationResult(
                status="NO_RELATIONAL_QUERY_FOUND",
                parameters=parameters,
            )

        translated = self._translate_query(
            query,
            parameters,
        )

        source_objects = self._extract_source_objects(
            translated
        )

        return SQLTranslationResult(
            status="TRANSLATED",
            sql=translated,
            parameters=parameters,
            source_objects=source_objects,
            evidence={
                "source_sql_length": len(source_sql),
                "translated_sql_length": len(translated),
            },
        )

    def _detect_unsupported(
        self,
        sql: str,
    ) -> list[str]:

        detected = []

        for feature, pattern in self.UNSUPPORTED_PATTERNS.items():
            if re.search(
                pattern,
                sql,
                flags=re.IGNORECASE | re.DOTALL,
            ):
                detected.append(feature)

        return detected

    def _extract_parameters(
        self,
        sql: str,
    ) -> list[str]:

        procedure_header = re.search(
            r"""
            \bCREATE\s+
            (?:OR\s+ALTER\s+)?
            PROCEDURE\b
            .*?
            \bAS\b
            """,
            sql,
            flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
        )

        if not procedure_header:
            return []

        header = procedure_header.group(0)

        parameters = []

        for match in self.PARAMETER_PATTERN.finditer(header):
            parameters.append(
                match.group("name")
            )

        return parameters

    def _extract_main_query(
        self,
        sql: str,
    ) -> str | None:
        """
        Extract the main relational SELECT statement.

        Important:
        Do NOT terminate on a generic END because CASE expressions
        contain END as well.

        SQL Server procedure structure:

            AS
            BEGIN
                SELECT ...
                RETURN 0;
            END;

        Therefore RETURN is the reliable boundary for the current
        supported procedure pattern.
        """

        select_match = re.search(
            r"\bSELECT\b",
            sql,
            flags=re.IGNORECASE,
        )

        if not select_match:
            return None

        query = sql[select_match.start():]

        # Remove everything beginning with RETURN.
        return_match = re.search(
            r"\bRETURN\b",
            query,
            flags=re.IGNORECASE,
        )

        if return_match:
            query = query[:return_match.start()]

        # Remove a trailing procedure END if one remains.
        query = re.sub(
            r"\bEND\s*;\s*$",
            "",
            query,
            flags=re.IGNORECASE | re.DOTALL,
        )

        return query.strip().rstrip(";").strip()

    def _translate_query(
        self,
        query: str,
        parameters: list[str],
    ) -> str:

        translated = query.strip()

        # SQL Server procedure parameters become named runtime
        # parameters/placeholders. The notebook/runtime layer will
        # determine how those values are supplied.
        for parameter in parameters:
            translated = re.sub(
                rf"@{re.escape(parameter)}\b",
                f":{parameter}",
                translated,
                flags=re.IGNORECASE,
            )

        # SQL Server bracketed identifiers.
        translated = re.sub(
            r"\[([^\]]+)\]",
            r"`\1`",
            translated,
        )

        # SQL Server datetime2 type references.
        translated = re.sub(
            r"\bdatetime2(?:\s*\([^)]*\))?\b",
            "TIMESTAMP",
            translated,
            flags=re.IGNORECASE,
        )

        return translated

    def _extract_source_objects(
        self,
        query: str,
    ) -> list[str]:

        pattern = re.compile(
            r"""
            \b(?:FROM|JOIN)\s+
            (?:
                `([^`]+)`
                |
                ([A-Za-z_][A-Za-z0-9_$]*)
            )
            \s*\.\s*
            (?:
                `([^`]+)`
                |
                ([A-Za-z_][A-Za-z0-9_$]*)
            )
            """,
            flags=re.IGNORECASE | re.VERBOSE,
        )

        objects = []

        for match in pattern.finditer(query):
            schema = match.group(1) or match.group(2)
            name = match.group(3) or match.group(4)

            if schema and name:
                objects.append(
                    f"{schema}.{name}"
                )

        return sorted(set(objects))
