from __future__ import annotations

from typing import Any

from .lineage import build_data_flow_lineage
from .column_mapping import analyze_column_mappings
from .transformation import analyze_component
from .transformation_semantics import analyze_transformation_semantics


def analyze_data_flow(data_flow: dict[str, Any]) -> dict[str, Any]:
    components = data_flow.get("components", [])

    analyzed_components = []
    sources = []
    destinations = []
    transformations = []

    for component in components:
        classification = analyze_component(component)
        semantics = analyze_transformation_semantics(component)

        analyzed = {
            **classification,
            "semantics": semantics.get("semantics", {}),
            "evidence": semantics.get("evidence", {}),
        }

        analyzed_components.append(analyzed)

        category = classification.get("category")

        if category == "SOURCE":
            sources.append(analyzed)

        elif category == "DESTINATION":
            destinations.append(analyzed)

        elif category == "TRANSFORMATION":
            transformations.append(analyzed)

    # IMPORTANT: these must be outside the component loop.
    column_mappings = analyze_column_mappings(data_flow)
    lineage = build_data_flow_lineage(data_flow)

    return {
        "name": data_flow.get("name"),
        "ref_id": data_flow.get("ref_id"),

        "component_count": len(components),
        "path_count": len(data_flow.get("paths", [])),
        "mapping_count": len(column_mappings),

        "sources": sources,
        "destinations": destinations,
        "transformations": transformations,
        "components": analyzed_components,

        "column_mappings": column_mappings,
        "lineage": lineage.get("lineage", {}),
    }