from __future__ import annotations

from pathlib import Path
from typing import Any

from ssis_migration.analyzer.ssis_sql_dependencies import (
    extract_ssis_sql_dependencies,
)

from ssis_migration.database.connection_mapping import (
    ConnectionMappingSet,
)

from ssis_migration.database.dtsx_metadata_builder import (
    build_database_ir_from_dtsx,
)
from ssis_migration.database.ddl_parser import (
    merge_ddl_into_dtsx,
    parse_ddl_file,
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
    connection_mapping: ConnectionMappingSet | None = None,
    base_path: str | Path = ".",
    ddl_path: str | Path | None = None,
) -> MigrationContext:

    base_path = Path(base_path)

    ssis_dependencies = (
        extract_ssis_sql_dependencies(
            package_ir
        )
    )

    databases: list[DatabaseContext] = []

    # -------------------------------------------------
    # Path A: External metadata via connection mapping
    # -------------------------------------------------
    if connection_mapping and connection_mapping.mappings:
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

    # -------------------------------------------------
    # Path B: Derive metadata from the .dtsx alone
    # -------------------------------------------------
    else:
        database_ir = build_database_ir_from_dtsx(
            package_ir
        )

        # If a DDL file is supplied, override dtsx-derived
        # columns with DDL-derived columns (which have
        # nullability and primary keys).
        if ddl_path:
            ddl_ir = parse_ddl_file(ddl_path)
            database_ir = merge_ddl_into_dtsx(
                database_ir, ddl_ir,
            )

        relevant = (
            build_relevant_database_context(
                database_ir,
                ssis_dependencies,
                connection_reference=None,
            )
        )

        databases.append(
            DatabaseContext(
                connection_reference="dtsx_derived",
                database_name="dtsx_derived",
                role="combined",
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
            "metadata_source": (
                "connection_mapping"
                if connection_mapping and connection_mapping.mappings
                else "dtsx_derived"
            ),
        },
    )