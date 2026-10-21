from __future__ import annotations

from typing import Any

from .sql_semantic import (
    analyze_sql,
    extract_stored_procedures,
)


def _classify_by_name(proc_name: str) -> str:
    """Name-based heuristic for stored procedure classification."""
    name_lower = proc_name.lower()
    if "lineagekey" in name_lower:
        return "lineage_key"
    if "etlcutofftime" in name_lower:
        return "cutoff_time"
    if "migratestaged" in name_lower:
        return "migrate_staged"
    if "populatedate" in name_lower:
        return "populate_dimension"
    return "unresolved"


def _classify_by_body(body: str) -> str:
    """Classify a procedure by its SQL features."""
    semantic = analyze_sql(body)
    features = semantic.get("features", [])

    if "MERGE" in features:
        return "migrate_staged"
    if "INSERT" in features and "SELECT" in features:
        return "migrate_staged"
    if "INSERT" in features:
        return "write"
    if "UPDATE" in features:
        return "write"
    if "DELETE" in features:
        return "truncate_staging"
    if "SELECT" in features:
        return "read"
    return "control"


def _classify_direct_sql(sql: str) -> str:
    """Classify direct SQL (no EXEC) by its features."""
    semantic = analyze_sql(sql)
    features = semantic.get("features", [])

    if "DELETE" in features:
        return "truncate_staging"
    if "INSERT" in features or "MERGE" in features:
        return "write"
    if "UPDATE" in features:
        return "write"
    if "SELECT" in features:
        return "read"
    return "control"


def classify_sql_task(
    task_name: str,
    sql: str,
    procedure_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Classify a single Execute SQL task.

    Returns a dict with task_name, sql_preview, classification,
    procedures, unresolved_procedures, procedure_classifications.
    """
    procedure_bodies = procedure_bodies or {}

    result: dict[str, Any] = {
        "task_name": task_name,
        "sql_preview": (sql or "")[:200],
        "classification": "UNKNOWN",
        "procedures": [],
        "unresolved_procedures": [],
        "procedure_classifications": {},
    }

    if not sql:
        result["classification"] = "empty"
        return result

    procs = extract_stored_procedures(sql)
    result["procedures"] = procs

    if not procs:
        result["classification"] = _classify_direct_sql(sql)
        return result

    for proc_name in procs:
        body = procedure_bodies.get(proc_name)

        if body is None:
            for key, val in procedure_bodies.items():
                if key.lower() == proc_name.lower():
                    body = val
                    break

        if body is None:
            result["unresolved_procedures"].append(proc_name)
            result["procedure_classifications"][proc_name] = (
                _classify_by_name(proc_name)
            )
            continue

        body_class = _classify_by_body(body)
        name_class = _classify_by_name(proc_name)

        if name_class != "unresolved":
            result["procedure_classifications"][proc_name] = name_class
        else:
            result["procedure_classifications"][proc_name] = body_class

    classifications = list(result["procedure_classifications"].values())
    if classifications:
        result["classification"] = classifications[0]
    elif result["unresolved_procedures"]:
        result["classification"] = "unresolved"

    return result


def classify_execute_sql_tasks(
    executables: list[dict[str, Any]],
    procedure_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Classify all Execute SQL tasks in the package.

    Returns task_count, classification_counts,
    unresolved_procedures (sorted unique),
    unresolved_count, tasks (list of per-task results).
    """
    tasks: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    classification_counts: dict[str, int] = {}

    for exe in executables:
        if exe.get("executable_type") != "Microsoft.ExecuteSQLTask":
            continue

        task_name = exe.get("name", "")
        sql = (exe.get("properties") or {}).get("SqlStatementSource", "")

        result = classify_sql_task(
            task_name=task_name,
            sql=sql,
            procedure_bodies=procedure_bodies,
        )
        tasks.append(result)

        cls = result["classification"]
        classification_counts[cls] = (
            classification_counts.get(cls, 0) + 1
        )

        for proc in result["unresolved_procedures"]:
            unresolved.add(proc)

    return {
        "task_count": len(tasks),
        "classification_counts": classification_counts,
        "unresolved_procedures": sorted(unresolved),
        "unresolved_count": len(unresolved),
        "tasks": tasks,
    }