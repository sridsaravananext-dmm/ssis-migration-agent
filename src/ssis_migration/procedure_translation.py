"""Inline bounded procedure bodies with formal-to-actual arguments; never emit UDFs."""
from __future__ import annotations

import re
import sqlglot
from sqlglot import exp, ErrorLevel

from ssis_migration.bound_sql import bindable_sql, bound_task
from ssis_migration.mapping import ConversionGap
from ssis_migration.procedure_file_loader import CREATE
from ssis_migration.sql_text import CALL, mask_sql


def comma_parts(text):
    clean=mask_sql(text)
    depth,start,parts=0,0,[]
    for i,char in enumerate(clean):
        if char=='(':depth+=1
        elif char==')':depth-=1
        elif char==',' and depth==0:
            parts.append(text[start:i].strip());start=i+1
        if depth<0:raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Unbalanced argument/type parentheses.')
    if depth:raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Unbalanced argument/type parentheses.')
    if text[start:].strip():parts.append(text[start:].strip())
    elif parts:raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','A trailing comma does not identify an argument.')
    return parts


def literal(text):
    try:node=sqlglot.parse_one(text,read='tsql',error_level=ErrorLevel.RAISE)
    except Exception as exc:raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Expected a literal/default argument.') from exc
    allowed=(exp.Literal,exp.Null,exp.Boolean,exp.Neg,exp.National)
    if not node or any(not isinstance(n,allowed) for n in node.walk()):
        raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Function/expression arguments require explicit translation.')
    return node.sql(dialect='tsql')


def procedure_body(definition):
    clean=mask_sql(definition)
    offset=len(clean)-len(clean.lstrip())
    header=CREATE.match(clean.lstrip())
    if not header:raise ConversionGap('PROCEDURE_TRANSLATION_REQUIRED','A full procedure definition is required.')
    tail=definition[offset+header.end():]
    delimiter=re.search(r'(?<![@\w])AS\b',bindable_sql(tail),re.I)
    if not delimiter:raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Cannot identify a procedure body boundary.')
    declarations=tail[:delimiter.start()].strip()
    params=[]
    for declaration in comma_parts(declarations):
        match=re.fullmatch(r'\s*(@[A-Za-z_]\w*)\s+([A-Za-z]+(?:\s*\(\s*(?:\d+|MAX)(?:\s*,\s*\d+)?\s*\))?)\s*(?:=\s*([\s\S]+))?',declaration,re.I)
        if not match:raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Output, table-valued, or execution-option declarations need review.')
        base=match[2].split('(')[0].strip().casefold()
        if base not in {'int','bigint','smallint','tinyint','bit','date','datetime','datetime2','time','decimal','numeric',
                'float','real','money','smallmoney','char','nchar','varchar','nvarchar','uniqueidentifier'}:
            raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','User-defined procedure parameter types require review.')
        name=match[1].lstrip('@').casefold()
        if any(p['name']==name for p in params):raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Duplicate formal parameter.')
        params.append({'name':name,'sql_type':match[2], 'default':literal(match[3]) if match[3] is not None else None})
    body=tail[delimiter.end():].strip()
    wrapper=re.fullmatch(r'\s*BEGIN\b([\s\S]*)\bEND\s*;?\s*',mask_sql(body),re.I)
    if wrapper:body=body[wrapper.start(1):wrapper.end(1)].strip()
    return params,body


def translate_procedure(call,definition,properties,owner,connection,config,runtime):
    formals,body=procedure_body(definition)
    clean=mask_sql(call)
    offset=len(clean)-len(clean.lstrip())
    matched=CALL.match(clean.lstrip())
    if not matched or re.match(r'\s*EXEC(?:UTE)?\s+@',clean,re.I):
        raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Return-value/dynamic procedure calls need separate translation.')
    arguments=call[offset+matched.end():].strip().removesuffix(';').strip()
    actuals={};named_seen=False;pos=0
    names={p['name'] for p in formals}
    for part in comma_parts(arguments):
        named=re.fullmatch(r'\s*@([A-Za-z_]\w*)\s*=\s*([\s\S]+)',part)
        if named:
            key,value=named[1].casefold(),named[2].strip();named_seen=True
        else:
            if named_seen or pos>=len(formals):raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Invalid positional/named procedure arguments.')
            key,value=formals[pos]['name'],part;pos+=1
        if key not in names or key in actuals:raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Unknown or duplicate formal argument.')
        actuals[key]=value
    inputs={}
    for binding in properties.get('parameter_bindings',[]):
        key=str(binding.get('ParameterName','')).lstrip('@').casefold()
        if key in inputs or str(binding.get('ParameterDirection','Input')) not in {'Input','1'}:
            raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Unique named input task bindings are required.')
        inputs[key]=binding.get('DtsVariableName')
    replacements={};bindings=[];used_inputs=set()
    body_variables={m[1].casefold() for m in re.finditer(r'(?<!@)@([A-Za-z_]\w*)',bindable_sql(body))}
    for index,formal in enumerate(formals):
        if formal['name'] not in body_variables:
            raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Unused formals still require source parameter-conversion review.')
        value=actuals.get(formal['name'])
        if value is None or value.casefold()=='default':
            value=formal['default']
            if value is None:raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','A required procedure argument is missing.')
        variable=re.fullmatch(r'@([A-Za-z_]\w*)',value)
        if variable:
            key=variable[1].casefold()
            if key not in inputs:raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','A call argument has no task-variable binding.')
            marker='ssis_proc'+str(index)
            while marker in body_variables:
                marker+='_'
            bindings.append({'ParameterName':marker,'DtsVariableName':inputs[key],'ParameterDirection':'Input'})
            used_inputs.add(key);value='@'+marker
        else:value=literal(value)
        replacements[formal['name']]=f'CAST({value} AS {formal["sql_type"]})'
    if used_inputs!=set(inputs):raise ConversionGap('PROCEDURE_BINDINGS_UNSUPPORTED','Unused task bindings require source review.')
    edits=[]
    for match in re.finditer(r'(?<!@)@([A-Za-z_]\w*)',bindable_sql(body)):
        if match[1].casefold() in replacements:edits.append((match.start(),match.end(),replacements[match[1].casefold()]))
    for a,b,value in reversed(edits):body=body[:a]+value+body[b:]
    rewritten={**properties,'SqlStatementSource':body,'parameter_bindings':bindings}
    code,uses,evidence=bound_task(rewritten,owner,connection,config,runtime)
    evidence['procedure_parameters']=formals
    return code,uses,evidence
