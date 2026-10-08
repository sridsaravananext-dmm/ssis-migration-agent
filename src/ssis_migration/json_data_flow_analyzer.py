from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ============================================================
# Component classification
# ============================================================

SOURCE_TYPES = {
    "Microsoft.OLEDBSource",
    "Microsoft.FlatFileSource",
    "Microsoft.ADONETSource",
    "Microsoft.ExcelSource",
    "Microsoft.RawFileSource",
    "Microsoft.ODBCSource",
}

DESTINATION_TYPES = {
    "Microsoft.OLEDBDestination",
    "Microsoft.FlatFileDestination",
    "Microsoft.ADONETDestination",
    "Microsoft.ExcelDestination",
    "Microsoft.RawFileDestination",
    "Microsoft.ODBCDestination",
}


def classify_component(component: dict[str, Any]) -> str:
    """
    Classify a parsed SSIS component.

    This is based only on the component type present
    in the friend's JSON.
    """

    component_type = str(
        component.get("type") or ""
    )

    if component_type in SOURCE_TYPES:
        return "SOURCE"

    if component_type in DESTINATION_TYPES:
        return "DESTINATION"

    return "TRANSFORMATION"


# ============================================================
# Helpers
# ============================================================

def _component_name_from_ref(
    ref_id: str | None,
    components: list[dict[str, Any]],
) -> str | None:

    if not ref_id:
        return None

    component_names = [
        component.get("name")
        for component in components
        if component.get("name")
    ]

    # Longest first prevents accidental partial matches.
    component_names.sort(
        key=len,
        reverse=True,
    )

    for name in component_names:
        if name in ref_id:
            return name

    return None


def _is_error_output(
    output: dict[str, Any],
) -> bool:

    if output.get("is_error_output") is True:
        return True

    name = str(
        output.get("name") or ""
    ).lower()

    return "error" in name


# ============================================================
# Column mapping analysis
# ============================================================

