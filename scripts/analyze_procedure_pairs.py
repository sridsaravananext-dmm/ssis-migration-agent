from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope


# ============================================================
# Paths
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

INVENTORY = ROOT / "examples/wwi/procedure_pair_inventory.csv"
SOURCE_FILE = ROOT / "examples/wwi/database_metadata_wwi.json"
TARGET_FILE = ROOT / "examples/wwi/database_metadata_wwi_dw.json"
OUTPUT = ROOT / "examples/wwi/procedure_pair_evidence.csv"


# ============================================================
# Metadata helpers
# ============================================================

def load_json(path: Path) -> dict:
    """Load a JSON metadata file."""
    return json.loads(path.read_text(encoding="utf-8"))


def index_procedures(data: dict) -> dict[str, dict]:
    """Index procedures by schema-qualified name."""
    return {
        f"{procedure.get('schema', '')}.{procedure['name']}": procedure
        for procedure in data.get("procedures", [])
    }


def unique_dependency_names(proc: dict) -> list[str]:
    """Return unique dependency names from exported metadata."""
    names = set()

    for dependency in proc.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue

        schema = dependency.get("schema")
        name = dependency.get("name")

        if name:
            names.add(
                f"{schema}.{name}" if schema else name
            )

    return sorted(names)


# ============================================================
# Heuristic SQL reference extraction
# ============================================================

def extract_table_references(sql: str) -> list[str]:
    """
    Extract common two-part SQL Server object references.

    This is a heuristic, not proof of read/write behavior.
    It does not reliably resolve dynamic SQL, aliases, or every
    possible SQL Server syntax.
    """
    pattern = re.compile(
        r"\b(?:FROM|JOIN|INTO|UPDATE|MERGE\s+INTO|"
        r"DELETE\s+FROM|TRUNCATE\s+TABLE)"
        r"\s+"
        r"(?:\[\s*(?P<schema1>[^\]]+)\s*\]|(?P<schema2>\w+))"
        r"\s*\.\s*"
        r"(?:\[\s*(?P<table1>[^\]]+)\s*\]|(?P<table2>[\w ]+))",
        re.IGNORECASE,
    )

    results = set()

    for match in pattern.finditer(sql or ""):
        schema = match.group("schema1") or match.group("schema2")
        table = match.group("table1") or match.group("table2")

        if schema and table:
            results.add(f"{schema.strip()}.{table.strip()}")

    return sorted(results)


# ============================================================
# Scope-aware table extraction
# ============================================================

def extract_scoped_table_names(expression: exp.Expression) -> list[str]:
    """
    Return physical table candidates from successfully parsed SQL.

    Scope traversal distinguishes actual table sources from CTEs
    and derived subqueries. Unqualified names are retained as
    candidates because database context may be needed to resolve them.
    """
    tables = set()

    try:
        scopes = traverse_scope(expression)
    except Exception:
        return []

    for scope in scopes:
        try:
            selected_sources = scope.selected_sources
        except Exception:
            continue

        for _, (_, source) in selected_sources.items():
            if not isinstance(source, exp.Table):
                continue

            if not source.name:
                continue

            name = source.name
            schema = source.db

            tables.add(
                f"{schema}.{name}" if schema else name
            )

    return sorted(tables)


# ============================================================
# Cursor query extraction and analysis
# ============================================================

CURSOR_QUERY_PATTERN = re.compile(
    r"\bDECLARE\s+(\w+)\s+CURSOR\b.*?\bFOR\s+"
    r"(SELECT\b.*?);",
    re.IGNORECASE | re.DOTALL,
)


