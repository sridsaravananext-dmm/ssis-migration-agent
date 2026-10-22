"""Generate reviewable artifacts and stop before Databricks deployment/execution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from ssis_migration.adapters.tm1.pipeline import run_tm1_review
from .contract import build_contract, load_mapping
from .emitter import render, validate_rendered, verify_fixtures

HANDOFF = '''# Databricks candidate review

This bundle stops at generation. It does not deploy, open source-system connections,
write persistent tables or claim migration acceptance. Offline implementation may continue
without a manual Databricks review; execution and source equivalence remain unverified.

## First run: synthetic fixtures

1. Read `migration_report.json`, `generation_contract.json` and `capability_report.json`.
   Check the explicit semantic profile and unsupported behavior before running anything.
2. In your Databricks workspace, import `Migration.ipynb` and `Validation.ipynb` as
   Python notebooks named `Migration` and `Validation` in the same workspace folder.
3. Attach an existing cluster with Python/PySpark and notebook support (15.4 LTS or
   newer is the intended runtime; this environment has not run these notebooks).
   No package installation, source-system access or persistent table privileges are needed
   for fixture mode. The utility code, programs, fixtures and expectations are embedded.
4. Run `Validation` from the top. Every supplied fixture must pass bidirectional Spark
   multiset reconciliation and row-count checks. Save its printed JSON output for review.
5. Run `Migration` from the top with `step_id=all`, `input_mode=fixtures`,
   `table_bindings_json={}` and `parameters_json={}`. Inspect the displayed cell snapshots.
   Results are session temporary views, not persistent tables or actual TM1 writes.

## Optional read-only table preview, after fixture review

Supply complete canonical numeric leaf-cell snapshots and, for TI, an ordered row snapshot.
Columns and types must exactly match the target contract. Sparse matrices, consolidations,
string-valued cells, nonintegral values, nulls, aliases, security-filtered cells, hidden rules
or feeders and oversized inputs are rejected/outside this capability. The complete domain
and row count must fit `max_rows`; this is a controlled preview, not a scalable production ETL.

In `Migration`, select one `step_id`, set `input_mode=tables`, and use explicit three-part
names in `table_bindings_json`, e.g.
`{"load":{"cells":"catalog.schema.cell_snapshot","rows":"catalog.schema.ordered_rows"}}`.
These are user-supplied read-only snapshots; the tool never reads legacy servers.
`parameters_json` is keyed by unit, e.g. `{"load":{"pFactor":2}}`; all process parameters
must be supplied either here or explicitly in the reviewed mapping, never guessed from defaults.
The TI record ordinal must uniquely encode source record order. The cell snapshot is read
once and the row snapshot once; provide stable snapshots during the run.
The generated source leaf-cell rules are constant overlays; the query inputs must have no
other rules/feeders, and independent units do not imply source execution order.

## Optional job template

`job.json` and `databricks.yml` are templates only. The latter is JSON-compatible YAML.
After importing the notebooks, set `notebook_root` to that absolute workspace folder and
`existing_cluster_id` to your chosen cluster ID. There are no cluster defaults, schedules
or automated deployment commands. Preview tasks run independent fixture snapshots; the
final validation task depends on all previews as a target testing workflow only.
No source cross-unit dependencies have been inferred. Resolve template variables before
using job.json with the Jobs API. Do not run the DAB/job until you have reviewed it.

## Evidence and next-phase gate

Local checks cover the pure Python target kernel, notebook structure/syntax, source/contract
replay and job graph. PySpark/Databricks execution is pending. The sample fixtures are
synthetic, with manually supplied expectations; they are not source-engine observations.
Passing them does not verify source equivalence, weighted business coverage or acceptance.

Record Databricks Runtime/cluster version, notebook output, actual table bindings, input
snapshot identifiers and independent TM1 observations in your review. Compare every critical
business rule and edge case. Criticality and weights remain unassigned; the minimum 80%
weighted verified semantic gate and all mandatory gates still apply.

Databricks human review is optional for continuing implementation. Same-cube, literal,
acyclic leaf-rule DB reads are supported only with the explicit pre-rule leaf snapshot
profile. Their dependency order and source evidence are in the target contract/report.
Consolidations, feeders, sparse/empty-cell semantics, general MDX functions/aggregations,
dynamic/cross-cube/cyclic rules, TI lifecycle logic, general floating arithmetic,
source transaction/error semantics, full C# compilation/equivalence and other adapters remain
outside this candidate. Continuing implementation does not waive execution, data-equivalence,
critical-behavior or weighted semantic acceptance gates.
'''


def _write(path, value):
    if isinstance(value,str):path.write_text(value,encoding='utf-8')
    else:path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')


def run_tm1_generation(source, output, target_mapping, *, workspace_config=None):
    # The original review/contract path remains available unchanged. No generic
    # review contract is promoted to executable by this opt-in target contract.
    mapping = load_mapping(target_mapping)
    review = run_tm1_review(source,output)
    directory = Path(review['artifacts']['report']).parent
    graph = json.loads((directory/'normalized_ir.json').read_text(encoding='utf-8'))
    contract = build_contract(graph,mapping,workspace_config=workspace_config)
    _write(directory/'generation_contract.json',contract)  # Contract precedes any target code.
    _write(directory/'capability_report.json',contract['capabilities'])
    fixtures = verify_fixtures(contract) if contract['status']=='CANDIDATE_READY' else []
    ready = contract['status']=='CANDIDATE_READY' and all(r['status']=='PASS' for r in fixtures)
    artifacts = {}
    if ready:
        artifacts,fixtures = render(contract)
        validate_rendered(contract,artifacts)
    # Verify source and target inputs again before publishing a candidate.
    for record in graph['evidence_files'] + [mapping['record']]:
        path = Path(record['path'])
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:
            raise ValueError('Evidence changed before target output: '+str(path))
    for name,value in artifacts.items():_write(directory/name,value)
    _write(directory/'fixture_results.json',fixtures)
    _write(directory/'DATABRICKS_REVIEW.md',HANDOFF)
    _write(directory/'target_mapping.json',mapping['document'])
    if workspace_config is not None:
        _write(directory/'workspace_config.json',workspace_config)
    evidence = directory/'source_evidence';evidence.mkdir()
    index = []
    for i,record in enumerate(graph['evidence_files']):
        destination = f'source-{i:04d}.bin'
        raw = Path(record['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise ValueError('Source changed while archiving.')
        (evidence/destination).write_bytes(raw)
        index.append({'file_id':record['file_id'],'original_path':record['path'],'archived_path':'source_evidence/'+destination,'sha256':record['sha256']})
    _write(directory/'source_evidence_index.json',index)
    validations = [{'validator':'source_ir_target_contract_replay','status':'PASS','scope':'SOURCE_AND_TARGET_STRUCTURE'},
                   {'validator':'target_notebook_and_job_validation','status':'PASS' if ready else 'BLOCKED','scope':'TARGET_STATIC'},
                   {'validator':'local_target_fixture_execution','status':'PASS' if ready else 'BLOCKED','scope':'TARGET_KERNEL_ONLY'},
                   {'validator':'databricks_execution','status':'NOT_RUN','scope':'DATABRICKS_EXECUTION'},
                   {'validator':'source_equivalence','status':'NOT_VERIFIED','scope':'SOURCE_SEMANTICS'}]
    _write(directory/'generation_validation.json',validations)
    (directory/'validation_results.json').rename(directory/'source_review_validation.json')
    _write(directory/'validation_results.json',validations)
    report = {'report_version':'2.1','name':review['name'],'source_platform':'tm1','source_format':'tm1_source_bundle_v1',
              'status':'GENERATED' if ready else 'BLOCKED','scope':'BOUNDED_PREVIEW','execution_status':'NOT_RUN',
              'migration_accepted':False,'generation_contract_id':contract['contract_id'],
              'analyzed_package_count':1,'migrated_package_count':0,'generated_artifact_count':len(artifacts),
              'source_files':graph['evidence_files'],'source_diagnostics':graph['diagnostics'],
              'parser_status':review['parser_status'],'metadata_binding_status':review['metadata_binding_status'],
              'strategies':{c['unit_id']:c['decision'] for c in contract['capabilities']},
              'unsupported':{c['unit_id']:c['reasons'] for c in contract['capabilities'] if c['decision']=='UNSUPPORTED'},
              'fixture_results':fixtures,'validation':validations,
              'coverage':{'structural':{'retained_obligations':len(contract['obligation_ids']),
                                         'total_obligations':len(graph['nodes'])+len(graph['edges'])},
                          'semantic':{'weighted_verified_percent':None,'minimum_percent':80,'critical_gates':'UNASSESSED'},
                          'execution':{'databricks':'NOT_RUN'},'data_equivalence':{'source_comparison':'NOT_VERIFIED'}},
              'assumptions':{'source':mapping['record'],'declarations':mapping['document']['semantic_profile'],'verification':'NOT_VERIFIED'},
              'required_human_actions':contract['validation_requirements'],
              'next_phase_gate':'CONTINUE_OFFLINE_IMPLEMENTATION',
              'databricks_human_review':'DEFERRED_BY_USER',
              'artifacts':{name:str(directory/name) for name in [*artifacts,'generation_contract.json','capability_report.json',
                            'fixture_results.json','generation_validation.json','validation_results.json','DATABRICKS_REVIEW.md','target_mapping.json',
                            'source_envelope.json','normalized_ir.json','migration_plan.json','migration_contracts.json',
                            'plan_ir_binding.json','source_evidence_index.json']}}
    report['rule_evaluation'] = {c['unit_id']:{
        'cube':c['program']['model']['cube'], 'evaluation_order':c['program']['evaluation_order'],
        'rules':[{key:r[key] for key in ('coordinates','source_statement_id','source_order','source_span','read_coordinates')}
                 for r in c['program']['rules']],
        'dependencies':c['program']['rule_dependencies'],
    } for c in contract['capabilities'] if c['program'] is not None and c['program']['kind']=='leaf_rules'}
    report['subset_expansion'] = {c['unit_id']:c['program']['subset_bindings'] for c in contract['capabilities']
                                  if c['program'] is not None and c['program'].get('subset_bindings')}
    # Keep the original source-only review report for diagnostics and backward compatibility.
    review['artifacts']['report'] = str(directory/'source_review_report.json')
    review['artifacts']['validation'] = str(directory/'source_review_validation.json')
    _write(directory/'source_review_report.json',review)
    report['artifacts']['report'] = str(directory/'migration_report.json')
    if workspace_config is not None:
        report['workspace_config'] = workspace_config
        report['artifacts']['workspace_config'] = str(directory/'workspace_config.json')
    report['artifacts']['bundle'] = str(directory.with_suffix('.zip'))
    _write(directory/'migration_report.json',report)
    inventory = {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(directory.rglob('*')) if p.is_file()}
    _write(directory/'artifact_inventory.json',{'sha256':inventory,'excludes':['artifact_inventory.json']})
    with zipfile.ZipFile(directory.with_suffix('.zip'),'x',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob('*')):
            if path.is_file():archive.write(path,str(path.relative_to(directory)))
    return report
