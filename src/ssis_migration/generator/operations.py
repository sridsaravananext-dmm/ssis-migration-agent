from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ssis_migration.planner.destination_semantics import (
    analyze_destination_procedure,
)


@dataclass
class MigrationOperation:
    operation_id: str
    operation_type: str
    name: str
    order: int

    source: list[str] = field(default_factory=list)
    target: list[str] = field(default_factory=list)

    sql: str | None = None

    dependencies: list[str] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MigrationOperationBuilder:

    def build(
        self,
        migration_step: dict[str, Any],
    ) -> list[MigrationOperation]:

        from ssis_migration.migration import prepare_generation_step

        migration_step = prepare_generation_step(migration_step)
        contract = migration_step.get("metadata", {}).get("migration_contract")
        if contract and contract["implementation_strategy"]["decision"] == "UNSUPPORTED":
            return []

        operations: list[MigrationOperation] = []
        order = 1

        metadata = migration_step.get("metadata", {})
        migration_intent = metadata.get(
            "migration_intent",
            {},
        )

        migration_strategy = metadata.get(
            "migration_strategy",
            {},
        )

        control_operations = migration_intent.get(
            "control_flow_operations",
            metadata.get("operations", []),
        )

        source_objects = migration_step.get(
            "source_objects",
            [],
        )

        target_objects = migration_step.get(
            "target_objects",
            [],
        )

        source_procedures = migration_intent.get(
            "source_procedures",
            [],
        )

        destination_procedures = migration_intent.get(
            "destination_procedures",
            [],
        )

        destination_semantics = metadata.get(
            "destination_procedure_semantics",
            [],
        )

        destination_semantics_by_name = {
            f"{item.get('schema')}.{item.get('name')}": item
            for item in destination_semantics
            if item.get("schema") and item.get("name")
        }

        control_mapping = {
            "SET_TABLE_NAME": (
                "set_table_name",
                "Set table name",
                "CONTROL",
            ),
            "GET_LAST_CUTOFF": (
                "get_last_cutoff",
                "Get last ETL cutoff",
                "CONTROL",
            ),
            "GET_LINEAGE_KEY": (
                "get_lineage_key",
                "Get lineage key",
                "CONTROL",
            ),
            "CLEAR_STAGING": (
                "clear_staging",
                "Clear staging table",
                "STAGING",
            ),
            "EXTRACT_TO_STAGING": (
                "extract_to_staging",
                "Extract source data",
                "EXTRACT",
            ),
        }

        previous_operation: str | None = None

        # ---------------------------------------------------------
        # 1. SSIS control-flow operations
        # ---------------------------------------------------------

        for control_name in [
            "SET_TABLE_NAME",
            "GET_LAST_CUTOFF",
            "GET_LINEAGE_KEY",
            "CLEAR_STAGING",
            "EXTRACT_TO_STAGING",
        ]:

            if control_name not in control_operations:
                continue

            operation_id, name, operation_type = control_mapping[
                control_name
            ]

            # -----------------------------------------------------
            # EXTRACT_TO_STAGING
            #
            # Preserve the actual source procedure definition and
            # all source-procedure semantics in the operation.
            # -----------------------------------------------------

            if control_name == "EXTRACT_TO_STAGING":

                source_procedure = (
                    source_procedures[0]
                    if source_procedures
                    else {}
                )

                source_procedure_name = source_procedure.get(
                    "procedure"
                )

                source_definition = source_procedure.get(
                    "definition"
                )

                operation = MigrationOperation(
                    operation_id=operation_id,
                    operation_type=operation_type,
                    name=name,
                    order=order,
                    source=(
                        [source_procedure_name]
                        if source_procedure_name
                        else source_objects
                    ),
                    target=target_objects,
                    sql=source_definition,
                    dependencies=(
                        [previous_operation]
                        if previous_operation
                        else []
                    ),
                    metadata={
                        "ssis_operation": control_name,
                        "step_id": migration_step.get(
                            "step_id"
                        ),
                        "step_name": migration_step.get(
                            "name"
                        ),
                        "source_procedure": source_procedure_name,
                        "source_procedure_definition": (
                            source_definition
                        ),
                        "source_procedure_semantics": (
                            source_procedure
                        ),
                        "source_objects": source_objects,
                        "target_objects": target_objects,
                        "migration_intent": migration_intent,
                        "migration_strategy": migration_strategy,
                    },
                )

            # -----------------------------------------------------
            # All other control-flow operations
            # -----------------------------------------------------

            else:

                operation = MigrationOperation(
                    operation_id=operation_id,
                    operation_type=operation_type,
                    name=name,
                    order=order,
                    source=source_objects,
                    target=target_objects,
                    dependencies=(
                        [previous_operation]
                        if previous_operation
                        else []
                    ),
                    metadata={
                        "ssis_operation": control_name,
                        "step_id": migration_step.get(
                            "step_id"
                        ),
                        "step_name": migration_step.get(
                            "name"
                        ),
                        "migration_strategy": migration_strategy,
                    },
                )

            operations.append(operation)

            previous_operation = operation_id
            order += 1

        # ---------------------------------------------------------
        # 2. Destination procedure semantic operations
        # ---------------------------------------------------------

        if "MIGRATE_STAGED_DATA" in control_operations:

            for procedure_index, procedure in enumerate(
                destination_procedures
            ):

                procedure_name = procedure.get(
                    "procedure"
                )

                semantic_procedure = (
                    destination_semantics_by_name.get(
                        procedure_name
                    )
                    if procedure_name
                    else None
                )

                if semantic_procedure:

                    procedure_for_analysis = {
                        **semantic_procedure,
                        "role": procedure.get(
                            "role",
                            semantic_procedure.get(
                                "procedure_role"
                            ),
                        ),
                    }

                else:
                    procedure_for_analysis = procedure

                definition = procedure_for_analysis.get(
                    "definition",
                    "",
                )

                procedure_analysis = (
                    analyze_destination_procedure(
                        procedure_for_analysis
                    )
                )

                semantic_operations = (
                    procedure_analysis.get(
                        "migration_operations",
                        [],
                    )
                )

                for semantic_index, semantic in enumerate(
                    semantic_operations
                ):

                    semantic_name = semantic.get(
                        "operation"
                    )

                    if not semantic_name:
                        continue

                    operation_id = semantic_name.lower()

                    existing_ids = {
                        operation.operation_id
                        for operation in operations
                    }

                    if operation_id in existing_ids:
                        operation_id = (
                            f"{operation_id}_"
                            f"{procedure_index + 1}"
                        )

                    target: list[str] = []

                    if semantic.get("target"):
                        target = [
                            semantic["target"]
                        ]

                    elif semantic.get("targets"):
                        target = semantic["targets"]

                    operation = MigrationOperation(
                        operation_id=operation_id,
                        operation_type=semantic.get(
                            "type",
                            "LOAD",
                        ),
                        name=_operation_display_name(
                            semantic_name
                        ),
                        order=order,
                        source=target_objects,
                        target=target,
                        dependencies=(
                            [previous_operation]
                            if previous_operation
                            else []
                        ),
                        sql=(
                            definition
                            if semantic_index == 0
                            else None
                        ),
                        metadata={
                            "step_id": migration_step.get(
                                "step_id"
                            ),
                            "migration_strategy": migration_strategy,
                            "step_name": migration_step.get(
                                "name"
                            ),
                            "semantic_operation": semantic_name,
                            "procedure": procedure_name,
                            "procedure_role": (
                                procedure_for_analysis.get(
                                    "role",
                                    procedure_for_analysis.get(
                                        "procedure_role"
                                    ),
                                )
                            ),
                            "features": semantic.get(
                                "features",
                                [],
                            ),
                            "description": semantic.get(
                                "description"
                            ),

                            # Preserve semantic information
                            # produced by destination analyzer.
                            "semantic": semantic,

                            # Preserve complete procedure context.
                            "procedure_definition": definition,

                            "procedure_analysis": (
                                procedure_analysis
                            ),
                        },
                    )

                    operations.append(operation)

                    previous_operation = operation_id
                    order += 1

        if "migration_contract" in metadata:
            for operation in operations:
                operation.metadata["migration_contract"] = metadata["migration_contract"]
        return operations


def build_migration_operations(
    blueprint: dict[str, Any],
) -> list[MigrationOperation]:

    builder = MigrationOperationBuilder()

    operations: list[MigrationOperation] = []

    steps = blueprint.get(
        "steps",
        [],
    )

    for step in steps:

        step_operations = builder.build(
            step
        )

        operations.extend(
            step_operations
        )

    return operations


def _operation_display_name(
    operation_name: str,
) -> str:

    names = {
        "RESOLVE_DIMENSION_KEYS":
            "Resolve dimension keys",

        "MERGE_TARGET":
            "Merge target",

        "COMPLETE_LINEAGE":
            "Complete lineage",

        "UPDATE_ETL_CUTOFF":
            "Update ETL cutoff",
    }

    return names.get(
        operation_name,
        operation_name.replace(
            "_",
            " ",
        ).title(),
    )
