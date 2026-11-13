"""Distributed dataset evidence; never infer whole-workload accuracy."""
from decimal import Decimal
import math


def compare_frames(source,target,*,keys=(),absolute_tolerances=None):
    from pyspark.sql import functions as F
    keys=list(keys);tolerances={} if absolute_tolerances is None else absolute_tolerances
    if not isinstance(tolerances,dict):raise ValueError('Tolerances must be a column mapping')
    if len(set(keys))!=len(keys):raise ValueError('Comparison keys must be unique')
    left={f.name:f.dataType.simpleString() for f in source.schema.fields}
    right={f.name:f.dataType.simpleString() for f in target.schema.fields}
    if len(left)!=len(source.schema.fields) or len(right)!=len(target.schema.fields):raise ValueError('Duplicate column names cannot be compared')
    if any(c not in left for c in keys) or any(c not in left or c in keys for c in tolerances):raise ValueError('Unknown comparison column or tolerance on key')
    for c,v in tolerances.items():
        if isinstance(v,bool) or not isinstance(v,(int,float,Decimal)) or not math.isfinite(v) or v<0:raise ValueError('Absolute tolerances must be finite nonnegative numbers')
        if left[c] not in {'tinyint','smallint','int','bigint','float','double'} and not left[c].startswith('decimal('):raise ValueError('Tolerance requires a numeric column')
    tolerances={c:Decimal(str(v)) for c,v in tolerances.items()}
    if any(len(v.as_tuple().digits)>38 or v.as_tuple().exponent < -38 or v.adjusted()>37 for v in tolerances.values()):
        raise ValueError('Tolerance exceeds Spark decimal precision/scale')
    for kind in list(left.values())+list(right.values()):
        if kind.startswith(('array<','map<','struct<')):raise ValueError('Complex comparison types need explicit canonicalization')
    nullable_left={f.name:f.nullable for f in source.schema.fields};nullable_right={f.name:f.nullable for f in target.schema.fields}
    schema_equal=left==right and nullable_left==nullable_right
    result={'scope':'DATASET','runtime_verification':'MEASURED','legacy_match_percent':None,
        'source_schema':left,'target_schema':right,'source_nullable':nullable_left,'target_nullable':nullable_right,'schema_equal':schema_equal,
        'source_rows':source.count(),'target_rows':target.count(),'keys':keys,'absolute_tolerances':{k:str(v) for k,v in tolerances.items()}}
    if not schema_equal:result.update(status='MISMATCH',reason='SCHEMA_MISMATCH');return result
    columns=list(left)
    source=source.select(*[F.col('`'+c.replace('`','``')+'`') for c in columns])
    target=target.select(*[F.col('`'+c.replace('`','``')+'`') for c in columns])
    if not keys:
        if tolerances:raise ValueError('Tolerant comparisons require unique keys')
        missing=source.exceptAll(target).count();extra=target.exceptAll(source).count()
        result.update(status='MATCH' if not missing and not extra else 'MISMATCH',missing_rows=missing,extra_rows=extra,method='MULTISET_EXCEPT_ALL')
        return result
    def q(c):return F.col('`'+c.replace('`','``')+'`')
    def qc(side,c):return F.col(side+'.`'+c.replace('`','``')+'`')
    invalid={}
    for label,frame in [('source',source),('target',target)]:
        null=q(keys[0]).isNull()
        for key in keys[1:]:null=null|q(key).isNull()
        count_name='__comparison_count'
        while count_name in keys:
            count_name+='_'
        invalid[label]={'null_key_rows':frame.filter(null).count(),
            'duplicate_key_groups':frame.groupBy(*[q(c) for c in keys]).agg(F.count(F.lit(1)).alias(count_name)).filter(q(count_name)>1).count()}
    result['key_checks']=invalid
    if any(v for record in invalid.values() for v in record.values()):
        result.update(status='INVALID_COMPARISON',reason='NONUNIQUE_OR_NULL_KEYS');return result
    a,b=source.alias('a'),target.alias('b')
    condition=qc('a',keys[0])==qc('b',keys[0])
    for c in keys[1:]:condition=condition&(qc('a',c)==qc('b',c))
    missing=a.join(b,condition,'left_anti').count();extra=b.join(a,condition,'left_anti').count()
    joined=a.join(b,condition,'inner');differences={}
    for c in columns:
        if c in keys:continue
        x,y=qc('a',c),qc('b',c);equal=x.eqNullSafe(y)
        if c in tolerances:
            # Non-finite values must agree exactly; NULLs stay distinct from data.
            equal=equal|((x.isNotNull()&y.isNotNull())&(F.abs(x-y)<=F.lit(tolerances[c])))
        differences[c]=joined.filter(~F.coalesce(equal,F.lit(False))).count()
    result.update(status='MATCH' if not missing and not extra and not any(differences.values()) else 'MISMATCH',
        missing_keys=missing,extra_keys=extra,column_mismatch_counts=differences,method='KEYED_VALUES')
    return result
