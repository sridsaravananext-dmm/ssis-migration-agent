from __future__ import annotations

from pathlib import Path
from typing import Any

from ssis_migration.analyzer.ssis_sql_dependencies import (
    extract_ssis_sql_dependencies,
)

from ssis_migration.database.connection_mapping import (
    ConnectionMappingSet,
)

from ssis_migration.database.importer import (
    load_database_metadata,
)

from ssis_migration.database.ir_builder import (
    build_database_ir,
)

from ssis_migration.database.relevance import (
    build_relevant_database_context,
)

from ssis_migration.ir.models import PackageIR

from .migration_context import (
    DatabaseContext,
    MigrationContext,
)


def build_migration_context(
    package_ir: PackageIR,
    ssis_analysis: dict[str, Any],
    connection_mapping: ConnectionMappingSet,
    base_path: str | Path = ".",
) -> MigrationContext:

    base_path = Path(base_path)

    ssis_dependencies = (
        extract_ssis_sql_dependencies(
            package_ir
        )
    )

    databases: list[DatabaseContext] = []

    for mapping in connection_mapping.mappings:

        metadata_path = (
            base_path
            / mapping.metadata_file
        )

        metadata = load_database_metadata(
            metadata_path
        )

        database_ir = build_database_ir(
            metadata
        )

        relevant = (
            build_relevant_database_context(
                database_ir,
                ssis_dependencies,
                connection_reference=(
                    mapping.connection_reference
                ),
            )
        )

        databases.append(
            DatabaseContext(
                connection_reference=(
                    mapping.connection_reference
                ),
                database_name=(
                    mapping.database_name
                ),
                role=mapping.role,
                database_ir=database_ir,
                relevant_objects=relevant[
                    "objects"
                ],
                dependency_edges=relevant[
                    "edges"
                ],
            )
        )

    return MigrationContext(
        package_ir=package_ir,
        ssis_analysis=ssis_analysis,
        databases=databases,
        metadata={
            "builder": "ssis_migration",
            "model_version": "1.0",
        },
    )