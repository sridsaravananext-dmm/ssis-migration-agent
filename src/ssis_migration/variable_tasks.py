"""Query-only variable Execute SQL tasks with static SQL and bounded results."""
import hashlib

from ssis_migration.bound_sql import result_bindings,variable_query,_result_code,_named_sql,sql_call,require_targets
from ssis_migration.mapping import ConversionGap,translate_sql
from ssis_migration.source_context import describe_variable,output_references


def variable_sql_task(properties,owner,connection,config,runtime,disabled=lambda ref:False):
    candidate=runtime.fork()
    results=result_bindings(properties)
    if not results:raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED','Variable Execute SQL tasks require explicit single-row result bindings.')
    context=describe_variable(properties.get('SqlStatementSource'),owner,candidate.package)
    if context.get('status')!='DECLARED_REQUIRES_RUNTIME_REVIEW' or str(context.get('data_type'))!='8':
        raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED','Variable SQL needs one scoped, non-sensitive String declaration.')
    reference=context['reference'];guards=[]
    if context.get('evaluate_as_expression') is True:
        if properties.get('parameter_bindings'):
            raise ConversionGap('SQL_BINDINGS_UNSUPPORTED','Expression query references provide arguments; additional task parameter bindings require separate translation.')
        call,uses=variable_query(context,owner,connection,config,candidate)
        kind='EXPRESSION_TEMPLATE';active=context.get('expression') or ''
    elif context.get('evaluate_as_expression') is False and isinstance(context.get('value'),str) and context['value'].strip():
        # The source string is inspected/compiled once. Runtime strings never
        # become executable SQL; changed overrides stop before any writes.
        value=context['value'];key,_=candidate.variable(reference,owner)
        for exe in candidate.package.executables:
            if disabled(exe.ref_id):continue
            for output in output_references(exe):
                try:output_key,_=candidate.fork().variable(output,exe.ref_id,write=True)
                except ConversionGap:continue
                if output_key==key:
                    raise ConversionGap('VARIABLE_SQL_MUTATION_UNSUPPORTED','The captured SQL variable is written by a source task; its changing SQL needs explicit translation.',exe.ref_id)
        sql,args=_named_sql(value,properties.get('parameter_bindings',[]),owner,candidate)
        emitted,uses=translate_sql(sql,connection,config,query_only=True,parameter_names=args.keys())
        require_targets(uses)
        # Register this read after argument discovery so ordinary bindings and
        # same-variable uses preserve their type/read requirements.
        read=candidate.read(reference,owner)
        candidate.inputs[key]['fixed_sql_text']=value
        guards=[f'if {read} != {value!r}:',"    raise RuntimeError('Captured SQL variable changed; regenerate before execution.')"]
        call=sql_call(emitted,args);kind='CAPTURED_QUERY';active=value
    else:
        raise ConversionGap('VARIABLE_SQL_REVIEW_REQUIRED','Variable SQL requires an explicit active expression or captured non-expression query.')
    lines=guards+['_ssis_variable_frame = '+call]+_result_code('_ssis_variable_frame',results,owner,candidate)
    evidence={'source_reference':reference,'scope_ref_id':context['scope_ref_id'],'kind':kind,
        'active_source_sha256':hashlib.sha256(active.encode()).hexdigest(),'query_only':True,'row_limit':2,
        'runtime_sql_evaluation':False,'result_bindings':results}
    runtime.adopt(candidate)
    return '\n'.join(lines),uses,evidence
