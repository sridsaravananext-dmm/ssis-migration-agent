from __future__ import annotations
import hashlib
import json
import sys
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / 'src') not in sys.path: sys.path.insert(0, str(ROOT / 'src'))
from ssis_migration.intake import classify_dependency,stage_packages
from ssis_migration.procedure_file_loader import read_sql_file

SAMPLE_PACKAGE = ROOT / 'examples/wwi/DailyETLMain.dtsx'
RUN_ROOT = Path(tempfile.gettempdir()) / 'ssis_conversion_app'
SAMPLE_MODE = 'Use WWI sample package'
st.set_page_config(page_title='SSIS Conversion & Assessment', page_icon='🔄', layout='wide')
st.title('SSIS Conversion & Assessment')
st.caption('Generate native Spark code, inspect coverage, and identify work that needs human review.')
for key, value in {'session_id': uuid4().hex, 'result': None, 'output': None}.items():
    st.session_state.setdefault(key, value)
workspace = RUN_ROOT / st.session_state.session_id

with st.sidebar:
    st.header('Configuration')
    catalog = st.text_input('Target catalog', value='dev_catalog')
    selected_layer = st.selectbox('Logical layer', ['Unspecified', 'Bronze', 'Silver', 'Gold', 'Multi-layer', 'Custom'])
    layer = st.text_input('Custom layer') if selected_layer == 'Custom' else selected_layer.lower()
    target_schema = st.text_input('Target schema (optional)', help='Propose catalog.schema.lowercase_table names. Leave blank to retain target placeholders. Enter only the schema name.')
    st.caption('Target naming uses your catalog/schema and lowercase table names. Object overrides are optional. Source inserts require existing mapped tables and captured column mappings.')
    description = st.text_area('Package context (optional)')
    default_schema = st.text_input('Default SQL Server schema', value='dbo')
    ai = st.checkbox('Enable AI-assisted legacy generation', value=False, help='The source-graph planner uses deterministic native translation. AI fallback is currently available in legacy warehouse mode.')
    strict = st.checkbox('Strict mode', value=False, help='Graph mode still writes review drafts with named pending sections. Errors block execution; strict CLI exits nonzero when review is required. Legacy mode withholds incomplete notebooks.')
    dry_run = st.checkbox('Dry run', value=False, help='Parse, resolve, and preview the plan without generating a final notebook or calling AI.')
    write_choice = st.selectbox('Destination writes', ['Preserve source behavior', 'Review only', 'Append to existing mapped tables'],
        help='Append preserves separate source TRUNCATE tasks. It requires exact captured column mappings/types and creates no tables.')
    audit_choice=st.selectbox('Audit framework',['Target audit framework','Preserve source auditing'])
    audit_catalog=None;audit_schema=None;audit_capture_stats=True;audit_value_text='{}';audit_rules_text='{}';audit_contract_text='{}'
    if audit_choice=='Target audit framework':
        with st.expander('Target audit configuration'):
            audit_catalog=st.text_input('Audit catalog (optional)',help='Defaults to the target catalog.') or None
            audit_schema=st.text_input('Existing audit schema',help='Contains your existing audit_notebooks and audit_tables. The tool does not create these tables.') or None
            audit_capture_stats=st.checkbox('Capture target table statistics',value=True)
            audit_value_text=st.text_area('Business audit value bindings (JSON)',value='{}',
                help='Explicit bridges for values still consumed by business SQL. Example: {"User::AuditKeyNormal":"runtime_input"}. notebook_key requires an Int64 source declaration.')
            audit_rules_text=st.text_area('Source audit classification rules (JSON)',value='{}',
                help='Add INSERT column signatures, connection-scoped audit objects, or package-scoped audit leaf-task replacements. Unknown business/recovery work is retained.')
            audit_contract_text=st.text_area('Target audit storage contract (JSON)',value='{}',
                help='Override target table names, canonical column mappings and lifecycle statuses. Required key/time/status roles cannot be omitted.')
    with st.expander('Physical mappings and dependency connections'):
        planner_choice = st.selectbox('Planning engine', ['Source graph', 'Legacy warehouse rules'])
        object_text = st.text_area('Physical object mappings (JSON)', value='{}',
            help='Optional overrides. Example: {"source|dbo.Customer": {"target": "catalog.bronze.customer", "layer": "bronze"}}. Unscoped keys apply across connections; scoped keys take precedence.')
        scope_text = st.text_area('Dependency file connections (JSON)', value='{}',
            help='Example: {"source_procs.sql": "Project.ConnectionManagers[Source]"}. Disambiguates same-named definitions in different databases.')
    configuration_errors = []
    try:
        object_mappings = json.loads(object_text)
        dependency_scopes = json.loads(scope_text)
        audit_value_bindings=json.loads(audit_value_text)
        source_audit_rules=json.loads(audit_rules_text);audit_contract=json.loads(audit_contract_text)
        if not isinstance(object_mappings, dict) or not isinstance(dependency_scopes, dict):
            raise ValueError('Both settings must be JSON objects.')
        if not isinstance(audit_value_bindings,dict):raise ValueError('Audit value bindings must be a JSON object.')
        if not isinstance(source_audit_rules,dict) or not isinstance(audit_contract,dict):raise ValueError('Audit rules and storage contract must be JSON objects.')
    except ValueError as exc:
        object_mappings, dependency_scopes, audit_value_bindings,source_audit_rules,audit_contract = {}, {}, {},{},{}
        configuration_errors.append(f'Mapping configuration: {exc}')


