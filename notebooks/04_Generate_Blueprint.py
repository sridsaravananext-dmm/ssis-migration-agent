from __future__ import annotations

import json
from pathlib import Path

from ssis_migration.parser.package_parser import SSISPackageParser
from ssis_migration.ir.builder import IRBuilder
from ssis_migration.analyzer.package import analyze_package
from ssis_migration.context.builder import build_migration_context
from ssis_migration.database.connection_mapping_loader import (
    load_connection_mapping,
)
from ssis_migration.planner.planner import MigrationPlanner


PACKAGE_PATH = Path(
    "examples/wwi/DailyETLMain.dtsx"
)

MAPPING_PATH = Path(
    "examples/wwi/connection_mapping.json"
)

OUTPUT_PATH = Path(
    "examples/wwi/migration_blueprint.json"
)


def main() -> None:

    print("1. Parsing SSIS package...")

    parsed = SSISPackageParser(
        PACKAGE_PATH
    ).parse()

    print("2. Building IR...")

    package_ir = IRBuilder().build(
        parsed
    )

    print("3. Analyzing SSIS package...")

    ssis_analysis = analyze_package(
        package_ir.to_dict()
    )

    print("4. Loading connection mappings...")

    connection_mapping = (
        load_connection_mapping(
            MAPPING_PATH
        )
    )

    print("5. Building migration context...")

    context = build_migration_context(
        package_ir=package_ir,
        ssis_analysis=ssis_analysis,
        connection_mapping=connection_mapping,
        base_path=".",
    )

    print("6. Building migration plan...")

    planner = MigrationPlanner()

    plan = planner.build_plan(
        context
    )

    print("7. Writing blueprint...")

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_PATH.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            plan.to_dict(),
            f,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print("BLUEPRINT GENERATED")
    print(
        f"Steps: {len(plan.steps)}"
    )
    print(
        f"Unresolved: {len(plan.unresolved)}"
    )
    print(
        f"Output: {OUTPUT_PATH}"
    )

    for step in plan.steps:
        print(
            f"\n{step.name}"
        )
        print(
            f"  Type: {step.step_type}"
        )
        print(
            f"  Source: {step.source_objects}"
        )
        print(
            f"  Target: {step.target_objects}"
        )


if __name__ == "__main__":
    main()