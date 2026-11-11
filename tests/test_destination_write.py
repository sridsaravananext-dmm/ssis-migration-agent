"""Preserve explicit OLE DB inserts, lineage, and fail-on-row-error behavior."""
from copy import deepcopy
import pytest

from ssis_migration.config import AssessmentConfig
from ssis_migration.generator.destination_write import append_code, destination_columns
from ssis_migration.generator.graph_native import dataflow_operations
from ssis_migration.ir.models import ComponentIR, DataFlowIR
from ssis_migration.mapping import ConversionGap, target_table
from ssis_migration.validator.native_spark import validate_native_spark


def destination():
    return ComponentIR(ref_id='D',name='Destination',detected_type='OLE_DB_DESTINATION',
        component_class_id='Microsoft.OLEDBDestination',properties={'AccessMode':'3','OpenRowset':'dbo.Target'},
        inputs=[{'ref_id':'D:i','columns':[
            {'ref_id':'D:c1','name':'cached_src_name','lineage_id':'1','external_metadata_column_id':'D:e1'},
            {'ref_id':'D:c2','name':'other_cached_name','lineage_id':'2','external_metadata_column_id':'D:e2'}],
            'external_columns':[{'ref_id':'D:e1','name':'id','data_type':'i4'},
                                {'ref_id':'D:e2','name':'label','data_type':'wstr','length':'4'}]}])


def flow():
    source=ComponentIR(ref_id='S',detected_type='OLE_DB_SOURCE',properties={'SqlCommand':'SELECT id AS src_id, label AS src_label FROM dbo.Source'},
        outputs=[{'ref_id':'S:o','attributes':{},'columns':[{'name':'src_id','lineage_id':'1'},{'name':'src_label','lineage_id':'2'}]}])
    return DataFlowIR(ref_id='DF',components=[source,destination()],paths=[{'start_id':'S:o','end_id':'D:i'}])


def cfg(**kwargs):
    return AssessmentConfig(object_mappings={'dbo.Source':'c.s.source','dbo.Target':'c.s.target'},**kwargs)


def test_source_mode_generates_existing_table_insert_from_authoritative_column_ids():
    ops,findings,_=dataflow_operations(flow(),cfg())
    write=next(o for o in ops if o['operation_type']=='NATIVE_DESTINATION_WRITE')
    assert 'LOAD_STRATEGY_REQUIRED' not in {f['code'] for f in findings}
    assert '.write.mode("append").insertInto(' in write['code']
    assert 'saveAsTable' not in write['code'] and 'overwrite' not in write['code']
    assert 'UTF-16LE' in write['code'] and 'F.raise_error' in write['code']
    assert validate_native_spark(write['code'])['status']=='PASS'
    columns=destination_columns(destination(),['src_id','src_label'],{'1':'src_id','2':'src_label'})
    assert [(c['source'],c['target']) for c in columns]==[('src_id','id'),('src_label','label')]


def test_review_mode_and_unknown_source_modes_do_not_generate_writes():
    for ir,config in [(flow(),cfg(destination_write_mode='review')),
                      (flow(),cfg())]:
        if config.destination_write_mode=='source': ir.components[1].properties.pop('AccessMode')
        ops,findings,_=dataflow_operations(ir,config)
        assert any(f['code']=='LOAD_STRATEGY_REQUIRED' for f in findings)
        assert not any(o['operation_type']=='NATIVE_DESTINATION_WRITE' for o in ops)


@pytest.mark.parametrize('change', ['missing_external','bad_lineage','duplicate_target','unknown_type','invalid_length','missing_length','bad_decimal','ansi'])
def test_incomplete_or_unsupported_mappings_remain_review_work(change):
    comp=destination()
    if change=='missing_external':comp.inputs[0]['external_columns']=[]
    elif change=='bad_lineage':comp.inputs[0]['columns'][0]['lineage_id']='unknown'
    elif change=='duplicate_target':comp.inputs[0]['external_columns'][1]['name']='ID'
    elif change=='unknown_type':comp.inputs[0]['external_columns'][1]['data_type']='variant'
    elif change=='invalid_length':comp.inputs[0]['external_columns'][1]['length']='-1'
    elif change=='missing_length':comp.inputs[0]['external_columns'][1].pop('length')
    elif change=='bad_decimal':comp.inputs[0]['external_columns'][1].update(data_type='numeric',precision='39',scale='40')
    else:comp.inputs[0]['external_columns'][1]['data_type']='str'
    with pytest.raises(ConversionGap):destination_columns(comp,['src_id','src_label'],{'1':'src_id','2':'src_label'})


def test_config_rejects_unknown_write_modes_and_legacy_explicit_writes():
    with pytest.raises(ValueError):AssessmentConfig(destination_write_mode='overwrite')
    with pytest.raises(ValueError):AssessmentConfig(planner_mode='legacy',destination_write_mode='append_existing')
