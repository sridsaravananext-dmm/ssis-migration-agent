from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any
import json


@dataclass
class ConnectionReferenceIR:
    ref_id: str | None = None
    connection_manager_id: str | None = None
    connection_manager_ref_id: str | None = None
    name: str | None = None


@dataclass
class ComponentIR:
    name: str | None = None
    ref_id: str | None = None
    component_class_id: str | None = None
    component_class_name: str | None = None
    detected_type: str | None = None
    description: str | None = None

    properties: dict[str, Any] = field(default_factory=dict)
    connection_manager_refs: list[ConnectionReferenceIR] = field(
        default_factory=list
    )

    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MappingIR:
    destination_component: str | None = None
    destination_input: str | None = None
    destination_column: str | None = None
    destination_column_ref: str | None = None
    source_lineage_id: str | None = None
    external_metadata_column_id: str | None = None


@dataclass
class DataFlowIR:
    name: str | None = None
    ref_id: str | None = None
    creation_name: str | None = None

    components: list[ComponentIR] = field(default_factory=list)
    paths: list[dict[str, Any]] = field(default_factory=list)
    column_mappings: list[MappingIR] = field(default_factory=list)


@dataclass
class PrecedenceConstraintIR:
    ref_id: str | None = None
    from_executable: str | None = None
    to_executable: str | None = None
    logical_and: bool | None = None
    evaluation_operation: str | None = None
    value: str | None = None
    expression: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class VariableIR:
    name: str | None = None
    namespace: str | None = None
    data_type: str | None = None
    value: Any = None
    expression: str | None = None
    ref_id: str | None = None


@dataclass
class ExecutableIR:
    name: str | None = None
    ref_id: str | None = None
    executable_type: str | None = None
    creation_name: str | None = None
    description: str | None = None
    properties: dict[str, Any] = field(
        default_factory=dict
    )

@dataclass
class PackageIR:
    package_name: str | None = None
    source_file: dict[str, Any] | None = None

    executables: list[ExecutableIR] = field(
        default_factory=list
    )

    data_flows: list[DataFlowIR] = field(
        default_factory=list
    )

    precedence_constraints: list[PrecedenceConstraintIR] = field(
        default_factory=list
    )

    variables: list[VariableIR] = field(
        default_factory=list
    )

    connection_manager_references: list[
        ConnectionReferenceIR
    ] = field(default_factory=list)

    statistics: dict[str, Any] = field(
        default_factory=dict
    )

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            ensure_ascii=False,
        )