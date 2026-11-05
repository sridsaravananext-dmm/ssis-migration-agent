from __future__ import annotations
from pathlib import Path
from ssis_migration.analyzer.ssis_sql_dependencies import extract_ssis_sql_dependencies
from ssis_migration.analyzer.sql_semantic import extract_referenced_objects
from ssis_migration.database.connection_mapping import ConnectionMappingSet
from ssis_migration.database.dtsx_metadata_builder import build_database_ir_from_dtsx
from ssis_migration.database.ddl_parser import merge_ddl_into_dtsx, parse_ddl_file
from ssis_migration.database.importer import load_database_metadata
from ssis_migration.database.ir import SQLObjectIR, DatabaseIR
from ssis_migration.database.ir_builder import build_database_ir
from ssis_migration.database.relevance import build_relevant_database_context
from ssis_migration.sql_text import normalize_name, procedure_calls
from .migration_context import DatabaseContext, MigrationContext


def build_migration_context(package_ir, ssis_analysis, connection_mapping: ConnectionMappingSet | None = None,
                            base_path='.', ddl_path=None, *, ddl_paths=(), procedure_definitions=None,
                            procedure_origins=None, default_schema='dbo', dependency_scopes=None):
    """Apply definitions to their connection context; never broadcast ambiguous files."""
    from ssis_migration.assessment import finding
    base_path = Path(base_path)
    dependencies = extract_ssis_sql_dependencies(package_ir)
    aliases = package_ir.metadata.get('connection_aliases', {})
    def canonical(ref): return aliases.get(ref, aliases.get(str(ref).strip('{}').casefold(), ref))
    for key in ('procedure_connections','object_connections'):
        normalized = {}
        for name, refs in dependencies.get(key,{}).items():
            normalized.setdefault(normalize_name(name,default_schema),set()).update(canonical(r) for r in refs)
        dependencies[key] = {k:sorted(v) for k,v in normalized.items()}
    mappings = connection_mapping.mappings if connection_mapping else []
    contexts = []
    for mapping in mappings or [None]:
        ir = build_database_ir(load_database_metadata(base_path/mapping.metadata_file)) if mapping else build_database_ir_from_dtsx(package_ir)
        contexts.append(DatabaseContext(connection_reference=canonical(mapping.connection_reference) if mapping else 'dtsx_derived',
            database_name=mapping.database_name if mapping else 'dtsx_derived',role=mapping.role if mapping else 'combined',database_ir=ir))
    input_findings = []
    scopes = dependency_scopes or {}
    definitions = procedure_definitions or {}
    origins = procedure_origins or {}
    def targets(scope, key, *, table=False):
        if scope:
            matches = [db for db in contexts if db.connection_reference == canonical(scope)]
            if len(matches)!=1: raise ValueError(f'Unknown or duplicate dependency connection scope: {scope}')
            return matches
        if len(contexts)==1: return contexts
        matches = []
        for db in contexts:
            objects = db.database_ir.tables if table else db.database_ir.objects
            if any(normalize_name(f'{o.schema}.{o.name}',default_schema)==key for o in objects): matches.append(db)
        if len(matches)==1: return matches
        input_findings.append(finding('DEPENDENCY_SCOPE_REQUIRED', key,
            'Supporting definition cannot be assigned to a unique database connection. Supply a dependency scope.', evidence=key))
        return []
    for scoped_name, definition in definitions.items():
        scope, separator, name = scoped_name.rpartition('|')
        key = name if separator else scoped_name
        for db in targets(scope if separator else None,key):
            index = {normalize_name(f'{o.schema}.{o.name}',default_schema):o for o in db.database_ir.objects}
            obj = index.get(key)
            if obj and obj.definition and obj.definition.strip()!=definition.strip():
                raise ValueError(f'Conflicting metadata and supplied definition for {db.connection_reference}|{key}')
            if not obj:
                parts = key.split('.')
                if len(parts)!=2: raise ValueError(f'Procedure definitions require schema.name: {key}')
                obj=SQLObjectIR(schema=parts[0],name=parts[1],object_type='PROCEDURE')
                db.database_ir.objects.append(obj)
            obj.definition=definition
            obj.metadata['definition_files']=origins.get(scoped_name,[])
    seen_tables={}
    for path in dict.fromkeys(([ddl_path] if ddl_path else [])+list(ddl_paths)):
        scope=scopes.get(str(path)) or scopes.get(Path(path).name)
        ddl=parse_ddl_file(path)
        if not ddl.tables:
            input_findings.append(finding('DDL_EXTRACTION_INCOMPLETE',Path(path).name,'No supported CREATE TABLE definition was extracted.'))
        for table in ddl.tables:
            key=normalize_name(f'{table.schema}.{table.name}',default_schema)
            for db in targets(scope,key,table=True):
                signature=(db.connection_reference,key)
                value=(table.columns,table.primary_keys)
                if signature in seen_tables and seen_tables[signature]!=value: raise ValueError(f'Conflicting supplied DDL definitions for {signature}')
                seen_tables[signature]=value
                merge_ddl_into_dtsx(db.database_ir,DatabaseIR(tables=[table],schemas=[table.schema]))
    for db in contexts:
        for obj in db.database_ir.objects:
            if obj.definition:
                refs=extract_referenced_objects(obj.definition)+procedure_calls(obj.definition)
                obj.dependencies=sorted({normalize_name(x,default_schema) for x in refs})
                db.database_ir.dependency_graph[f'{obj.schema}.{obj.name}']=obj.dependencies
        relevant=build_relevant_database_context(db.database_ir,dependencies,connection_reference=None if not mappings else db.connection_reference)
        db.relevant_objects=relevant['objects'];db.dependency_edges=relevant['edges']
    return MigrationContext(package_ir=package_ir,ssis_analysis=ssis_analysis,databases=contexts,
        metadata={'builder':'ssis_migration','model_version':'2.1','metadata_source':'connection_mapping' if mappings else 'dtsx_derived',
            'default_schema':default_schema,'procedure_origins':origins,'multiple_database_contexts':len(contexts)>1,
            'connection_aliases':aliases,'input_findings':input_findings})
