"""Native transforms from explicit SSIS lineage; no name-based join inference."""
from ssis_migration.mapping import ConversionGap,translate_sql


def col(name):return 'F.col('+repr('`'+name.replace('`','``')+'`')+')'
def lineage_id(value):return str(value).removeprefix('#{').removesuffix('}')
def kind(column):
    value=str(column.get('data_type','')).lower()
    result={'i1':'tinyint','ui1':'smallint','i2':'smallint','ui2':'int','i4':'int','ui4':'bigint','i8':'bigint',
        'bool':'boolean','r4':'float','r8':'double','wstr':'string','dbdate':'date','dbtimestamp':'timestamp','dbtimestamp2':'timestamp','bytes':'binary'}.get(value)
    if value in {'decimal','numeric'}:
        p,s=column.get('precision'),column.get('scale')
        if str(p).isdigit() and str(s).isdigit() and 1<=int(p)<=38 and 0<=int(s)<=int(p):result=f'decimal({int(p)},{int(s)})'
    if not result:raise ConversionGap('TRANSFORM_TYPE_REQUIRED','Captured output data type/precision is unsupported or missing.')
    return result


def output(port):
    columns=port.get('columns',[])
    names=[c.get('name') for c in columns]
    ids=[str(c.get('lineage_id')) for c in columns]
    if not names or any(not n for n in names) or len(set(names))!=len(names) or None in [c.get('lineage_id') for c in columns] or len(set(ids))!=len(ids):
        raise ConversionGap('COLUMN_LINEAGE_UNRESOLVED','Output names and lineage IDs must be nonempty and unique.')
    return names,{str(c['lineage_id']):c['name'] for c in columns}


def union_all(component,linked,port,name):
    names,lineage=output(port);types={c['name']:kind(c) for c in port['columns']};lines=[];frames=[]
    if len(linked)!=len(component.inputs) or len(linked)<2:raise ConversionGap('UNION_MAPPING_REQUIRED','Union All requires every input to be resolved.')
    for i,(frame,cols,source_ids) in enumerate(linked):
        pairs={}
        for c in component.inputs[i].get('columns',[]):
            target=lineage.get(lineage_id(c.get('properties',{}).get('OutputColumnLineageID')))
            source=source_ids.get(str(c.get('lineage_id')))
            if not target or source not in cols or target in pairs:raise ConversionGap('UNION_MAPPING_REQUIRED','Map every Union input lineage to one explicit output lineage.')
            pairs[target]=source
        if set(pairs)!=set(names):raise ConversionGap('UNION_MAPPING_REQUIRED','Missing Union input/output mappings; NULL padding is not inferred.')
        lines += [f'_union_types = {{f.name:f.dataType.simpleString() for f in {frame}.schema.fields}}']
        for target,source in pairs.items():
            lines += [f'if _union_types[{source!r}] != {types[target]!r}:',"    raise RuntimeError('Union source type differs from captured output; implicit widening is disabled.')"]
        projected=name+'_input'+str(i);frames.append(projected)
        lines += [projected+' = '+frame+'.select('+', '.join(col(pairs[n])+'.alias('+repr(n)+')' for n in names)+')']
    lines += [name+' = '+'.unionByName('.join(frames)+')'*(len(frames)-1)]
    return lines,(name,names,lineage)


