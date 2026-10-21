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

    # MERGE is deliberately NOT listed here: Delta Lake supports
    # MERGE INTO natively, so T-SQL MERGE statements are translated.
    UNSUPPORTED_PATTERNS = {
    "CURSOR": r"\bCURSOR\b",
    "WHILE": r"\bWHILE\b",
    "TRY_CATCH": r"\bBEGIN\s+TRY\b",
    "TEMP_TABLE": r"(?:CREATE\s+TABLE\s+#\w+|#\w+)",
    "TABLE_VARIABLE": r"\bDECLARE\s+@\w+\s+TABLE\b",
    "DYNAMIC_SQL": r"\bEXEC(?:UTE)?\s*\(",
    "SEQUENCE": r"\bNEXT\s+VALUE\s+FOR\b",
    }

    # A top-level DML statement following a statement separator.
    # Used to detect multi-statement procedure bodies that cannot
    # be represented as a single Spark SQL statement.
    _FOLLOWING_STATEMENT_RE = re.compile(
        r";\s*(?:UPDATE|INSERT|DELETE|MERGE)\b",
        flags=re.IGNORECASE,
    )

    # T-SQL change-detection pattern:
    #   EXISTS (SELECT a1, a2 EXCEPT SELECT b1, b2)
    # becomes a NULL-safe Delta comparison chain:
    #   (a1 IS DISTINCT FROM b1 OR a2 IS DISTINCT FROM b2)
    # Delta MERGE WHEN-clause conditions cannot contain subqueries,
    # so this rewrite is required for executable output.
    _EXISTS_EXCEPT_RE = re.compile(
        r"EXISTS\s*\(\s*SELECT\s+(?P<left>.+?)"
        r"\s+EXCEPT\s+SELECT\s+(?P<right>.+?)\s*\)",
        flags=re.IGNORECASE | re.DOTALL,
    )

    # T-SQL type names that must be rewritten when they appear
    # as the target type of a CAST/CONVERT expression. Types
    # already valid in Spark SQL (INT, BIGINT, DATE, BIT,
    # DECIMAL, BINARY, DOUBLE) are not listed.
    TSQL_TYPE_MAP = {
        "nvarchar": "STRING",
        "varchar": "STRING",
        "nchar": "STRING",
        "char": "STRING",
        "text": "STRING",
        "ntext": "STRING",
        "sysname": "STRING",
        "uniqueidentifier": "STRING",
        "varbinary": "BINARY",
        "image": "BINARY",
        "datetime": "TIMESTAMP",
        "datetime2": "TIMESTAMP",
        "smalldatetime": "TIMESTAMP",
        "datetimeoffset": "TIMESTAMP",
        "money": "DECIMAL(19,4)",
        "smallmoney": "DECIMAL(10,4)",
        "tinyint": "TINYINT",
        "smallint": "SMALLINT",
        "numeric": "DECIMAL",
        "real": "FLOAT",
        "float": "DOUBLE",
    }

    # Matches a T-SQL type name in a CAST target position
    # ("... AS <type>[ (<args>) ]"). The (precision, scale)
    # arguments of NUMERIC/DECIMAL are preserved.
    _TSQL_TYPE_AS_RE = re.compile(
        r"(?P<as>\bAS\s+)"
        r"(?P<type>"
        r"nvarchar|varchar|nchar|char|ntext|text|sysname|"
        r"uniqueidentifier|varbinary|image|datetime|datetime2|"
        r"smalldatetime|datetimeoffset|money|smallmoney|tinyint|"
        r"smallint|numeric|real|float"
        r")"
        r"(?P<args>\s*\(\s*(?:\d+|max)[^)]*\))?",
        flags=re.IGNORECASE,
    )

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

        # A procedure body whose main query is followed by further
        # top-level DML statements cannot be represented as a single
        # Spark SQL statement. Deterministic single-statement
        # translation only.
        if self._FOLLOWING_STATEMENT_RE.search(query):
            return SQLTranslationResult(
                status="UNSUPPORTED_PROCEDURAL_SQL",
                evidence={
                    "unsupported_features": ["MULTI_STATEMENT"],
                },
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
        Extract the main relational statement.

        Preference order for the main statement:

        1. MERGE  -> Delta Lake supports MERGE INTO natively.
        2. INSERT INTO ... (with a SELECT) -> staged extraction.
        3. SELECT -> plain relational query.

        Important:
        Do NOT terminate on a generic END because CASE expressions
        contain END as well. RETURN (or COMMIT / ROLLBACK) is the
        reliable boundary for the supported procedure pattern.
        """

        merge_match = re.search(
            r"\bMERGE\b",
            sql,
            flags=re.IGNORECASE,
        )

        insert_match = re.search(
            r"\bINSERT\s+INTO\b|\bINSERT\s+(?!\()",
            sql,
            flags=re.IGNORECASE,
        )

        dml_candidates = [m for m in (merge_match, insert_match) if m]

        if dml_candidates:
            start = min(m.start() for m in dml_candidates)
            query = sql[start:]

            for terminator in (r"\bRETURN\b", r"\bCOMMIT\b", r"\bROLLBACK\b"):
                term_match = re.search(terminator, query, flags=re.IGNORECASE)
                if term_match:
                    query = query[: term_match.start()]

            query = re.sub(
                r"\bEND\s*;?\s*$",
                "",
                query,
                flags=re.IGNORECASE | re.DOTALL,
            )

            return query.strip().rstrip(";").strip()

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

        # T-SQL data types in CAST/CONVERT target positions become
        # their Spark SQL equivalents (e.g. nvarchar(20) -> STRING).
        translated = self._TSQL_TYPE_AS_RE.sub(
            self._tsql_type_replacement,
            translated,
        )

        # Delta MERGE syntax: T-SQL "WHEN NOT MATCHED BY TARGET"
        # becomes "WHEN NOT MATCHED".
        translated = re.sub(
            r"\bNOT\s+MATCHED\s+BY\s+TARGET\b",
            "NOT MATCHED",
            translated,
            flags=re.IGNORECASE,
        )

        # Change-detection subqueries become NULL-safe comparisons.
        translated = self._EXISTS_EXCEPT_RE.sub(
            self._exists_except_replacement,
            translated,
        )

        return translated

    @classmethod
    def _tsql_type_replacement(
        cls,
        match: re.Match,
    ) -> str:
        type_name = match.group("type").lower()
        mapped = cls.TSQL_TYPE_MAP.get(type_name)

        if mapped is None:
            return match.group(0)

        if type_name == "numeric":
            # Preserve the (precision, scale) arguments.
            args = match.group("args") or ""
            return f"{match.group('as')}{mapped}{args}"

        return f"{match.group('as')}{mapped}"

    @classmethod
    def _exists_except_replacement(
        cls,
        match: re.Match,
    ) -> str:
        left_columns = [
            column.strip()
            for column in match.group("left").split(",")
        ]

        right_columns = [
            column.strip()
            for column in match.group("right").split(",")
        ]

        if len(left_columns) != len(right_columns):
            # Column lists must align; if they do not, keep the
            # original expression so the failure is visible.
            return match.group(0)

        comparisons = [
            f"{left} IS DISTINCT FROM {right}"
            for left, right in zip(left_columns, right_columns)
        ]

        return "(" + " OR ".join(comparisons) + ")"

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
