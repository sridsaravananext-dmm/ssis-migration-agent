from __future__ import annotations

from typing import Any

from ssis_migration.analyzer.sql_semantic import analyze_sql

from ssis_migration.planner.migration_strategy import (
    determine_migration_strategy,
)

from ssis_migration.context.migration_context import (
    MigrationContext,
)
from ssis_migration.planner.models import (
    MigrationPlan,
    MigrationStep,
)

from ssis_migration.planner.procedure_semantics import (
    analyze_procedure,
)

from ssis_migration.planner.migration_semantics import (
    analyze_migration_semantics,
)

from ssis_migration.planner.destination_semantics import (
    analyze_destination_procedure,
)

from dataclasses import asdict


class MigrationPlanner:

    def build_plan(
        self,
        context: MigrationContext,
    ) -> MigrationPlan:

        plan = MigrationPlan(
            package_name=(
                context.package_ir.package_name
            ),
            metadata={
                "planner": "deterministic",
                "model_version": "1.0",
            },
        )

        self._build_logical_loads(
            context,
            plan,
        )

        return plan

    # =========================================================
    # LOGICAL LOAD DETECTION
    # =========================================================

    def _build_logical_loads(
    self,
    context: MigrationContext,
        plan: MigrationPlan,
    ) -> None:

        package = context.package_ir

        for executable in package.executables:

            if executable.executable_type != (
                "STOCK:SEQUENCE"
            ):
                continue

            load_name = executable.name

            if not load_name:
                continue

            load_ref = executable.ref_id

            children = [
                child
                for child in package.executables
                if child.ref_id
                and load_ref
                and child.ref_id.startswith(
                    load_ref + "\\"
                )
            ]

            source_components = []
            target_components = []
            source_objects = []
            target_objects = []
            destination_target_objects = []
            transformations = []
            operations = []

            # -------------------------------------------------
            # Analyze control-flow operations
            # -------------------------------------------------

            for child in children:

                name = child.name or ""

                if "Set TableName" in name:
                    operations.append(
                        "SET_TABLE_NAME"
                    )

                elif (
                    "Lineage Key" in name
                    or "Get Lineage" in name
                ):
                    operations.append(
                        "GET_LINEAGE_KEY"
                    )

                elif (
                    "Truncate" in name
                    or "Delete" in name
                ):
                    operations.append(
                        "CLEAR_STAGING"
                    )

                elif (
                    "Last" in name
                    and "Cutoff" in name
                ):
                    operations.append(
                        "GET_LAST_CUTOFF"
                    )

                elif (
                    "Extract" in name
                    and "Staging" in name
                ):
                    operations.append(
                        "EXTRACT_TO_STAGING"
                    )

                elif "Migrate Staged" in name:
                    operations.append(
                        "MIGRATE_STAGED_DATA"
                    )

            # -------------------------------------------------
            # Find data flows belonging to this load
            # -------------------------------------------------

            for flow in package.data_flows:

                if not load_ref:
                    continue

                flow_belongs_to_load = any(
                    component.ref_id
                    and component.ref_id.startswith(
                        load_ref + "\\"
                    )
                    for component in flow.components
                )

                if not flow_belongs_to_load:
                    continue

                for component in flow.components:

                    detected_type = (
                        component.detected_type
                    )

                    if detected_type == "OLE_DB_SOURCE":

                        source_components.append(
                            component.name
                        )

                    elif detected_type == "OLE_DB_DESTINATION":

                        target_components.append(
                            component.name
                        )

                        properties = component.properties or {}

                        target_object = properties.get(
                            "OpenRowset"
                        )

                        if target_object:
                            destination_target_objects.append(
                                target_object
                            )

            # -------------------------------------------------
            # Resolve database objects
            # -------------------------------------------------

            for database in context.databases:

                for obj in database.relevant_objects:

                    object_name = obj.get("name")
                    schema = obj.get("schema")

                    if not object_name:
                        continue

                    qualified_name = (
                        f"{schema}.{object_name}"
                        if schema
                        else object_name
                    )

                    # -----------------------------------------
                    # Source database objects
                    # -----------------------------------------

                    if database.role == "source":

                        source_object_matches = False

                        for flow in package.data_flows:

                            if not load_ref:
                                continue

                            flow_belongs_to_load = any(
                                component.ref_id
                                and component.ref_id.startswith(
                                    load_ref + "\\"
                                )
                                for component in flow.components
                            )

                            if not flow_belongs_to_load:
                                continue

                            for component in flow.components:

                                if (
                                    component.detected_type
                                    != "OLE_DB_SOURCE"
                                ):
                                    continue

                                properties = (
                                    component.properties or {}
                                )

                                sql = (
                                    properties.get("SqlCommand")
                                    or properties.get(
                                        "SqlCommandVariable"
                                    )
                                )

                                if not sql:
                                    continue

                                semantic = analyze_sql(sql)

                                referenced_objects = {
                                    self._normalize_database_object_name(
                                        value
                                    )
                                    for value in semantic.get(
                                        "referenced_objects",
                                        [],
                                    )
                                }

                                stored_procedures = {
                                    self._normalize_database_object_name(
                                        value
                                    )
                                    for value in semantic.get(
                                        "stored_procedures",
                                        [],
                                    )
                                }

                                normalized_object = (
                                    self._normalize_database_object_name(
                                        qualified_name
                                    )
                                )

                                if (
                                    normalized_object
                                    in referenced_objects
                                    or normalized_object
                                    in stored_procedures
                                ):
                                    source_object_matches = True
                                    break

                            if source_object_matches:
                                break

                        if source_object_matches:
                            if not any(
                                existing.lower()
                                == qualified_name.lower()
                                for existing in source_objects
                            ):
                                source_objects.append(
                                    qualified_name
                                )

                    # -----------------------------------------
                    # Destination database objects
                    # -----------------------------------------

                    elif database.role == "destination":

                        normalized_target_objects = {
                            self._normalize_database_object_name(
                                target
                            )
                            for target in destination_target_objects
                        }

                        normalized_qualified_name = (
                            self._normalize_database_object_name(
                                qualified_name
                            )
                        )

                        if normalized_qualified_name in (
                            normalized_target_objects
                        ):
                            target_objects.append(
                                qualified_name
                            )
            # -------------------------------------------------
            # Classify load
            # -------------------------------------------------

            load_type = self._classify_load(
                load_name
            )

            # -------------------------------------------------
            # Analyze source procedure semantics
            # -------------------------------------------------

            procedure_semantics = []

            for database in context.databases:

                if database.role != "source":
                    continue

                for obj in database.relevant_objects:

                    if obj.get("object_type") != "PROCEDURE":
                        continue

                    object_name = obj.get("name")

                    schema = obj.get("schema")

                    qualified_name = (
                        f"{schema}.{object_name}"
                        if schema
                        else object_name
                    )

                    if qualified_name.lower() not in {
                        source_object.lower()
                        for source_object in source_objects
                    }:
                        continue

                    known_objects = set()

                    for database_object in database.relevant_objects:

                        schema = database_object.get("schema")
                        name = database_object.get("name")

                        if not name:
                            continue

                        if schema:
                            known_objects.add(
                                f"{schema}.{name}"
                            )
                        else:
                            known_objects.add(name)

                    procedure_analysis = analyze_procedure(
                        obj,
                        known_objects=known_objects,
                    )

                    procedure_analysis["migration_semantics"] = (
                        analyze_migration_semantics(
                            procedure_analysis
                        )
                    )

                    procedure_semantics.append(
                        procedure_analysis
                    )

            # -------------------------------------------------
            # Analyze destination procedures
            # -------------------------------------------------

            destination_procedure_semantics = []

            # Build an index of destination procedures.
            destination_procedures = {}

            for database in context.databases:

                if database.role != "destination":
                    continue

                for obj in database.relevant_objects:

                    if obj.get("object_type") != "PROCEDURE":
                        continue

                    schema = obj.get("schema")
                    name = obj.get("name")

                    if not name:
                        continue

                    qualified_name = (
                        f"{schema}.{name}"
                        if schema
                        else name
                    )

                    destination_procedures[
                        self._normalize_name(
                            qualified_name
                        )
                    ] = obj

                    destination_procedures[
                        self._normalize_name(name)
                    ] = obj

            # Inspect Execute SQL Tasks belonging to this load.
            for child in children:

                if child.executable_type != (
                    "Microsoft.ExecuteSQLTask"
                ):
                    continue

                properties = child.properties or {}

                sql_statement = properties.get(
                    "SqlStatementSource"
                )

                if not sql_statement:
                    continue

                semantic = analyze_sql(
                    sql_statement
                )

                for procedure_name in semantic.get(
                    "stored_procedures",
                    [],
                ):

                    normalized_procedure = (
                        self._normalize_name(
                            procedure_name
                        )
                    )

                    destination_procedure = (
                        destination_procedures.get(
                            normalized_procedure
                        )
                    )

                    if destination_procedure is None:
                        continue

                    analysis = analyze_destination_procedure(
                        destination_procedure
                    )

                    if analysis.get("procedure_role") == "ORCHESTRATION":
                        continue

                    analysis["execution"] = {
                        "task_name": child.name,
                        "task_ref_id": child.ref_id,
                        "sql_statement": sql_statement,
                        "procedure": procedure_name,
                    }

                    destination_procedure_semantics.append(
                        analysis
                    )
            
            # -------------------------------------------------
            # Capture data-flow semantics
            # -------------------------------------------------

            flow_semantics = []

            for flow in package.data_flows:

                if not load_ref:
                    continue

                flow_belongs_to_load = any(
                    component.ref_id
                    and component.ref_id.startswith(
                        load_ref + "\\"
                    )
                    for component in flow.components
                )

                if not flow_belongs_to_load:
                    continue

                source_info = []
                destination_info = []

                for component in flow.components:

                    component_data = asdict(
                        component
                    )

                    component_type = (
                        component_data.get(
                            "detected_type"
                        )
                    )

                    if component_type == "OLE_DB_SOURCE":

                        source_info.append(
                            {
                                "name": component.name,
                                "component_type": component_type,
                                "properties": component_data.get(
                                    "properties",
                                    {},
                                ),
                                "connections": component_data.get(
                                    "connection_manager_refs",
                                    [],
                                ),
                                "outputs": component_data.get(
                                    "output_columns",
                                    [],
                                ),
                            }
                        )

                    elif component_type == "OLE_DB_DESTINATION":

                        destination_info.append(
                            {
                                "name": component.name,
                                "component_type": component_type,
                                "properties": component_data.get(
                                    "properties",
                                    {},
                                ),
                                "connections": component_data.get(
                                    "connection_manager_refs",
                                    [],
                                ),
                                "inputs": component_data.get(
                                    "input_columns",
                                    [],
                                ),
                            }
                        )

                flow_semantics.append(
                    {
                        "name": flow.name,
                        "source": source_info,
                        "destination": destination_info,
                        "mappings": [
                            {
                                "destination_component": (
                                    mapping.destination_component
                                ),
                                "destination_input": (
                                    mapping.destination_input
                                ),
                                "destination_column": (
                                    mapping.destination_column
                                ),
                                "destination_column_ref": (
                                    mapping.destination_column_ref
                                ),
                                "source_lineage_id": (
                                    mapping.source_lineage_id
                                ),
                                "external_metadata_column_id": (
                                    mapping.external_metadata_column_id
                                ),
                            }
                            for mapping in flow.column_mappings
                        ],
                        "mapping_count": len(
                            flow.column_mappings
                        ),
                    }
                )

            # -------------------------------------------------
            # Build migration step
            # -------------------------------------------------

            migration_intent = self._build_migration_intent(
                load_name=load_name,
                load_type=load_type,
                source_objects=source_objects,
                target_objects=target_objects,
                operations=operations,
                procedure_semantics=procedure_semantics,
                destination_procedure_semantics=(
                    destination_procedure_semantics
                ),
                flow_semantics=flow_semantics,
            )

            migration_strategy = determine_migration_strategy(
                migration_intent
            )    

            step = MigrationStep(
                step_id=self._make_step_id(
                    load_name
                ),
                name=load_name,
                step_type=load_type,
                source_components=(
                    self._unique(
                        source_components
                    )
                ),
                source_objects=(
                    self._unique(
                        source_objects
                    )
                ),
                target_components=(
                    self._unique(
                        target_components
                    )
                ),
                target_objects=(
                    self._unique(
                        target_objects
                    )
                ),
                transformations=transformations,
                metadata={
                    "operations": self._unique(
                        operations
                    ),
                    "ref_id": load_ref,
                    "data_flows": flow_semantics,
                    "procedure_semantics": procedure_semantics,
                    "destination_procedure_semantics": (
                        destination_procedure_semantics
                    ),
                    "migration_intent": migration_intent,
                    "migration_strategy": migration_strategy,
                },
            )

            plan.steps.append(step)

    # =========================================================
    # MIGRATION INTENT
    # =========================================================

    def _build_migration_intent(
        self,
        load_name: str,
        load_type: str,
        source_objects: list[str],
        target_objects: list[str],
        operations: list[str],
        procedure_semantics: list[dict[str, Any]],
        destination_procedure_semantics: list[dict[str, Any]],
        flow_semantics: list[dict[str, Any]],
    ) -> dict[str, Any]:

        source_intent = []

        for procedure in procedure_semantics:
            semantics = procedure.get(
                "migration_semantics",
                {},
            )

            source_intent.append(
                {
                    "procedure": (
                        f"{procedure.get('schema')}.{procedure.get('name')}"
                        if procedure.get("schema")
                        else procedure.get("name")
                    ),
                    "definition": procedure.get("definition"),
                    "pattern": semantics.get("pattern"),
                    "extraction_pattern": semantics.get(
                        "extraction_pattern"
                    ),
                    "execution_style": semantics.get(
                        "execution_style"
                    ),
                    "operations": semantics.get(
                        "operations",
                        [],
                    ),
                    "relational_features": semantics.get(
                        "relational_features",
                        [],
                    ),
                    "temporary_tables": semantics.get(
                        "temporary_tables",
                        [],
                    ),
                    "has_cursor": semantics.get(
                        "has_cursor",
                        False,
                    ),
                    "has_loop": semantics.get(
                        "has_loop",
                        False,
                    ),
                    "has_cutoff_parameters": semantics.get(
                        "has_cutoff_parameters",
                        False,
                    ),
                    "has_cutoff_filter": semantics.get(
                        "has_cutoff_filter",
                        False,
                    ),
                    "has_temporal_logic": semantics.get(
                        "has_temporal_logic",
                        False,
                    ),
                    "has_point_in_time_lookup": semantics.get(
                        "has_point_in_time_lookup",
                        False,
                    ),
                }
            )

        destination_intent = []

        for procedure in destination_procedure_semantics:
            destination_intent.append(
                {
                    "procedure": (
                        f"{procedure.get('schema')}.{procedure.get('name')}"
                        if procedure.get("schema")
                        else procedure.get("name")
                    ),
                    "role": procedure.get(
                        "procedure_role"
                    ),
                    "operations": procedure.get(
                        "features",
                        [],
                    ),
                    "target_objects": procedure.get(
                        "target_objects",
                        [],
                    ),
                }
            )

        flows = []

        for flow in flow_semantics:
            flows.append(
                {
                    "name": flow.get("name"),
                    "source_components": [
                        source.get("name")
                        for source in flow.get(
                            "source",
                            []
                        )
                    ],
                    "destination_components": [
                        destination.get("name")
                        for destination in flow.get(
                            "destination",
                            []
                        )
                    ],
                    "mapping_count": flow.get(
                        "mapping_count",
                        0,
                    ),
                }
            )

        return {
            "load_name": load_name,
            "load_type": load_type,
            "source_objects": self._unique(
                source_objects
            ),
            "target_objects": self._unique(
                target_objects
            ),
            "control_flow_operations": self._unique(
                operations
            ),
            "source_procedures": source_intent,
            "destination_procedures": destination_intent,
            "data_flows": flows,
        }
    
    # =========================================================
    # CLASSIFICATION
    # =========================================================

    def _classify_load(
        self,
        name: str,
    ) -> str:

        normalized = name.lower()

        if "dimension" in normalized:
            return "DIMENSION_LOAD"

        if "fact" in normalized:
            return "FACT_LOAD"

        return "DATA_LOAD"

    # =========================================================
    # MATCHING
    # =========================================================

    def _normalize_database_object_name(
    self,
    value: str,
    ) -> str:

        return (
            value
            .replace("[", "")
            .replace("]", "")
            .strip()
            .lower()
        )

    def _component_matches(
    self,
    component_name: str | None,
    object_name: str,
    ) -> bool:

        if not component_name or not object_name:
            return False

        component = self._normalize_name(
            component_name
        )

        object_parts = [
            part
            for part in object_name.replace(
                "[", ""
            ).replace(
                "]", ""
            ).split(".")
            if part
        ]

        object_name_only = self._normalize_name(
            object_parts[-1]
        )

        return component == object_name_only

    # =========================================================
    # HELPERS
    # =========================================================

    def _normalize_name(
        self,
        value: str,
    ) -> str:

        return (
            value
            .replace("_", "")
            .replace(" ", "")
            .replace("[", "")
            .replace("]", "")
            .lower()
        )

    def _make_step_id(
        self,
        name: str,
    ) -> str:

        return (
            name
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
        )

    def _unique(
        self,
        values: list[Any],
    ) -> list[Any]:

        result = []

        for value in values:

            if value and value not in result:
                result.append(value)

        return result