def analyze_column_mappings(
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    mappings = []

    for component in components:

        destination_component = component.get(
            "name"
        )

        for input_data in component.get(
            "inputs",
            [],
        ):

            for destination_column in input_data.get(
                "columns",
                [],
            ):

                source_lineage_id = (
                    destination_column.get(
                        "lineage_id"
                    )
                )

                if not source_lineage_id:
                    continue

                source_column = _column_name_from_ref(
                    source_lineage_id
                )

                mappings.append(
                    {
                        "source_column":
                            source_column,

                        "source_lineage_id":
                            source_lineage_id,

                        "destination_component":
                            destination_component,

                        "destination_input":
                            input_data.get(
                                "name"
                            ),

                        "destination_column":
                            destination_column.get(
                                "name"
                            ),

                        "destination_column_ref":
                            destination_column.get(
                                "ref_id"
                            ),

                        "mapping_type":
                            (
                                "DIRECT"
                                if source_column
                                else "UNKNOWN"
                            ),

                        "evidence":
                            destination_column,
                    }
                )

    return mappings


def _column_name_from_ref(
    ref_id: str | None,
) -> str | None:

    if not ref_id:
        return None

    marker = ".Columns["

    if marker not in ref_id:
        return None

    return (
        ref_id
        .split(marker, 1)[1]
        .rstrip("]")
    )


# ============================================================
# Component analysis
# ============================================================

def analyze_component(
    component: dict[str, Any],
) -> dict[str, Any]:

    component_type = str(
        component.get("type") or "UNKNOWN"
    )

    category = classify_component(
        component
    )

    outputs = component.get(
        "outputs",
        [],
    )

    inputs = component.get(
        "inputs",
        [],
    )

    regular_outputs = [
        output
        for output in outputs
        if not _is_error_output(output)
    ]

    error_outputs = [
        output
        for output in outputs
        if _is_error_output(output)
    ]

    return {
        "name":
            component.get("name"),

        "type":
            component_type,

        "category":
            category,

        "ref_id":
            component.get("ref_id"),

        "description":
            component.get("description"),

        "properties":
            component.get(
                "properties",
                {},
            ),

        "inputs":
            inputs,

        "outputs":
            regular_outputs,

        "error_outputs":
            error_outputs,

        "input_count":
            len(inputs),

        "output_count":
            len(regular_outputs),

        "error_output_count":
            len(error_outputs),

        "connections":
            component.get(
                "connection_manager_refs",
                [],
            ),
    }


# ============================================================
# Path analysis
# ============================================================

def analyze_paths(
    pipeline: dict[str, Any],
) -> list[dict[str, Any]]:

    components = pipeline.get(
        "components",
        []
    )

    results = []

    for path in pipeline.get(
        "paths",
        []
    ):

        start_ref = path.get(
            "start"
        )

        end_ref = path.get(
            "end"
        )

        source_component = (
            _component_name_from_ref(
                start_ref,
                components,
            )
        )

        destination_component = (
            _component_name_from_ref(
                end_ref,
                components,
            )
        )

        results.append(
            {
                "name":
                    path.get("name"),

                "ref_id":
                    path.get("ref_id"),

                "from":
                    source_component,

                "to":
                    destination_component,

                "start":
                    start_ref,

                "end":
                    end_ref,

                "source_metadata":
                    path.get(
                        "source_metadata",
                        {},
                    ),
            }
        )

    return results


# ============================================================
# Lineage
# ============================================================

def analyze_lineage(
    components: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
) -> dict[str, Any]:

    nodes = []

    for component in components:

        nodes.append(
            {
                "component":
                    component.get(
                        "name"
                    ),

                "type":
                    component.get(
                        "type"
                    ),

                "category":
                    classify_component(
                        component
                    ),
            }
        )

    column_lineage = []

    for mapping in mappings:

        source_lineage_id = mapping.get(
            "source_lineage_id"
        )

        if not source_lineage_id:
            continue

        source_component = (
            _component_name_from_ref(
                source_lineage_id,
                components,
            )
        )

        column_lineage.append(
            {
                "source_component":
                    source_component,

                "source_column":
                    mapping.get(
                        "source_column"
                    ),

                "destination_component":
                    mapping.get(
                        "destination_component"
                    ),

                "destination_column":
                    mapping.get(
                        "destination_column"
                    ),

                "lineage_type":
                    mapping.get(
                        "mapping_type"
                    ),

                "source_lineage_id":
                    source_lineage_id,

                "destination_column_ref":
                    mapping.get(
                        "destination_column_ref"
                    ),
            }
        )

    return {
        "nodes": nodes,
        "column_lineage": column_lineage,
    }


# ============================================================
# Data Flow Analyzer
# ============================================================

def analyze_data_flow(
    pipeline: dict[str, Any],
) -> dict[str, Any]:

    raw_components = pipeline.get(
        "components",
        []
    )

    analyzed_components = [
        analyze_component(
            component
        )
        for component in raw_components
    ]

    sources = [
        component
        for component in analyzed_components
        if component["category"] == "SOURCE"
    ]

    transformations = [
        component
        for component in analyzed_components
        if component["category"] == "TRANSFORMATION"
    ]

    destinations = [
        component
        for component in analyzed_components
        if component["category"] == "DESTINATION"
    ]

    paths = analyze_paths(
        pipeline
    )

    mappings = analyze_column_mappings(
        raw_components
    )

    lineage = analyze_lineage(
        raw_components,
        mappings,
    )

    return {
        "name":
            pipeline.get("name"),

        "version":
            pipeline.get("version"),

        "component_count":
            len(raw_components),

        "path_count":
            len(paths),

        "mapping_count":
            len(mappings),

        "sources":
            sources,

        "transformations":
            transformations,

        "destinations":
            destinations,

        "components":
            analyzed_components,

        "paths":
            paths,

        "column_mappings":
            mappings,

        "lineage":
            lineage,
    }


# ============================================================
# Find all Data Flow Tasks in friend's JSON
# ============================================================

def _find_pipelines(
    node: Any,
) -> list[dict[str, Any]]:

    pipelines = []

    if isinstance(node, dict):

        pipeline = node.get(
            "pipeline"
        )

        if isinstance(
            pipeline,
            dict,
        ):

            if (
                "components" in pipeline
                and "paths" in pipeline
            ):
                pipelines.append(
                    {
                        "name":
                            node.get(
                                "name"
                            ),

                        "ref_id":
                            node.get(
                                "ref_id"
                            ),

                        "pipeline":
                            pipeline,
                    }
                )

        for value in node.values():

            pipelines.extend(
                _find_pipelines(
                    value
                )
            )

    elif isinstance(node, list):

        for item in node:

            pipelines.extend(
                _find_pipelines(
                    item
                )
            )

    return pipelines


def analyze_package_json(
    package_json: dict[str, Any],
) -> dict[str, Any]:

    pipeline_tasks = _find_pipelines(
        package_json
    )

    data_flows = []

    for task in pipeline_tasks:

        analysis = analyze_data_flow(
            task["pipeline"]
        )

        analysis[
            "task_name"
        ] = task["name"]

        analysis[
            "task_ref_id"
        ] = task["ref_id"]

        data_flows.append(
            analysis
        )

    return {
        "package_name":
            package_json.get(
                "name"
            ),

        "data_flow_count":
            len(data_flows),

        "data_flows":
            data_flows,
    }


# ============================================================
# Command-line entry point
# ============================================================

if __name__ == "__main__":

    input_file = Path(
        "test_data/test_01_parsed_package.json"
    )

    output_file = Path(
        "test_data/test_result_data_flow_analysis.json"
    )

    with input_file.open(
        "r",
        encoding="utf-8",
    ) as file:

        package_json = json.load(
            file
        )

    result = analyze_package_json(
        package_json
    )

    with output_file.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            result,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print(
        "======================================"
    )
    print(
        "Data-flow analysis completed."
    )
    print(
        f"Output: {output_file}"
    )
    print(
        f"Data flows analyzed: "
        f"{result['data_flow_count']}"
    )
    print(
        "======================================"
    )