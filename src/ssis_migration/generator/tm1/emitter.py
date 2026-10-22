"""Self-contained PySpark previews and reconciliation notebooks from target contracts."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from ssis_migration.generator.job_generator import JobGenerator
from ssis_migration.generator.notebook import NotebookAssembler
from . import runtime
from .contract import require_candidate


def output_types(program):
    result = {c['column']:'STRING' for c in program['model']['coordinates']}
    result[program['model']['value_column']] = 'NUMERIC'
    if program['kind'] == 'cell_query':
        result.update({name:'NUMERIC' for name in program['requests'][0]['axis_positions']})
    return result


def verify_fixtures(contract):
    programs = {c['unit_id']:c['program'] for c in contract['capabilities']}
    results = []
    for case in contract['mapping_source']['document']['fixture_cases']:
        program = programs[case['unit_id']]
        try:
            actual = runtime.execute_program(program, case['cells'], case['rows'], case['parameters'])
            types = output_types(program)
            expected = []
            if len(case['expected']) > program['max_rows']:raise ValueError('Expected output exceeds the preview bound.')
            for row in case['expected']:
                if set(row) != set(types):raise ValueError('Expected-result schema differs from the target contract.')
                expected.append({n:runtime._typed(row[n],t) for n,t in types.items()})
            canonical = lambda rows:Counter(json.dumps(r,sort_keys=True,ensure_ascii=False,allow_nan=False) for r in rows)
            if canonical(actual) != canonical(expected):raise ValueError('Actual rows differ from the independently supplied expected rows.')
            results.append({'case_id':case['case_id'],'unit_id':case['unit_id'],'status':'PASS',
                            'scope':'LOCAL_TARGET_KERNEL_FIXTURE','provenance':case['provenance'],
                            'actual_row_count':len(actual),'expected_row_count':len(expected)})
        except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
            results.append({'case_id':case['case_id'],'unit_id':case['unit_id'],'status':'FAIL',
                            'scope':'LOCAL_TARGET_KERNEL_FIXTURE','issue':str(exc),'provenance':case['provenance']})
    return results


# Only target helpers are executable. All source-derived programs and fixtures
# are JSON data, never interpolated as Python statements or SQL.
SPARK_HELPERS = '''
from pyspark.sql.types import StructType, StructField, StringType, LongType, NumericType
import re

def frame(rows, types):
    normalized = []
    for row in rows:
        if set(row) != set(types):
            raise ValueError('Result schema differs from the generated contract.')
        normalized.append({name:_typed(row[name], kind) for name,kind in types.items()})
    schema = StructType([StructField(name, StringType() if kind == 'STRING' else LongType(), False)
                         for name,kind in types.items()])
    return spark.createDataFrame(normalized, schema=schema)

def read_snapshot(table, types, limit):
    if not isinstance(table, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*\\.[A-Za-z_][A-Za-z0-9_]*\\.[A-Za-z_][A-Za-z0-9_]*', table):
        raise ValueError('Supply an explicit three-part table binding; SQL expressions are not accepted.')
    source = spark.table(table)
    if set(source.columns) != set(types) or len(source.columns) != len(types):
        raise ValueError('Table columns differ from the explicit source mapping.')
    for field in source.schema.fields:
        required = types[field.name]
        if (required == 'STRING' and not isinstance(field.dataType, StringType)) or (required == 'NUMERIC' and not isinstance(field.dataType, NumericType)):
            raise ValueError('Table column type differs from the source declaration: ' + field.name)
    # The extra row is a guard, never a silently truncated input.
    rows = source.limit(limit + 1).collect()
    if len(rows) > limit:
        raise ValueError('Input exceeds the bounded preview limit; use a separately reviewed scalable strategy.')
    return [row.asDict(recursive=True) for row in rows]
'''

PARAMETERS = '''
def binding_json(text):
    def unique(pairs):
        result = {}
        for name,value in pairs:
            if name in result:
                raise ValueError('Duplicate JSON binding key: ' + name)
            result[name] = value
        return result
    def nonfinite(value):
        raise ValueError('Non-finite JSON binding value: ' + value)
    return json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)

dbutils.widgets.text('step_id', 'all', 'Unit ID or all')
dbutils.widgets.dropdown('input_mode', 'fixtures', ['fixtures','tables'], 'Read fixture data or explicit tables')
dbutils.widgets.text('table_bindings_json', '{}', 'Unit to cell/datasource table bindings')
dbutils.widgets.text('parameters_json', '{}', 'Unit to explicit process parameters')
_step_id = dbutils.widgets.get('step_id')
_input_mode = dbutils.widgets.get('input_mode')
_table_bindings = binding_json(dbutils.widgets.get('table_bindings_json'))
_parameter_bindings = binding_json(dbutils.widgets.get('parameters_json'))
if _input_mode not in ('fixtures','tables'):
    raise ValueError('Unknown input mode.')
if _step_id != 'all' and _step_id not in PROGRAMS:
    raise ValueError('Unknown unit ID; no work was silently skipped.')
if not isinstance(_table_bindings, dict) or not isinstance(_parameter_bindings, dict):
    raise ValueError('Bindings must be JSON objects keyed by source unit ID.')
if set(_table_bindings) - set(PROGRAMS) or set(_parameter_bindings) - set(PROGRAMS):
    raise ValueError('Bindings contain an unknown source unit.')
'''

RUN_PREVIEW = '''
for unit_id, program in PROGRAMS.items():
    if _step_id not in ('all', unit_id):
        continue
    if _input_mode == 'fixtures':
        case = next(case for case in CASES if case['unit_id'] == unit_id)
        cells, rows, parameters = case['cells'], case['rows'], case['parameters']
    else:
        binding = _table_bindings.get(unit_id, {})
        if not isinstance(binding, dict) or set(binding) - {'cells','rows'}:
            raise ValueError('Table bindings accept only explicit cells and rows table names.')
        model = program['model']
        cell_types = {c['column']:'STRING' for c in model['coordinates']}
        cell_types[model['value_column']] = 'NUMERIC'
        cells = read_snapshot(binding.get('cells', model['input_table']), cell_types, program['max_rows'])
        rows = []
        parameters = {}
        if program['kind'] == 'row_process':
            rows = read_snapshot(binding.get('rows', program['datasource_table']), program['source_columns'], program['max_rows'])
            parameters = _parameter_bindings.get(unit_id, program['default_parameters'])
        elif 'rows' in binding or unit_id in _parameter_bindings:
            raise ValueError('Unexpected datasource/parameter binding for a query/rule unit.')
    output = execute_program(program, cells, rows, parameters)
    result = frame(output, OUTPUT_TYPES[unit_id])
    view = VIEW_NAMES[unit_id]
    result.createOrReplaceTempView(view)
    print('Bounded preview only:', unit_id, len(output), 'rows; temporary view:', view)
    display(result)
'''

RUN_VALIDATION = '''
validation_results = []
for case in CASES:
    program = PROGRAMS[case['unit_id']]
    actual_rows = execute_program(program, case['cells'], case['rows'], case['parameters'])
    types = OUTPUT_TYPES[case['unit_id']]
    actual = frame(actual_rows, types)
    expected = frame(case['expected'], types)
    # Multiset reconciliation in both directions catches duplicates and omissions.
    missing = expected.exceptAll(actual).count()
    unexpected = actual.exceptAll(expected).count()
    actual_count, expected_count = actual.count(), expected.count()
    if missing or unexpected or actual_count != expected_count:
        raise AssertionError('Fixture reconciliation failed: ' + case['case_id'])
    validation_results.append({'case_id':case['case_id'], 'unit_id':case['unit_id'], 'status':'PASS',
                               'actual_rows':actual_count, 'expected_rows':expected_count,
                               'evidence_kind':case['provenance']['kind']})
print(json.dumps({'scope':'DATABRICKS_TARGET_FIXTURES', 'source_equivalence':'NOT_VERIFIED',
                  'migration_accepted':False, 'results':validation_results}, indent=2))
# Save the cell output as review evidence. This notebook writes no tables or files.
'''


def _notebook(name, blocks, contract_id):
    cells = []
    assembler = NotebookAssembler()
    for index,(kind,source) in enumerate(blocks):
        cell = (assembler._code_cell(source) if kind == 'code' else assembler._markdown_cell(source))
        cell['id'] = 'tm1-cell-' + str(index)
        cells.append(cell)
    notebook = {'nbformat':4,'nbformat_minor':5,
                'metadata':{'application/databricks':{'name':name,'language':'python'},
                            'target_contract_id':contract_id,'execution_mode':'BOUNDED_PREVIEW'},'cells':cells}
    validation = NotebookAssembler.validate(notebook)
    if validation['status'] != 'PASS':raise ValueError('Generated notebook validation failed: ' + str(validation))
    return notebook


def render(contract):
    require_candidate(contract)
    fixture_results = verify_fixtures(contract)
    if any(r['status'] != 'PASS' for r in fixture_results):raise ValueError('Fixture validation failed; executable artifacts were not generated.')
    programs = {c['unit_id']:c['program'] for c in contract['capabilities']}
    types = {name:output_types(program) for name,program in programs.items()}
    cases = contract['mapping_source']['document']['fixture_cases']
    # repr serializes a JSON string as a Python literal; source contents cannot become code.
    data = '\nimport json\n' + '\n'.join(name+' = json.loads('+repr(json.dumps(value,ensure_ascii=False,allow_nan=False))+')'
             for name,value in [('PROGRAMS',programs),('OUTPUT_TYPES',types),('CASES',cases),
                                 ('VIEW_NAMES',{n:'tm1_preview_'+hashlib.sha256(n.encode()).hexdigest()[:16] for n in programs})]) + '\n'
    utility = Path(runtime.__file__).read_text(encoding='utf-8')
    description = ('# TM1 Databricks candidate\n\nBounded dense numeric leaf snapshots only; exact integer arithmetic. '
                   'Generated code has not established source equivalence. Review the target contract and semantic declarations first. '
                   'Inputs are read-only; results appear in session temporary views. No persistent writes, deployment or source connections. '
                   'Independent units do not imply a source execution order.\n\nContract: ' + contract['contract_id'])
    common = [('markdown',description),('code',utility),('code',data),('code',SPARK_HELPERS)]
    preview = _notebook('TM1_Migration', common + [('code',PARAMETERS),('code',RUN_PREVIEW)], contract['contract_id'])
    validation = _notebook('TM1_Validation', common + [('markdown','# Fixture reconciliation\n\nExpected results were explicitly supplied. Synthetic evidence does not verify source behavior.'),('code',RUN_VALIDATION)], contract['contract_id'])
    # These dependencies describe this target test workflow, not inferred TM1 execution dependencies.
    steps = [{'step_id':'preview_'+str(i),'name':unit,'step_type':'BOUNDED_PREVIEW','dependencies':[]}
             for i,unit in enumerate(programs)]
    steps.append({'step_id':'fixture_validation','name':'Databricks fixture reconciliation','step_type':'VALIDATION',
                  'dependencies':[s['step_id'] for s in steps]})
    plan = {'package_name':'TM1CandidateReview','steps':steps,'metadata':{'dependency_scope':'TARGET_TEST_WORKFLOW_ONLY'}}
    job = JobGenerator().generate(plan,'${var.notebook_root}/Migration').to_dict()
    job.pop('job_clusters',None);job.pop('parameters',None)
    job['name'] = 'TM1 bounded candidate review'
    job['tags'] = {'source':'tm1','mode':'bounded_preview','accepted':'false'}
    for i,task in enumerate(job['tasks']):
        task.pop('job_cluster_key',None);task['existing_cluster_id'] = '${var.existing_cluster_id}'
        task['max_retries'] = 0
        if i < len(programs):
            task['notebook_task']['base_parameters'] = {'step_id':list(programs)[i],'input_mode':'fixtures',
                                                       'table_bindings_json':'{}','parameters_json':'{}'}
        else:
            task['notebook_task']['notebook_path'] = '${var.notebook_root}/Validation'
            task['notebook_task']['base_parameters'] = {}
    dab = {'bundle':{'name':'tm1_candidate_review'},
           'variables':{'existing_cluster_id':{'description':'User-selected Databricks cluster with notebook support.'},
                        'notebook_root':{'description':'Absolute workspace folder containing imported Migration and Validation notebooks.'}},
           'resources':{'jobs':{'tm1_candidate_review':job}}}
    return {'Migration.ipynb':preview,'Validation.ipynb':validation,'utilities.py':utility,
            'job.json':job,'databricks.yml':dab,'target_test_plan.json':plan}, fixture_results


def validate_rendered(contract, artifacts):
    """Independent notebook syntax checks plus full re-render; edited code is refused."""
    expected,_ = render(contract)
    if artifacts != expected:raise ValueError('Generated artifacts differ from their replayed source-bound target contract.')
    for name in ('Migration.ipynb','Validation.ipynb'):
        result = NotebookAssembler.validate(artifacts[name])
        if result['status'] != 'PASS':raise ValueError('Notebook syntax/structure validation failed: '+name)
    compile(artifacts['utilities.py'],'utilities.py','exec')
    JobGenerator()._validate_steps(artifacts['target_test_plan.json']['steps'])