upload_tab, results_tab, artifacts_tab, ddl_tab = st.tabs(['Inputs', 'Results', 'Artifacts', 'DDL preview'])
with upload_tab:
    mode = st.radio('Package source', ['Upload .dtsx packages', SAMPLE_MODE], horizontal=True)
    packages = st.file_uploader('SSIS packages', type=['dtsx'],accept_multiple_files=True) if mode != SAMPLE_MODE else []
    if mode == SAMPLE_MODE: st.info('Uses the WWI sample and its adjacent connection mapping.')
    deps = st.file_uploader('Supporting files (optional)', type=['sql', 'txt', 'json','zip','py','html','ipynb'], accept_multiple_files=True)
    procedures, ddls, mappings, references = [], [], [], []
    errors = list(configuration_errors)
    for upload in deps or []:
        raw = upload.getvalue()
        role = classify_dependency(upload.name, raw)
        options = ['procedures', 'ddl', 'mixed_sql', 'connection_mapping','reference', 'ignore']
        default = role if role in options else 'ignore'
        role = st.selectbox(f'Role: {upload.name}', options, index=options.index(default),
                            key='role_' + hashlib.sha256(raw + upload.name.encode()).hexdigest(),
                            help='Reference inspects target notebooks/libraries and ZIP members without executing them or treating their DDL as SQL Server definitions.')
        if role == 'ignore':
            st.caption(f'{upload.name}: not supplied to the backend.')
            continue
        if classify_dependency(upload.name,raw)=='reference' and role not in {'reference','ignore'}:
            errors.append(f'{upload.name}: use the reference role for notebook/archive inputs. Supply SQL Server definitions as SQL/text files.')
            continue
        if role == 'connection_mapping' and classify_dependency(upload.name, raw) != 'connection_mapping':
            errors.append(f'{upload.name}: invalid connection mapping JSON.')
            continue
        name = Path(upload.name.replace('\\', '/')).name
        saved = workspace / 'uploads' / hashlib.sha256(raw).hexdigest() / name
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(raw)
        # A mixed file contributes to BOTH collections; every uploaded file is consumed.
        if role in {'procedures', 'mixed_sql'}: procedures.append(str(saved))
        if role in {'ddl', 'mixed_sql'}: ddls.append(str(saved))
        if role == 'connection_mapping': mappings.append(str(saved))
        if role == 'reference':references.append(str(saved))
    if len(mappings) > 1: errors.append('Supply one connection mapping file; it may contain multiple mappings.')
    for error in errors: st.error(error)
    st.write(f'Procedure files: {len(procedures)} · DDL files: {len(ddls)} · Reference inputs: {len(references)}')
    available = SAMPLE_PACKAGE.is_file() if mode == SAMPLE_MODE else bool(packages)
    valid = available and not errors and bool(catalog.strip() and default_schema.strip() and layer.strip())
    if st.button('Analyze & generate' if not dry_run else 'Preview assessment', type='primary', disabled=not valid):
        from ssis_migration.migrate import run_migration
        try:
            st.session_state.result = None
            st.session_state.output = None
            if mode == SAMPLE_MODE:
                source = SAMPLE_PACKAGE
            else:
                source=stage_packages([(p.name,p.getvalue()) for p in packages],workspace/'packages')
            output = workspace / 'artifacts'
            with st.spinner('Inspecting source work and generating assessment artifacts…'):
                report = run_migration(str(source), str(output), catalog=catalog.strip(),
                    connection_mapping=mappings[0] if mappings else None, base_path=str(ROOT),
                    procedures_paths=procedures, ddl_paths=ddls, layer=layer.strip(),
                    target_schema=target_schema.strip() or None, description=description,
                    default_schema=default_schema.strip(), ai=ai, strict_mode=strict, dry_run=dry_run,
                    planner_mode='graph' if planner_choice == 'Source graph' else 'legacy',
                    object_mappings=object_mappings, dependency_scopes=dependency_scopes,
                    references_paths=references,
                    audit_mode='target' if audit_choice=='Target audit framework' else 'source',audit_catalog=audit_catalog,
                    audit_schema=audit_schema,audit_capture_table_stats=audit_capture_stats,audit_value_bindings=audit_value_bindings,
                    source_audit_rules=source_audit_rules,audit_contract=audit_contract,
                    destination_write_mode={'Preserve source behavior':'source','Review only':'review','Append to existing mapped tables':'append_existing'}[write_choice])
            st.session_state.result = report
            st.session_state.output = str(output)
            st.info(f"Assessment finished: {report['status']}. See Results for findings.")
        except Exception as exc:
            st.error(f'Assessment failed: {exc}')