def sort(component,source,port,name):
    frame,cols,lineage=source;keys=[];lines=[]
    for c in component.inputs[0].get('columns',[]):
        props=c.get('properties',{});attrs=c.get('attributes',{})
        position=str(props.get('SortKeyPosition',attrs.get('sortKeyPosition','0')))
        if position=='0':continue
        if str(c.get('data_type','')).lower() in {'str','wstr','guid','bytes','r4','r8'}:
            raise ConversionGap('SORT_COLLATION_REQUIRED','Captured string/GUID/NaN sort types need a reviewed comparison policy.')
        try:order=int(position)
        except ValueError:raise ConversionGap('SORT_MAPPING_REQUIRED','Sort key position must be a signed integer.')
        ref=lineage.get(str(c.get('lineage_id')))
        if ref not in cols or str(props.get('ComparisonFlags',attrs.get('comparisonFlags','0')))!='0':
            raise ConversionGap('SORT_MAPPING_REQUIRED','Sort key lineage/collation must be explicit.')
        keys.append((order,ref))
    if not keys or len({abs(k) for k,c in keys})!=len(keys):raise ConversionGap('SORT_MAPPING_REQUIRED','Sort priorities must be unique.')
    # Windows string/GUID collation is deliberately not approximated.
    lines += [f'_sort_types = {{f.name:f.dataType.simpleString() for f in {frame}.schema.fields}}']
    for order,c in keys:
        lines += [f'if _sort_types[{c!r}] in (\'string\',\'binary\',\'float\',\'double\'):',"    raise RuntimeError('Sort requires reviewed string/GUID/NaN ordering.')"]
    eliminate=str(component.properties.get('EliminateDuplicates','false')).lower()
    if eliminate not in {'true','false','0','1'}:raise ConversionGap('SORT_MAPPING_REQUIRED','Unknown duplicate-elimination policy.')
    if eliminate in {'true','1'}:
        if {c for k,c in keys}!=set(cols):raise ConversionGap('SORT_DUPLICATE_REVIEW','Dropping duplicates on only some columns requires a source survivor-selection policy.')
        lines += [name+' = '+frame+'.dropDuplicates('+repr(cols)+')'];frame=name
    lines += [name+' = '+frame+'.sort('+', '.join(col(c)+('.asc_nulls_first()' if k>0 else '.desc_nulls_last()') for k,c in sorted(keys,key=lambda x:abs(x[0])))+')']
    for c in port.get('columns',[]):
        source_id=c.get('properties',{}).get('SourceInputColumnLineageID')
        if source_id is not None and c.get('name')!=lineage.get(lineage_id(source_id)):
            raise ConversionGap('SORT_MAPPING_REQUIRED','Sort output renames require explicit projection mapping.')
    out_lineage=dict(lineage)
    out_lineage.update({str(c['lineage_id']):c['name'] for c in port.get('columns',[]) if c.get('name') in cols and c.get('lineage_id') is not None})
    return lines,(name,list(cols),out_lineage)


def conversion(component,source,port,name):
    frame,cols,lineage=source;lines=[name+' = '+frame];cols=list(cols);lineage=dict(lineage)
    widen={'tinyint':{'smallint','int','bigint'},'smallint':{'int','bigint'},'int':{'bigint'},'float':{'double'}}
    for c in port.get('columns',[]):
        p=c.get('properties',{});ref=lineage.get(lineage_id(p.get('SourceInputColumnLineageID')))
        target=c.get('name');dtype=kind(c)
        if ref not in cols or not target or target in cols:raise ConversionGap('CONVERSION_MAPPING_REQUIRED','Conversion requires explicit source lineage and a new unique output column.')
        allowed=sorted({dtype}|{s for s,targets in widen.items() if dtype in targets})
        lines += [f'_conversion_type = next(f.dataType.simpleString() for f in {frame}.schema.fields if f.name == {ref!r})',
            f'if _conversion_type not in {allowed!r}:',"    raise RuntimeError('Conversion requires an explicit lossless source/target type policy.')"]
        expression=col(ref)+'.cast('+repr(dtype)+')'
        if dtype=='string':
            length=c.get('length')
            if not str(length).isdigit() or int(length)<1:raise ConversionGap('CONVERSION_MAPPING_REQUIRED','Converted WSTR length must be captured.')
            expression='F.when(F.length(F.encode('+col(ref)+", 'UTF-16LE')) > "+str(2*int(length))+", F.raise_error('Conversion string truncation')).otherwise("+expression+')'
        lines += [name+' = '+name+'.withColumn('+repr(target)+', '+expression+')']
        cols.append(target)
        if c.get('lineage_id') is None:raise ConversionGap('COLUMN_LINEAGE_UNRESOLVED','Conversion output lineage is required.')
        lineage[str(c['lineage_id'])]=target
    return lines,(name,cols,lineage)


