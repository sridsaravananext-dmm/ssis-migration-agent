from pathlib import Path
import json
import sys


# ============================================================
# Repository
# ============================================================

CURRENT_FILE = Path(__file__).resolve()
REPO_ROOT = CURRENT_FILE.parent.parent

SRC_PATH = REPO_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


# ============================================================
# Imports
# ============================================================

from ssis_migration.analyzer import analyze_package


# ============================================================
# Files
# ============================================================

IR_FILE = (
    REPO_ROOT
    / "examples"
    / "wwi"
    / "package_ir.json"
)

ANALYSIS_FILE = (
    REPO_ROOT
    / "examples"
    / "wwi"
    / "analysis.json"
)


# ============================================================
# Load IR
# ============================================================

with IR_FILE.open(
    "r",
    encoding="utf-8",
) as f:

    package_ir = json.load(f)


# ============================================================
# Analyze
# ============================================================

analysis = analyze_package(
    package_ir
)


# ============================================================
# Save
# ============================================================

with ANALYSIS_FILE.open(
    "w",
    encoding="utf-8",
) as f:

    json.dump(
        analysis,
        f,
        indent=2,
        ensure_ascii=False,
    )


# ============================================================
# Summary
# ============================================================

print()
print("======================================")
print("SSIS DETERMINISTIC ANALYSIS")
print("======================================")

print(
    f"Package       : "
    f"{analysis['package_name']}"
)

summary = analysis["summary"]

print(
    f"Executables   : "
    f"{summary['executables']}"
)

print(
    f"Data Flows    : "
    f"{summary['data_flows']}"
)

print(
    f"Components    : "
    f"{summary['components']}"
)

print(
    f"Mappings      : "
    f"{summary['mappings']}"
)

print(
    f"Constraints   : "
    f"{summary['precedence_constraints']}"
)

print(
    f"Variables     : "
    f"{summary['variables']}"
)

print()
print("COMPONENT TYPES")
print("--------------------------------------")

for component_type, count in sorted(
    analysis[
        "component_type_counts"
    ].items()
):

    print(
        f"{component_type:<30} "
        f"{count}"
    )

print()
print(
    f"Analysis written to:"
)

print(
    ANALYSIS_FILE
)

print()
print("LOAD STRATEGIES")
print("--------------------------------------")

load_strategies = {}

for flow in analysis["data_flows"]:

    strategy = flow[
        "load_strategy"
    ]["strategy"]

    load_strategies[strategy] = (
        load_strategies.get(
            strategy,
            0
        ) + 1
    )

for strategy, count in sorted(
    load_strategies.items()
):

    print(
        f"{strategy:<30} {count}"
    )


print()
print("DEPENDENCY GRAPH")
print("--------------------------------------")

print(
    f"Nodes : "
    f"{analysis['dependencies']['node_count']}"
)

print(
    f"Edges : "
    f"{analysis['dependencies']['edge_count']}"
)

control_flow = analysis["control_flow"]

print()
print("CONTROL FLOW")
print("--------------------------------------")
print(
    f"Nodes       : {control_flow['node_count']}"
)
print(
    f"Tasks       : {control_flow['task_count']}"
)
print(
    f"Containers  : {control_flow['container_count']}"
)
print(
    f"Edges       : {control_flow['edge_count']}"
)

print()
print("TRANSFORMATION SEMANTICS")
print("--------------------------------------")

for flow in analysis.get("data_flows", []):
    print()
    print(f"DATA FLOW: {flow.get('name')}")

    for component in flow.get("components", []):
        semantics = component.get("semantics", {})
        operation = semantics.get("semantics", {}).get("operation")

        print(
            f"  {component.get('name')}"
            f" [{component.get('type')}]"
            f" -> {operation}"
        )

print()
print("COLUMN LINEAGE")
print("--------------------------------------")

for flow in analysis.get("data_flows", []):
    mappings = flow.get("column_mappings", [])

    print()
    print(
        f"DATA FLOW: {flow.get('name')}"
        f" ({len(mappings)} mappings)"
    )

    for mapping in mappings:
        print(
            f"  {mapping.get('source_column')}"
            f" -> "
            f"{mapping.get('destination_column')}"
            f" [{mapping.get('mapping_type')}]"
        )