with results_tab:
    report = st.session_state.result
    if report is None:
        st.info('Analyze a package to view its assessment.')
    else:
        cols = st.columns(4)
        for col, label, key in zip(cols, ['Discovered', 'Analyzed', 'Generated notebooks', 'Needs review'],
                ['package_count', 'analyzed_package_count', 'generated_package_count', 'review_required_package_count']):
            col.metric(label, report.get(key, 0))
        st.write(f"Status: **{report['status']}**")
        st.info('Legacy behavioral match: not measured. Static generation coverage is not an accuracy score.')
        for entry in report['packages']:
            # Streamlit 1.38 (the supported minimum) rejects nested expanders.
            # A package card can safely contain the pending-sections expander.
            with st.container(border=True):
                st.markdown(f"**{entry['name']} — {entry['status']}**")
                if entry.get('error'): st.error(entry['error'])
                if entry.get('message'): st.info(entry['message'])
                if entry.get('protection_reason'):
                    st.write(f"Preserved notebook: **{entry['protection_reason']}**. Execution readiness: **REVALIDATION_REQUIRED**")
                pending_items = []
                pending_path = entry.get('artifacts', {}).get('pending_items')
                if pending_path and Path(pending_path).is_file():
                    pending_items = json.loads(Path(pending_path).read_text())
                assessment = entry.get('assessment')
                if assessment:
                    generation = assessment['generation']
                    percent = generation['operation_coverage_percent']
                    if generation.get('status') == 'NOT_RUN' or entry.get('notebook_status') == 'DRY_RUN' or report.get('mode') == 'DRY_RUN':
                        st.info('Dry run: notebook generation and static code checks were not run. Turn off Dry run to generate a review draft.')
                    else:
                        st.write(f"Operations passing static checks: {generation['statically_valid_operations']} / {generation['operation_count']}")
                        if percent is not None: st.write(f'Static operation coverage: {percent}%')
                        st.caption(generation['basis'])
                        st.caption(f"Container/control bookkeeping operations excluded: {generation.get('structural_operation_count', 0)}")
                        if generation.get('audit_replaced_operation_count'):
                            st.caption(f"Source audit operations replaced, excluded from coverage: {generation['audit_replaced_operation_count']}")
                        st.write(f"Pending operations: {generation.get('pending_operations', 0)}")
                    pending_count = assessment.get('planning', {}).get('pending_section_count', len(pending_items))
                    st.write(f'Pending implementation sections: {pending_count}')
                    st.write(f"Execution readiness: **{assessment.get('execution_readiness', {}).get('status', 'NOT_ASSESSED')}**")
                    st.json(assessment['source_accounting'])
                    if assessment.get('runtime_bindings'):
                        with st.expander('Native runtime bindings'):
                            st.caption('External values use the notebook ssis_runtime_values JSON widget. Bound variables require step_id=all.')
                            st.json(assessment['runtime_bindings'])
                    if assessment.get('target_audit'):
                        with st.expander('Target audit replacement'):
                            st.json(assessment['target_audit'])
                if assessment and assessment.get('supporting_references'):
                    st.caption('Supporting notebooks were inspected as reference data; no reference code was applied.')
                    st.json(assessment['supporting_references'])
                for key, label in [('notebook','Download notebook draft'), ('review','Download review summary'),('reference_review','Download supporting-file review')]:
                    artifact = entry.get('artifacts', {}).get(key)
                    if artifact and Path(artifact).is_file():
                        st.download_button(label, Path(artifact).read_bytes(), file_name=Path(artifact).name,
                            key=key + '_' + hashlib.sha256(artifact.encode()).hexdigest())
                if pending_items:
                    with st.expander(f'Pending implementation sections ({len(pending_items)})'):
                        for pending in pending_items:
                            names = ', '.join(p['name'] for p in pending.get('procedures', []))
                            st.write(f"**{names or pending['kind']}** — {pending['source_id']}")
                            st.caption(f"{pending['pending_id']} · {', '.join(pending['reason_codes'])}")
                            context = pending.get('source_variable_context')
                            if context:
                                st.write(f"Source variable: **{context['reference']}** — {context['status']}")
                                if context.get('expression'):
                                    st.caption('Declared SSIS expression; runtime evaluation and bindings require review.')
                                    st.code(context['expression'], language='text')
                                if context.get('active_value_kind') == 'VALUE' and context.get('value') is not None:
                                    st.caption('Captured variable value; runtime changes and overrides require review.')
                                    st.code(str(context['value']), language='text')
                                if context.get('references'):
                                    st.caption('Runtime references: ' + ', '.join(ref['reference'] for ref in context['references']))
                for item in entry.get('findings', []):
                    st.write(f"**{item['code']}** — {item['message']}")
                    st.caption(f"Source: {item['source_id']} · Finding: {item['finding_id']}")
        archive = workspace / 'assessment_artifacts.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
            for file in Path(st.session_state.output).rglob('*'):
                if file.is_file(): zipped.write(file, file.relative_to(st.session_state.output))
        with st.expander('All diagnostic files'):
            st.caption('For debugging and traceability. Review the summary and notebook draft first; JSON files are not scripts to run.')
            st.download_button('Download all diagnostics (ZIP)', archive.read_bytes(), file_name='assessment_artifacts.zip')

with artifacts_tab:
    if not st.session_state.output:
        st.info('Analyze a package to browse its artifacts.')
    else:
        base = Path(st.session_state.output)
        files = sorted(str(p.relative_to(base)) for p in base.rglob('*') if p.is_file())
        if not st.checkbox('Show diagnostic artifacts', value=False):
            files = [name for name in files if name.endswith(('_review.md', '_Migration.ipynb', '/pending_items.json'))]
        if files:
            selected = st.selectbox('Artifact', files)
            path = base / selected
            st.code(path.read_text(encoding='utf-8', errors='replace')[:30000], language='json' if path.suffix in {'.json', '.ipynb'} else None)
            st.download_button('Download this artifact', path.read_bytes(), file_name=path.name)
with ddl_tab:
    if not ddls: st.info('Supply DDL files to preview them here.')
    for path in ddls:
        with st.expander(Path(path).name): st.code(read_sql_file(path), language='sql')
