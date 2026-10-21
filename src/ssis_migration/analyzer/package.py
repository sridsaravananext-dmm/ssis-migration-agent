from __future__ import annotations

from typing import Any

from .data_flow import analyze_data_flow
from .dependency import analyze_dependencies
from .load_strategy import detect_load_strategy
from .control_flow import analyze_control_flow
from .sql_task_classifier import classify_execute_sql_tasks

def analyze_package(
    package: dict[str, Any],
    procedure_bodies: dict[str, str] | None = None,
) -> dict[str, Any]:

    data_flows = package.get(
        "data_flows",
        []
    )

    analyzed_flows = []

    for flow in data_flows:

        analyzed = analyze_data_flow(flow)

        analyzed["load_strategy"] = (
            detect_load_strategy(
                analyzed
            )
        )

        analyzed_flows.append(
            analyzed
        )

    component_types = {}

    for flow in analyzed_flows:

        for component in flow[
            "components"
        ]:

            component_type = component[
                "type"
            ]

            component_types[
                component_type
            ] = (
                component_types.get(
                    component_type,
                    0
                ) + 1
            )

    dependency_analysis = (
        analyze_dependencies(
            package
        )
    )

    sql_task_analysis = classify_execute_sql_tasks(
        executables=package.get("executables", []),
        procedure_bodies=procedure_bodies,
    )

    return {
        "package_name": package.get("package_name"),
        "source_file": package.get("source_file"),

        "summary": {
            "executables": len(package.get("executables", [])),
            "data_flows": len(data_flows),
            "components": sum(
                flow["component_count"]
                for flow in analyzed_flows
            ),
            "mappings": sum(
                flow["mapping_count"]
                for flow in analyzed_flows
            ),
            "precedence_constraints": len(
                package.get("precedence_constraints", [])
            ),
            "variables": len(package.get("variables", [])),
            "connection_references": len(
                package.get(
                    "connection_manager_references",
                    []
                )
            ),
            "execute_sql_tasks": sql_task_analysis["task_count"],
        },

        "component_type_counts": component_types,

        "control_flow": analyze_control_flow(package),

        "dependencies": dependency_analysis,

        "sql_tasks": sql_task_analysis,

        "data_flows": analyzed_flows,
    }