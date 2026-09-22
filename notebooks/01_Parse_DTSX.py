from pathlib import Path
import sys
import json
import os

# ============================================================
# Repository / source path
# ============================================================

CURRENT_FILE = Path(__file__).resolve()
REPO_ROOT = CURRENT_FILE.parent.parent
SRC_PATH = REPO_ROOT / "src"

if not SRC_PATH.exists():
    raise RuntimeError(
        f"Source directory not found: {SRC_PATH}"
    )

sys.path.insert(0, str(SRC_PATH))

from ssis_migration.parser import SSISPackageParser


# ============================================================
# DTSX input resolution
# ============================================================

# Databricks Volume location
DATABRICKS_DTSX = Path(
    "/Volumes/migration_ai/raw/ssis_packages/"
    "wwi_demo/DailyETLMain.dtsx"
)

# Local Codespace test fixture
LOCAL_DTSX = (
    REPO_ROOT
    / "examples"
    / "wwi"
    / "DailyETLMain.dtsx"
)


def resolve_dtsx_file() -> Path:
    """
    Resolve the DTSX input from either:

    1. Databricks Unity Catalog Volume
    2. Local repository test fixture
    """

    if DATABRICKS_DTSX.exists():
        print("Using Databricks Volume input:")
        print(DATABRICKS_DTSX)
        return DATABRICKS_DTSX

    if LOCAL_DTSX.exists():
        print("Using local Codespace test fixture:")
        print(LOCAL_DTSX)
        return LOCAL_DTSX

    raise FileNotFoundError(
        "\nDTSX file not found.\n\n"
        "Checked Databricks Volume:\n"
        f"  {DATABRICKS_DTSX}\n\n"
        "Checked local repository:\n"
        f"  {LOCAL_DTSX}\n\n"
        "Place the DTSX file in examples/wwi/ for local testing "
        "or upload it to the Databricks Volume."
    )


# ============================================================
# Parse
# ============================================================

DTSX_FILE = resolve_dtsx_file()

print()
print(f"Repository : {REPO_ROOT}")
print(f"Source     : {SRC_PATH}")
print(f"DTSX       : {DTSX_FILE}")
print()


parser = SSISPackageParser(DTSX_FILE)

parsed_package = parser.parse()


# ============================================================
# Result
# ============================================================

print("======================================")
print("DTSX PARSING COMPLETED")
print("======================================")

print(
    json.dumps(
        parsed_package["statistics"],
        indent=2
    )
)

print()
print("======================================")
print("DATA FLOW SUMMARY")
print("======================================")

for index, pipeline in enumerate(
    parsed_package["data_flow_tasks"],
    start=1,
):

    print(
        f"\n[{index}] {pipeline['name']}"
    )

    print(
        f"  Components: "
        f"{len(pipeline['components'])}"
    )

    for component in pipeline["components"]:

        print(
            f"    - "
            f"{component['name']} "
            f"| "
            f"{component['detected_type']} "
            f"| "
            f"ConnectionRefs="
            f"{len(component['connection_manager_refs'])}"
        )

    print(
        f"  Paths: "
        f"{len(pipeline['paths'])}"
    )

    print()
print("======================================")
print("COMPONENT METADATA SAMPLE")
print("======================================")

pipeline = parsed_package["data_flow_tasks"][0]

for component in pipeline["components"]:

    print()
    print(f"COMPONENT: {component['name']}")
    print(f"TYPE     : {component['detected_type']}")
    print(
        f"CONNECTIONS: "
        f"{component['connection_manager_refs']}"
    )

    print("PROPERTIES:")

    for name, value in component["properties"].items():
        print(f"  {name} = {value}")

    print("INPUTS:")

    for item in component["inputs"]:
        print(f"  {item}")

    print("OUTPUTS:")

    for item in component["outputs"]:
        print(f"  {item}")

PARSED_OUTPUT = (
    REPO_ROOT
    / "examples"
    / "wwi"
    / "parsed.json"
)

with PARSED_OUTPUT.open(
    "w",
    encoding="utf-8",
) as f:

    json.dump(
        parsed_package,
        f,
        indent=2,
        ensure_ascii=False,
    )

print()
print(f"Parsed model written to: {PARSED_OUTPUT}")