from __future__ import annotations

from typing import Any

from ssis_migration.analyzer.sql_semantic import analyze_sql
from ssis_migration.database.ir import DatabaseIR


def analyze_database_ir(database_ir: DatabaseIR) -> dict[str, Any]:
    """
    Deterministically analyze SQL definitions contained in DatabaseIR.

    No LLM is used.
    """

    objects = []

    for obj in database_ir.objects:
        semantic = analyze_sql(obj.definition)

        objects.append({
            "schema": obj.schema,
            "name": obj.name,
            "object_type": obj.object_type,
            "classification": semantic["classification"],
            "features": semantic["features"],
            "procedural_features": semantic["procedural_features"],
            "relational_features": semantic["relational_features"],
            "referenced_objects": semantic["referenced_objects"],
            "stored_procedures": semantic["stored_procedures"],
        })

    return {
        "database": database_ir.database,
        "object_count": len(objects),
        "objects": objects,
        "dependency_graph": database_ir.dependency_graph,
    }