def analyze_cursor_queries(sql: str) -> dict:
    """
    Extract and parse cursor SELECT queries.

    Reports:
      - number of cursor queries found
      - number parsed successfully
      - number containing UNION ALL
      - scope-aware table candidates
      - parsing errors

    The results are evidence for review, not semantic proof.
    """
    matches = list(
        CURSOR_QUERY_PATTERN.finditer(sql or "")
    )

    parsed_count = 0
    union_all_count = 0
    table_candidates = set()
    parse_errors = []

    for match in matches:
        cursor_name = match.group(1)
        query = match.group(2)

        try:
            expression = sqlglot.parse_one(
                query,
                read="tsql",
            )

            if expression is None:
                raise ValueError("SQLGlot returned no expression")

            parsed_count += 1

            contains_union_all = any(
                isinstance(node, exp.Union)
                and node.args.get("distinct") is False
                for node in expression.walk()
            )

            if contains_union_all:
                union_all_count += 1

            # Collect actual table sources using SQLGlot scopes.
            table_candidates.update(
                extract_scoped_table_names(expression)
            )

        except Exception as exc:
            parse_errors.append(
                f"{cursor_name}: "
                f"{type(exc).__name__}: {exc}"
            )

    return {
        "found": len(matches),
        "parsed": parsed_count,
        "union_all": union_all_count,
        "tables": sorted(table_candidates),
        "errors": parse_errors,
    }


# ============================================================
# Main procedure-pair analysis
# ============================================================

def main() -> None:
    with INVENTORY.open(
        encoding="utf-8",
        newline="",
    ) as file:
        inventory = list(csv.DictReader(file))

    source_procedures = index_procedures(
        load_json(SOURCE_FILE)
    )

    target_procedures = index_procedures(
        load_json(TARGET_FILE)
    )

    rows = []

    for pair in inventory:
        source_name = pair["source_procedure"]
        target_name = pair["target_procedure"]

        source_proc = source_procedures.get(
            source_name,
            {},
        )

        target_proc = target_procedures.get(
            target_name,
            {},
        )

        source_sql = source_proc.get("definition") or ""
        target_sql = target_proc.get("definition") or ""

        source_deps = unique_dependency_names(
            source_proc
        )

        target_deps = unique_dependency_names(
            target_proc
        )

        source_refs = extract_table_references(
            source_sql
        )

        target_refs = extract_table_references(
            target_sql
        )

        cursor_evidence = analyze_cursor_queries(
            source_sql
        )

        staging_refs = [
            name
            for name in target_refs
            if "Staging" in name
        ]

        destination_refs = [
            name
            for name in target_refs
            if name.startswith(
                ("Dimension.", "Fact.")
            )
        ]

        rows.append({
            "source_procedure": source_name,
            "target_procedure": target_name,

            "source_definition_present": bool(source_sql),
            "target_definition_present": bool(target_sql),

            "source_dependency_objects": "; ".join(
                source_deps
            ),

            "target_dependency_objects": "; ".join(
                target_deps
            ),

            "source_definition_refs_heuristic": "; ".join(
                source_refs
            ),

            "target_definition_refs_heuristic": "; ".join(
                target_refs
            ),

            "candidate_staging_refs": "; ".join(
                staging_refs
            ),

            "candidate_dimension_fact_refs": "; ".join(
                destination_refs
            ),

            # Do not mark a pair verified based only on name
            # matching or heuristic SQL reference extraction.
            "verification_status": "REVIEW_REQUIRED",

            "source_cursor_queries_found": (
                cursor_evidence["found"]
            ),

            "source_cursor_queries_parsed": (
                cursor_evidence["parsed"]
            ),

            "source_cursor_union_all_count": (
                cursor_evidence["union_all"]
            ),

            "source_cursor_table_candidates": "; ".join(
                cursor_evidence["tables"]
            ),

            "source_cursor_parse_errors": "; ".join(
                cursor_evidence["errors"]
            ),
        })

    # Keep a stable CSV schema even if the inventory is empty.
    fields = [
        "source_procedure",
        "target_procedure",
        "source_definition_present",
        "target_definition_present",
        "source_dependency_objects",
        "target_dependency_objects",
        "source_definition_refs_heuristic",
        "target_definition_refs_heuristic",
        "candidate_staging_refs",
        "candidate_dimension_fact_refs",
        "verification_status",
        "source_cursor_queries_found",
        "source_cursor_queries_parsed",
        "source_cursor_union_all_count",
        "source_cursor_table_candidates",
        "source_cursor_parse_errors",
    ]

    with OUTPUT.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fields,
        )

        writer.writeheader()
        writer.writerows(rows)

    print("Pairs analyzed:", len(rows))
    print("Output:", OUTPUT)
    print(
        "All pairs remain REVIEW_REQUIRED; "
        "regex matches are not semantic proof."
    )


if __name__ == "__main__":
    main()
