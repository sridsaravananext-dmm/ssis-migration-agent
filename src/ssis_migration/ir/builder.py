from __future__ import annotations

from typing import Any

from .models import (
    PackageIR,
    ExecutableIR,
    DataFlowIR,
    ComponentIR,
    ConnectionReferenceIR,
    MappingIR,
    PrecedenceConstraintIR,
    VariableIR,
)


class IRBuilder:

    def build(self, parsed: dict[str, Any]) -> PackageIR:

        package_data = parsed.get(
            "package",
            {}
        )

        package_attributes = package_data.get(
            "attributes",
            {}
        )

        package_name = package_attributes.get(
            "ObjectName"
        )

        package = PackageIR(
            package_name=package_name,
            source_file=parsed.get("source"),
            statistics=parsed.get(
                "statistics",
                {}
            ),
            metadata={
                "parser": "ssis_migration",
                "model_version": "1.0",
            },
        )

        # -----------------------------------------------------
        # Executables
        # -----------------------------------------------------

        for executable in parsed.get(
            "executables",
            []
        ):

            ref_id = (
                executable.get("ref_id")
                or executable.get(
                    "attributes",
                    {}
                ).get("refId")
            )
            properties=executable.get(
            "properties",
            {},
),

            # The package itself appears in the parser's
            # executable collection. It is represented by
            # PackageIR, so do not duplicate it here.

            if ref_id == "Package":
                continue

            package.executables.append(
                self._build_executable(
                    executable
                )
            )

        # -----------------------------------------------------
        # Data flows
        # -----------------------------------------------------

        package.data_flows = [
            self._build_data_flow(flow)
            for flow in parsed.get(
                "data_flow_tasks",
                []
            )
        ]

        # -----------------------------------------------------
        # Precedence constraints
        # -----------------------------------------------------

        package.precedence_constraints = [
            self._build_constraint(
                constraint
            )
            for constraint in parsed.get(
                "precedence_constraints",
                []
            )
        ]

        # -----------------------------------------------------
        # Variables
        # -----------------------------------------------------

        package.variables = [
            self._build_variable(
                variable
            )
            for variable in parsed.get(
                "variables",
                []
            )
        ]

        # -----------------------------------------------------
        # Connection references
        # -----------------------------------------------------

        package.connection_manager_references = [
            self._build_connection_reference(
                connection
            )
            for connection in parsed.get(
                "connection_manager_references",
                []
            )
        ]

        return package

    # =========================================================
    # EXECUTABLE
    # =========================================================

    def _build_executable(
    self,
    executable: dict[str, Any],
    ) -> ExecutableIR:

        attributes = executable.get(
            "attributes",
            {},
        )

        ref_id = (
            attributes.get("refId")
            or attributes.get("DTS:refId")
            or attributes.get("ref_id")
        )

        return ExecutableIR(
            name=executable.get("name"),
            ref_id=ref_id,
            executable_type=executable.get(
                "executable_type"
            ),
            creation_name=executable.get(
                "creation_name"
            ),
            description=executable.get(
                "description"
            ),
            properties=executable.get(
                "properties",
                {},
            ),
        )

    # =========================================================
    # DATA FLOW
    # =========================================================

    def _build_data_flow(
        self,
        flow: dict[str, Any],
    ) -> DataFlowIR:

        data_flow = DataFlowIR(
            name=flow.get("name"),
            ref_id=flow.get("ref_id"),
            creation_name=flow.get(
                "creation_name"
            ),
        )

        data_flow.components = [
            self._build_component(
                component
            )
            for component in flow.get(
                "components",
                []
            )
        ]

        data_flow.paths = flow.get(
            "paths",
            []
        )

        data_flow.column_mappings = [
            MappingIR(
                destination_component=mapping.get(
                    "destination_component"
                ),
                destination_input=mapping.get(
                    "destination_input"
                ),
                destination_column=mapping.get(
                    "destination_column"
                ),
                destination_column_ref=mapping.get(
                    "destination_column_ref"
                ),
                source_lineage_id=mapping.get(
                    "source_lineage_id"
                ),
                external_metadata_column_id=mapping.get(
                    "external_metadata_column_id"
                ),
            )
            for mapping in flow.get(
                "column_mappings",
                []
            )
        ]

        return data_flow

    # =========================================================
    # COMPONENT
    # =========================================================

    def _build_component(
        self,
        component: dict[str, Any],
    ) -> ComponentIR:

        connection_refs = []

        for ref in component.get(
            "connection_manager_refs",
            []
        ):

            if isinstance(ref, str):

                connection_refs.append(
                    ConnectionReferenceIR(
                        connection_manager_ref_id=ref
                    )
                )

            else:

                connection_refs.append(
                    ConnectionReferenceIR(
                        ref_id=ref.get(
                            "ref_id"
                        ),
                        connection_manager_id=ref.get(
                            "connection_manager_id"
                        ),
                        connection_manager_ref_id=ref.get(
                            "connection_manager_ref_id"
                        ),
                        name=ref.get(
                            "name"
                        ),
                    )
                )

        return ComponentIR(
            name=component.get(
                "name"
            ),
            ref_id=component.get(
                "ref_id"
            ),
            component_class_id=component.get(
                "component_class_id"
            ),
            component_class_name=component.get(
                "component_class_name"
            ),
            detected_type=component.get(
                "detected_type"
            ),
            description=component.get(
                "description"
            ),
            properties=component.get(
                "properties",
                {}
            ),
            connection_manager_refs=connection_refs,
            inputs=component.get(
                "inputs",
                []
            ),
            outputs=component.get(
                "outputs",
                []
            ),
        )

    # =========================================================
    # CONNECTION
    # =========================================================

    def _build_connection_reference(
        self,
        connection: dict[str, Any],
        ) -> ConnectionReferenceIR:

        if isinstance(connection, str):
            reference_id = connection
            connection_manager_id = None

        else:
            reference_id = (
                connection.get("reference_id")
                or connection.get("connection_manager_ref_id")
                or connection.get("ref_id")
            )
            connection_manager_id = (
                connection.get("connection_manager_id")
            )

        name = None

        if reference_id:
            name = (
                reference_id
                .split("[")[-1]
                .rstrip("]")
            )

        return ConnectionReferenceIR(
            ref_id=reference_id,
            connection_manager_id=connection_manager_id,
            connection_manager_ref_id=reference_id,
            name=name,
        )

    # =========================================================
    # CONSTRAINT
    # =========================================================

    def _build_constraint(
    self,
    constraint: dict[str, Any],
    ) -> PrecedenceConstraintIR:

        attributes = constraint.get("attributes", {})

        logical_and = constraint.get("logical_and")
        if logical_and is None:
            logical_and = attributes.get("LogicalAnd")

        if isinstance(logical_and, str):
            logical_and = logical_and.strip().lower() == "true"

        if logical_and is None:
            logical_and = True

        # -------------------------------------------------
        # Evaluation operation (EvalOp)
        # SSIS XML values: 0=Constraint, 1=Expression,
        #   2=ExpressionAndConstraint,
        #   3=ExpressionOrConstraint.
        # Default when absent: Constraint.
        # -------------------------------------------------
        _EVAL_OP_MAP = {
            "0": "Constraint",
            "1": "Expression",
            "2": "ExpressionAndConstraint",
            "3": "ExpressionOrConstraint",
        }

        eval_op = (
            constraint.get("evaluation_operation")
            or attributes.get("EvalOp")
        )
        if eval_op is None:
            eval_op = "Constraint"
        elif isinstance(eval_op, str) and eval_op in _EVAL_OP_MAP:
            eval_op = _EVAL_OP_MAP[eval_op]

        # -------------------------------------------------
        # Constraint value (Value)
        # SSIS XML values: 0=Success, 1=Failure,
        #   2=Completion.
        # Default when absent: Success.
        # -------------------------------------------------
        _VALUE_MAP = {
            "0": "Success",
            "1": "Failure",
            "2": "Completion",
        }

        value = (
            constraint.get("value")
            or attributes.get("Value")
        )
        if value is None:
            value = "Success"
        elif isinstance(value, str) and value in _VALUE_MAP:
            value = _VALUE_MAP[value]

        # -------------------------------------------------
        # Expression (absent = no expression)
        # -------------------------------------------------
        expression = (
            constraint.get("expression")
            or attributes.get("Expression")
        )

        return PrecedenceConstraintIR(
            ref_id=(
                constraint.get("ref_id")
                or attributes.get("refId")
            ),
            from_executable=(
                constraint.get("from_executable")
                or constraint.get("from")
                or attributes.get("From")
            ),
            to_executable=(
                constraint.get("to_executable")
                or constraint.get("to")
                or attributes.get("To")
            ),
            logical_and=logical_and,
            evaluation_operation=eval_op,
            value=value,
            expression=expression,
            attributes=attributes,
        )

    # =========================================================
    # VARIABLE
    # =========================================================

    def _build_variable(
        self,
        variable: dict[str, Any],
    ) -> VariableIR:

        return VariableIR(
            name=variable.get(
                "name"
            ),
            namespace=variable.get(
                "namespace"
            ),
            data_type=variable.get(
                "data_type"
            ),
            value=variable.get(
                "value"
            ),
            expression=variable.get(
                "expression"
            ),
            ref_id=variable.get(
                "ref_id"
            ),
        )