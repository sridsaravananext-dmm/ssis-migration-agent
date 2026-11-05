from __future__ import annotations
from pathlib import Path
from ssis_migration.analyzer.ssis_sql_dependencies import extract_ssis_sql_dependencies
from ssis_migration.analyzer.sql_semantic import extract_referenced_objects
from ssis_migration.database.connection_mapping import ConnectionMappingSet
from ssis_migration.database.dtsx_metadata_builder import build_database_ir_from_dtsx
from ssis_migration.database.ddl_parser import merge_ddl_into_dtsx, parse_ddl_file
from ssis_migration.database.importer import load_database_metadata
from ssis_migration.database.ir import SQLObjectIR
from ssis_migration.database.ir_builder import build_database_ir
from ssis_migration.database.relevance import build_relevant_database_context
from ssis_migration.sql_text import normalize_name, procedure_calls
from .migration_context import DatabaseContext, MigrationContext


def build_migration_context(package_ir, ssis_analysis, connection_mapping: ConnectionMappingSet | None = None,
                            base_path='.', ddl_path=None, *, ddl_paths=(), procedure_definitions=None,
                            procedure_origins=None, default_schema='dbo'):
    """Apply the same supplied definitions before relevance/planning on both paths."""
    base_path = Path(base_path)
    dependencies = extract_ssis_sql_dependencies(package_ir)
    for key in ('procedure_connections', 'object_connections'):
        normalized = {}
        for name, refs in dependencies.get(key, {}).items():
            normalized.setdefault(normalize_name(name, default_schema), set()).update(refs)
        dependencies[key] = {k: sorted(v) for k, v in normalized.items()}
    ddls = [parse_ddl_file(path) for path in dict.fromkeys(([ddl_path] if ddl_path else []) + list(ddl_paths))]
    tables = {}
    for ddl in ddls:
        for table in ddl.tables:
            key = f'{table.schema}.{table.name}'.casefold()
            value = (table.columns, table.primary_keys)
            if key in tables and tables[key] != value:
                raise ValueError(f'Conflicting supplied DDL definitions for {key}')
            tables[key] = value
    definitions = procedure_definitions or {}
    origins = procedure_origins or {}
    mappings = connection_mapping.mappings if connection_mapping else []
    contexts = []
    for mapping in mappings or [None]:
        ir = (build_database_ir(load_database_metadata(base_path / mapping.metadata_file))
              if mapping else build_database_ir_from_dtsx(package_ir))
        for ddl in ddls:
            ir = merge_ddl_into_dtsx(ir, ddl)
        index = {normalize_name(f'{obj.schema}.{obj.name}', default_schema): obj for obj in ir.objects}
        for name, definition in definitions.items():
            obj = index.get(name)
            if obj and obj.definition and obj.definition.strip() != definition.strip():
                raise ValueError(f'Conflicting metadata and supplied definition for {name}; resolve the source conflict')
            if not obj:
                parts = name.split('.')
                if len(parts) != 2:
                    raise ValueError(f'Procedure definition requires schema.name: {name}')
                obj = SQLObjectIR(schema=parts[0], name=parts[1], object_type='PROCEDURE')
                ir.objects.append(obj)
                index[name] = obj
            obj.definition = definition
            obj.metadata['definition_files'] = origins.get(name, [])
        for obj in ir.objects:
            if obj.definition:
                refs = extract_referenced_objects(obj.definition) + procedure_calls(obj.definition)
                obj.dependencies = sorted({normalize_name(x, default_schema) for x in refs})
                ir.dependency_graph[f'{obj.schema}.{obj.name}'] = obj.dependencies
        relevant = build_relevant_database_context(ir, dependencies,
                         connection_reference=mapping.connection_reference if mapping else None)
        contexts.append(DatabaseContext(
            connection_reference=mapping.connection_reference if mapping else 'dtsx_derived',
            database_name=mapping.database_name if mapping else 'dtsx_derived',
            role=mapping.role if mapping else 'combined', database_ir=ir,
            relevant_objects=relevant['objects'], dependency_edges=relevant['edges']))
    return MigrationContext(package_ir=package_ir, ssis_analysis=ssis_analysis, databases=contexts,
        metadata={'builder': 'ssis_migration', 'model_version': '2.0',
                  'metadata_source': 'connection_mapping' if mappings else 'dtsx_derived',
                  'default_schema': default_schema, 'procedure_origins': origins,
                  'multiple_database_contexts': len(contexts) > 1})
