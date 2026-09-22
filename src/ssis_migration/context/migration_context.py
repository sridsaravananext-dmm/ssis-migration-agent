from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ssis_migration.database.ir import DatabaseIR
from ssis_migration.ir.models import PackageIR


@dataclass
class DatabaseContext:

    connection_reference: str

    database_name: str | None

    role: str | None

    database_ir: DatabaseIR

    relevant_objects: list[dict[str, Any]] = field(
        default_factory=list
    )

    dependency_edges: list[dict[str, str]] = field(
        default_factory=list
    )

    def to_dict(self) -> dict[str, Any]:

        return {
            "connection_reference": (
                self.connection_reference
            ),
            "database_name": self.database_name,
            "role": self.role,
            "database": self.database_ir.to_dict(),
            "relevant_objects": (
                self.relevant_objects
            ),
            "dependency_edges": (
                self.dependency_edges
            ),
        }


@dataclass
class MigrationContext:

    package_ir: PackageIR

    ssis_analysis: dict[str, Any]

    databases: list[DatabaseContext] = field(
        default_factory=list
    )

    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    def to_dict(self) -> dict[str, Any]:

        return {
            "package": self.package_ir.to_dict(),

            "ssis_analysis": self.ssis_analysis,

            "databases": [
                database.to_dict()
                for database in self.databases
            ],

            "metadata": self.metadata,
        }