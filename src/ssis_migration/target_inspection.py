"""Read-only target metadata evidence; never resolve business identities by guess."""
from ssis_migration.mapping import target_table


def inspect_targets(spark,plan):
    grouped={}
    for use in plan.get('metadata',{}).get('mapping_uses',[]):
        name=target_table(use['target']).sql(dialect='databricks')
        grouped.setdefault(name,[]).append({k:use.get(k) for k in ('source_id','source','connection_reference','role','resolution')})
    result=[]
    for name,origins in sorted(grouped.items()):
        record={'target':name,'origins':origins,'status':'NOT_INSPECTED','business_identity_confirmed':False}
        try:
            if not spark.catalog.tableExists(name):record['status']='MISSING'
            else:
                record['columns']=[{'name':f.name,'type':f.dataType.simpleString(),'nullable':f.nullable} for f in spark.table(name).schema.fields]
                rows=spark.sql('DESCRIBE TABLE EXTENDED '+name).filter("col_name = 'Provider'").limit(2).collect()
                record['provider']=rows[0]['data_type'] if len(rows)==1 else None
                record['status']='INSPECTED'
        except Exception as error:
            record.update(status='INSPECTION_FAILED',error_type=type(error).__name__)
        result.append(record)
    return {'scope':'TARGET_METADATA','objects':result,'execution_gate_changed':False,
        'note':'Existence/schema evidence does not establish shared legacy connection identity or workload equivalence.'}
