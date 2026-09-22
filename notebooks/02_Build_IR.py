from pathlib import Path
import json
import sys


# ============================================================
# Repository setup
# ============================================================

CURRENT_FILE = Path(__file__).resolve()
REPO_ROOT = CURRENT_FILE.parent.parent

SRC_PATH = REPO_ROOT / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


# ============================================================
# Imports
# ============================================================

from ssis_migration.ir import IRBuilder


# ============================================================
# Input / Output
# ============================================================

PARSED_FILE = (
    REPO_ROOT
    / "examples"
    / "wwi"
    / "parsed.json"
)

IR_FILE = (
    REPO_ROOT
    / "examples"
    / "wwi"
    / "package_ir.json"
)


# ============================================================
# Load parser output
# ============================================================

if not PARSED_FILE.exists():
    raise FileNotFoundError(
        f"Parsed JSON not found: {PARSED_FILE}"
    )

with PARSED_FILE.open(
    "r",
    encoding="utf-8",
) as f:

    parsed_package = json.load(f)


# ============================================================
# Build IR
# ============================================================

builder = IRBuilder()

package_ir = builder.build(
    parsed_package
)


# ============================================================
# Save IR
# ============================================================

with IR_FILE.open(
    "w",
    encoding="utf-8",
) as f:

    json.dump(
        package_ir.to_dict(),
        f,
        indent=2,
        ensure_ascii=False,
    )


# ============================================================
# Validation / Summary
# ============================================================

print()
print("======================================")
print("UNIVERSAL IR BUILD")
print("======================================")

print(
    f"Package       : {package_ir.package_name}"
)

print(
    f"Executables   : {len(package_ir.executables)}"
)

print(
    f"Constraints   : "
    f"{len(package_ir.precedence_constraints)}"
)

print(
    f"Variables     : "
    f"{len(package_ir.variables)}"
)

print(
    f"Connections   : "
    f"{len(package_ir.connection_manager_references)}"
)

data_flow_count = len(
    package_ir.data_flows
)

component_count = sum(
    len(flow.components)
    for flow in package_ir.data_flows
)

mapping_count = sum(
    len(flow.column_mappings)
    for flow in package_ir.data_flows
)

print(
    f"Data Flows    : {data_flow_count}"
)

print(
    f"Components    : {component_count}"
)

print(
    f"Mappings      : {mapping_count}"
)

print()
print(f"IR written to:")
print(IR_FILE)