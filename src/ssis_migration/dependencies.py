"""Connection-aware procedure resolution and reachable dependency findings."""
from ssis_migration.sql_text import normalize_name, procedure_calls, has_dynamic_call, mask_sql
from ssis_migration.mapping import ConversionGap
from ssis_migration.source_context import source_selection, active_source_sql
import re


class ProcedureResolver:
    def __init__(self, context):
        self.context=context
        self.aliases=context.metadata.get('connection_aliases',{})
        self.default_schema=context.metadata.get('default_schema','dbo')

    def resolve(self, name, connection=None):
        connection=self.aliases.get(connection,self.aliases.get(str(connection).strip('{}').casefold(),connection))
        dbs=self.context.databases
        if connection:
            selected=[d for d in dbs if d.connection_reference==connection]
            if not selected and len(dbs)==1 and dbs[0].connection_reference=='dtsx_derived': selected=dbs
        else: selected=dbs
        if not selected: raise ConversionGap('CONNECTION_CONTEXT_UNRESOLVED',f'Unknown connection context: {connection}')
        if len(selected)!=1: raise ConversionGap('AMBIGUOUS_PROCEDURE_REFERENCE',f'Procedure {name} needs a unique database connection.')
        db=selected[0]
        parts=normalize_name(name,self.default_schema).split('.')
        if len(parts)==3:
            if not db.database_name or parts[0]!=db.database_name.casefold():
                raise ConversionGap('CROSS_DATABASE_REFERENCE_REVIEW',f'Explicit database reference {name} differs from the selected context.')
            parts=parts[1:]
        key='.'.join(parts)
        matches=[o for o in db.database_ir.objects if normalize_name(f'{o.schema}.{o.name}',self.default_schema)==key and o.definition and 'PROCEDURE' in o.object_type.upper()]
        if not matches: raise ConversionGap('MISSING_PROCEDURE_DEFINITION',f'Provide {name} for connection {db.connection_reference}.',key)
        if len(matches)!=1: raise ConversionGap('AMBIGUOUS_PROCEDURE_REFERENCE',f'Multiple definitions for {name}.',key)
        return matches[0].definition,db.connection_reference,key


def dependency_findings(context):
    from ssis_migration.assessment import finding
    package=context.package_ir
    findings=list(context.metadata.get('input_findings',[]))
    resolver=ProcedureResolver(context)
    visited=set()
    if str(package.metadata.get('package_attributes',{}).get('Disabled','')).casefold() in {'true','1','-1'}: return findings
    def inspect(ref,sql,connection=None,required=False):
        if not isinstance(sql,str) or not sql.strip():
            if required: findings.append(finding('SQL_EXTRACTION_INCOMPLETE',ref,'Static SQL text was not extracted.'))
            return
        if (has_dynamic_call(sql) or re.search(r'\bsp_executesql\b',mask_sql(sql),re.I)) and ref not in context.metadata.get('compiled_dynamic_sql_sources', set()):
            findings.append(finding('DYNAMIC_PROCEDURE_REFERENCE',ref,'Dynamic procedure/SQL identity requires review.'))
        for call in procedure_calls(sql):
            try: body,scope,key=resolver.resolve(call,connection)
            except ConversionGap as exc:
                findings.append(finding(exc.code,ref,str(exc),evidence=exc.evidence or call));continue
            identity=(scope,key)
            if identity in visited: continue
            visited.add(identity)
            inspect(key,body,scope)
    disabled_refs=set()
    for exe in package.executables:
        attrs=exe.properties.get('_attributes',{})
        if str(attrs.get('Disabled','')).casefold() in {'true','1','-1'}: disabled_refs.add(exe.ref_id)
    parents = {e.ref_id: e.properties.get('_parent_ref_id') for e in package.executables}
    def disabled(ref):
        seen = set()
        while ref:
            if ref in disabled_refs: return True
            if ref in seen: return False
            seen.add(ref)
            inferred = ref.rsplit('\\',1)[0] if '\\' in ref else None
            ref = parents.get(ref) or inferred
        return False
    for index,exe in enumerate(package.executables):
        if disabled(exe.ref_id): continue
        if 'executesqltask' in (exe.executable_type or exe.creation_name or '').casefold():
            props=exe.properties;sql=props.get('SqlStatementSource')
            if str(props.get('SqlStatementSourceType','DirectInput')) not in {'0','DirectInput'}:sql=None
            inspect(exe.ref_id or f'executable:{index}',sql,props.get('connection_manager_ref_id') or props.get('Connection'),True)
    for flow in package.data_flows:
        if disabled(flow.ref_id): continue
        for component in flow.components:
            if component.detected_type!='OLE_DB_SOURCE':continue
            props=component.properties
            connection=next((r.connection_manager_ref_id for r in component.connection_manager_refs if r.connection_manager_ref_id),None)
            selected=source_selection(props)
            if selected['kind'].startswith('VARIABLE'):
                if component.ref_id not in context.metadata.get('compiled_variable_sources', set()):
                    findings.append(finding('VARIABLE_SQL_REVIEW_REQUIRED', component.ref_id,
                        'Active source variable requires runtime review; inactive captured SQL is not dependency evidence.'))
            else: inspect(component.ref_id,active_source_sql(props),connection,True)
    return findings