def aggregate(component,source,port,name):
    frame,cols,source_ids=source;names,lineage=output(port);groups=[];aggregates=[];lines=[];casts=[]
    for c in port['columns']:
        p=c.get('properties',{});op=str(p.get('AggregationType','0'));ref=source_ids.get(lineage_id(p.get('AggregationColumnId')))
        if str(p.get('AggregationComparisonFlags','0'))!='0':raise ConversionGap('AGGREGATE_COLLATION_REQUIRED','String comparison flags need a reviewed target collation.')
        if op not in {'0','1','2','3','4','5','6','7'} or (op!='2' and ref not in cols):raise ConversionGap('AGGREGATE_MAPPING_REQUIRED','Unsupported aggregation or unresolved source lineage.')
        target=c['name'];dtype=kind(c)
        if op in {'0','6','7'} and dtype in {'string','binary','float','double'}:
            raise ConversionGap('AGGREGATE_COLLATION_REQUIRED','Captured grouping/min/max comparison type needs explicit policy.')
        if op in {'0','3','6','7'}:
            lines += [f'_agg_source_type = next(f.dataType.simpleString() for f in {frame}.schema.fields if f.name == {ref!r})',
                "if _agg_source_type in ('string','binary','float','double'):","    raise RuntimeError('Aggregate comparison requires reviewed collation/NaN semantics.')"]
        if op=='0':
            lines += [f'if _agg_source_type != {dtype!r}:',"    raise RuntimeError('Aggregate group type differs from captured output.')"]
            groups.append((ref,target));continue
        function={'1':'count','2':'count','3':'count_distinct','4':'sum','5':'avg','6':'min','7':'max'}[op]
        if op in {'4','5'}:
            lines += [f'_agg_source_type = next(f.dataType.simpleString() for f in {frame}.schema.fields if f.name == {ref!r})',
                "if _agg_source_type not in ('tinyint','smallint','int','bigint') and not _agg_source_type.startswith('decimal('):",
                "    raise RuntimeError('Numeric aggregate requires an exact captured integer/decimal source.')"]
        expression=('F.count(F.lit(1))' if op=='2' else 'F.'+function+'('+col(ref)+')')
        if op=='4':
            # Native ANSI mode must reject aggregate arithmetic overflow.
            lines += ["if spark.conf.get('spark.sql.ansi.enabled').lower() != 'true':", "    raise RuntimeError('SUM requires Spark ANSI overflow checks.')"]
        aggregates.append(expression+'.alias('+repr(target)+')');casts.append((target,dtype,c.get('data_type')))
    if not groups:raise ConversionGap('AGGREGATE_EMPTY_INPUT_REVIEW','Ungrouped SSIS empty-input behavior needs explicit runtime verification.')
    if not aggregates:raise ConversionGap('AGGREGATE_MAPPING_REQUIRED','At least one explicit aggregation is required.')
    lines += [name+' = '+frame+'.groupBy('+', '.join(col(ref)+'.alias('+repr(target)+')' for ref,target in groups)+').agg('+', '.join(aggregates)+')']
    for target,dtype,source_type in casts:
        quoted='`'+target.replace('`','``')+'`'
        cast='F.expr('+repr('try_cast('+quoted+' AS '+dtype+')')+')'
        invalid=col(target)+'.isNotNull() & '+cast+'.isNull()'
        if str(source_type).lower()=='ui4':invalid='('+invalid+') | ('+cast+' > F.lit(4294967295))'
        lines += [name+' = '+name+'.withColumn('+repr(target)+', F.when('+invalid+", F.raise_error('Aggregate output overflow')).otherwise("+cast+'))']
    lines += [name+' = '+name+'.select('+', '.join(col(n) for n in names)+')']
    return lines,(name,names,lineage)


