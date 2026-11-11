"""Small SSIS pipeline-variable expression compiler; no eval and no Spark UDFs."""
import json
import re
from ssis_migration.mapping import ConversionGap


def assignment_code(expression,owner,runtime):
    match=re.fullmatch(r'\s*@\[([^\]]+)\]\s*=(?!=)\s*([\s\S]+)',expression or '')
    if not match or not match[1].startswith('User::'):
        raise ConversionGap('EXPRESSION_TASK_REVIEW_REQUIRED','Expected one assignment to a declared User variable.')
    candidate=runtime.fork()
    key,kind=candidate.variable(match[1],owner,write=True)
    def resolve(ref):
        code=candidate.read(ref,owner)
        dependency,_=candidate.variable(ref,owner)
        return code,candidate.inputs[dependency]['kind']
    code,_=compile_scalar(match[2],resolve)
    runtime.adopt(candidate)
    return f'_ssis_values[{key!r}] = _ssis_coerce({code}, {kind!r})'


def compile_scalar(text,resolve):
    token=re.compile(r'\s*(?P<cast>\(\s*DT_[A-Za-z0-9_]+\s*\))|\s*(?P<string>"(?:[^"\\]|\\.)*")|\s*(?P<ref>@\[[^\]]+\])|\s*(?P<number>\d+(?:\.\d+)?)|\s*(?P<name>[A-Za-z_]\w*)|\s*(?P<op>[+(),-])')
    tokens=[];pos=0
    while pos<len(text):
        if not text[pos:].strip():break
        match=token.match(text,pos)
        if not match:raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Unsupported variable-expression token.')
        tokens.append((match.lastgroup,match[match.lastgroup]));pos=match.end()
    if not tokens or len(tokens)>256:raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Missing or excessive variable-expression tokens.')
    index=0
    casts={'DT_I2':'int16','DT_I4':'int32','DT_I8':'int64','DT_R4':'float','DT_R8':'float','DT_BOOL':'bool','DT_DATE':'datetime','DT_DBTIMESTAMP':'datetime'}
    def consume(expected):
        nonlocal index
        if index>=len(tokens) or tokens[index][1]!=expected:raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Unbalanced variable expression.')
        index+=1
    def atom(depth):
        nonlocal index
        if depth>32 or index>=len(tokens):raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Incomplete/deep variable expression.')
        kind,value=tokens[index];index+=1
        if kind=='cast':
            name=value.strip('() ').upper()
            if name not in casts:raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Unsupported SSIS variable cast.')
            code,_=atom(depth+1)
            return f'_ssis_coerce({code}, {casts[name]!r})',casts[name]
        if value=='(':
            result=add(depth+1);consume(')');return result
        if value=='-':
            code,category=atom(depth+1)
            if category not in {'int16','int32','int64','float','decimal'}:raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Numeric negation requires a known number.')
            return f'(-{code})',category
        if kind=='string':
            if re.search(r'\\(?!["\\nrtbf])',value[1:-1]):raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Unsupported string escape.')
            return repr(json.loads(value,strict=False)),'string'
        if kind=='number':return (f'Decimal({value!r})','decimal') if '.' in value else (str(int(value)),'int32')
        if kind=='ref':return resolve(value[2:-1])
        if kind=='name' and value.upper() in {'SUBSTRING','RIGHT','LEFT'}:
            consume('(');arguments=[add(depth+1)]
            while index<len(tokens) and tokens[index][1]==',':
                index+=1;arguments.append(add(depth+1))
            consume(')')
            expected=3 if value.upper()=='SUBSTRING' else 2
            if len(arguments)!=expected:raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Invalid string-function argument count.')
            first=arguments[0][0]
            # Runtime arguments are explicit values; JSON numeric date keys can be
            # viewed as text for the source's string operations.
            args=[f'str({first})']+[f'_ssis_coerce({code}, "int32")' for code,_ in arguments[1:]]
            return f'_ssis_{value.lower()}('+', '.join(args)+')','string'
        raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Unsupported variable expression/function.')
    def add(depth):
        nonlocal index
        left,category=atom(depth)
        while index<len(tokens) and tokens[index][1]=='+':
            index+=1;right,right_type=atom(depth)
            numeric={'int16','int32','int64','float','decimal'}
            if not (category==right_type=='string' or category in numeric and right_type in numeric):
                raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Ambiguous string/numeric addition requires a typed source decision.')
            left=f'({left} + {right})'
        return left,category
    result=add(0)
    if index!=len(tokens):raise ConversionGap('VARIABLE_EXPRESSION_REVIEW_REQUIRED','Unconsumed variable expression.')
    return result