def lookup(component,source,ports,name,connection,config):
    frame,cols,lineage=source;p=component.properties
    if str(p.get('CacheType','0'))!='0' or str(p.get('NoMatchBehavior','0'))!='0':
        raise ConversionGap('LOOKUP_POLICY_UNSUPPORTED','Only full-cache, fail-on-no-match lookups are supported.')
    candidates=[port for port in ports if any(c.get('properties',{}).get('CopyFromReferenceColumn') for c in port.get('columns',[]))]
    port=candidates[0] if len(candidates)==1 else ports[0] if len(ports)==1 else None
    if port is None:raise ConversionGap('LOOKUP_OUTPUT_UNRESOLVED','Resolve the lookup match output from captured copy metadata.')
    query=p.get('SqlCommand')
    if not query:raise ConversionGap('LOOKUP_REFERENCE_REQUIRED','A captured static reference query is required.')
    sql,uses=translate_sql(query,connection,config,query_only=True)
    if any(u['resolution']=='PLACEHOLDER' for u in uses):raise ConversionGap('TARGET_LOCATION_UNRESOLVED','Lookup reference requires a mapped target.')
    pairs=[];replacements={};added={}
    for c in component.inputs[0].get('columns',[]):
        source_name=lineage.get(str(c.get('lineage_id')));props=c.get('properties',{})
        join=props.get('JoinToReferenceColumns') or props.get('JoinToReferenceColumn')
        if join:
            if str(c.get('data_type','')).lower() in {'str','wstr','guid','bytes','r4','r8'}:
                raise ConversionGap('LOOKUP_COLLATION_REQUIRED','Captured lookup key type needs an explicit comparison policy.')
            if source_name not in cols:raise ConversionGap('LOOKUP_MAPPING_REQUIRED','Lookup input lineage is unresolved.')
            pairs.append((source_name,join))
        if props.get('CopyFromReferenceColumn'):
            if source_name not in cols:raise ConversionGap('LOOKUP_MAPPING_REQUIRED','Lookup replacement input lineage is unresolved.')
            replacements[source_name]=props['CopyFromReferenceColumn']
    if not pairs or len(set(a for a,b in pairs))!=len(pairs) or len(set(b for a,b in pairs))!=len(pairs):raise ConversionGap('LOOKUP_MAPPING_REQUIRED','Lookup needs unique explicit equi-join keys.')
    for c in port.get('columns',[]):
        copy=c.get('properties',{}).get('CopyFromReferenceColumn')
        if copy:
            if not c.get('name') or c['name'] in added or c['name'] in cols:raise ConversionGap('LOOKUP_MAPPING_REQUIRED','Lookup added output columns must be unique.')
            added[c['name']]=copy
    reference=name+'_reference';joined=name+'_join'
    needed=sorted({b for a,b in pairs}|set(added.values())|set(replacements.values()))
    lines=[reference+' = spark.sql('+repr(sql)+')',f'_lookup_reference_types = {{f.name:f.dataType.simpleString() for f in {reference}.schema.fields}}',
        f'_lookup_source_types = {{f.name:f.dataType.simpleString() for f in {frame}.schema.fields}}',
        f'if not set({needed!r}).issubset(_lookup_reference_types):',"    raise RuntimeError('Lookup reference columns differ from captured mappings.')"]
    for a,b in pairs:
        lines += [f'if _lookup_source_types[{a!r}] != _lookup_reference_types[{b!r}] or _lookup_source_types[{a!r}] in (\'string\',\'binary\',\'float\',\'double\'):',
            "    raise RuntimeError('Lookup keys need identical numeric/date types; collation/NaN matching is not inferred.')"]
    for a,b in replacements.items():
        lines += [f'if _lookup_source_types[{a!r}] != _lookup_reference_types[{b!r}]:',"    raise RuntimeError('Lookup replacement type differs from input type.')"]
    for c in port.get('columns',[]):
        if c.get('name') in added:
            lines += [f'if _lookup_reference_types[{added[c["name"]]!r}] != {kind(c)!r}:',"    raise RuntimeError('Lookup copied output type differs from captured metadata.')"]
    nonnull=' & '.join(col(b)+'.isNotNull()' for a,b in pairs)
    duplicates='__ssis_lookup_count'
    while duplicates in {b for a,b in pairs}:
        duplicates+='_'
    lines += ['from pyspark import StorageLevel',reference+' = '+reference+'.persist(StorageLevel.DISK_ONLY)',
        '_ssis_cached_frames.append('+reference+')']
    lines += [f'if {reference}.filter({nonnull}).groupBy('+', '.join(col(b) for a,b in pairs)+').agg(F.count(F.lit(1)).alias('+repr(duplicates)+')).filter('+col(duplicates)+' > 1).limit(1).count():',
        "    raise RuntimeError('Lookup reference keys are not unique; source first-match behavior requires review.')"]
    def qualified(side,c):return 'F.col('+repr(side+'.`'+c.replace('`','``')+'`')+')'
    condition=' & '.join('('+qualified('src',a)+' == '+qualified('ref',b)+')' for a,b in pairs)
    lines += [f'if {frame}.alias(\'src\').join({reference}.alias(\'ref\'), {condition}, \'left_anti\').limit(1).count():',
        "    raise RuntimeError('Lookup has unmatched input rows; source fail-on-no-match policy is preserved.')",
        joined+' = '+frame+".alias('src').join("+reference+".alias('ref'), "+condition+", 'inner')"]
    projections=[qualified('ref',replacements[n]) if n in replacements else qualified('src',n) for n in cols]
    projections += [qualified('ref',v) for v in added.values()]
    names=cols+list(added)
    lines += [name+' = '+joined+'.select('+', '.join(e+'.alias('+repr(n)+')' for e,n in zip(projections,names))+')']
    out_lineage=dict(lineage);out_lineage.update({str(c['lineage_id']):c['name'] for c in port.get('columns',[]) if c.get('name') in added and c.get('lineage_id')})
    return lines,(name,names,out_lineage),port,uses


def merge_join(component,linked,port,name):
    if len(component.inputs)!=2 or len(linked)!=2:raise ConversionGap('JOIN_MAPPING_REQUIRED','Merge Join requires exactly two resolved ordered inputs.')
    try:count=int(component.properties['NumKeyColumns'])
    except (KeyError,ValueError,TypeError):raise ConversionGap('JOIN_MAPPING_REQUIRED','Capture NumKeyColumns explicitly.')
    join={'0':'full_outer','1':'left_outer','2':'inner'}.get(str(component.properties.get('JoinType')))
    nulls=str(component.properties.get('TreatNullsAsEqual','true')).lower()
    if not join or count<1 or nulls not in {'true','false','0','1'}:raise ConversionGap('JOIN_MAPPING_REQUIRED','Unsupported join/null policy.')
    keys=[];lines=[];names,lineage=output(port)
    for i,(frame,cols,ids) in enumerate(linked):
        selected={}
        for c in component.inputs[i].get('columns',[]):
            value=c.get('properties',{}).get('SortKeyPosition',c.get('attributes',{}).get('sortKeyPosition','0'))
            try:position=int(value)
            except (ValueError,TypeError):raise ConversionGap('JOIN_MAPPING_REQUIRED','Invalid source sort-key position.')
            if not position or abs(position)>count:continue
            if str(c.get('data_type','')).lower() in {'str','wstr','guid','bytes','r4','r8'}:
                raise ConversionGap('JOIN_COLLATION_REQUIRED','Captured join key type needs an explicit comparison policy.')
            ref=ids.get(str(c.get('lineage_id')))
            if ref not in cols or abs(position) in selected:raise ConversionGap('JOIN_MAPPING_REQUIRED','Join key lineage/priority is ambiguous.')
            selected[abs(position)]=(ref,position>0)
        if set(selected)!=set(range(1,count+1)):raise ConversionGap('JOIN_MAPPING_REQUIRED','Both inputs must capture every join key position.')
        keys.append([selected[k] for k in range(1,count+1)])
        lines += [f'_join_types_{i} = {{f.name:f.dataType.simpleString() for f in {frame}.schema.fields}}']
    def qualified(side,c):return 'F.col('+repr(side+'.`'+c.replace('`','``')+'`')+')'
    conditions=[]
    for (left,asc),(right,right_asc) in zip(*keys):
        if asc!=right_asc:raise ConversionGap('JOIN_MAPPING_REQUIRED','Input sort directions disagree.')
        lines += [f'if _join_types_0[{left!r}] != _join_types_1[{right!r}] or _join_types_0[{left!r}] in (\'string\',\'binary\',\'float\',\'double\'):',
            "    raise RuntimeError('Merge Join requires compatible numeric/date keys; collation/NaN ordering needs review.')"]
        a,b=qualified('left',left),qualified('right',right)
        conditions.append(a+'.eqNullSafe('+b+')' if nulls in {'true','1'} else '('+a+' == '+b+')')
    projections=[]
    for c in port['columns']:
        ref=lineage_id(c.get('properties',{}).get('InputColumnID'))
        candidates=[(i,state[2][ref]) for i,state in enumerate(linked) if ref in state[2]]
        if len(candidates)!=1:raise ConversionGap('JOIN_MAPPING_REQUIRED','Output must identify one unambiguous source input lineage.')
        side,original=candidates[0];dtype=kind(c)
        lines += [f'if _join_types_{side}[{original!r}] != {dtype!r}:',"    raise RuntimeError('Join output type differs from captured metadata.')"]
        projections.append(qualified('left' if side==0 else 'right',original)+'.alias('+repr(c['name'])+')')
    lines += [name+' = '+linked[0][0]+".alias('left').join("+linked[1][0]+".alias('right'), "+' & '.join(conditions)+', '+repr(join)+').select('+', '.join(projections)+')']
    return lines,(name,names,lineage)
