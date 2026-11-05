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
from ssis_migration.intake import classify_dependency
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
    st.caption('Target naming uses your catalog/schema and lowercase table names. Object overrides are optional. Destination writes still need a load strategy.')
    description = st.text_area('Package context (optional)')
    default_schema = st.text_input('Default SQL Server schema', value='dbo')
    ai = st.checkbox('Enable AI-assisted legacy generation', value=False, help='The source-graph planner uses deterministic native translation. AI fallback is currently available in legacy warehouse mode.')
    strict = st.checkbox('Strict mode', value=False, help='Graph mode still writes review drafts with named pending sections. Errors block execution; strict CLI exits nonzero when review is required. Legacy mode withholds incomplete notebooks.')
    dry_run = st.checkbox('Dry run', value=False, help='Parse, resolve, and preview the plan without generating a final notebook or calling AI.')
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
        if not isinstance(object_mappings, dict) or not isinstance(dependency_scopes, dict):
            raise ValueError('Both settings must be JSON objects.')
    except ValueError as exc:
        object_mappings, dependency_scopes = {}, {}
        configuration_errors.append(f'Mapping configuration: {exc}')


upload_tab, results_tab, artifacts_tab, ddl_tab = st.tabs(['Inputs', 'Results', 'Artifacts', 'DDL preview'])
with upload_tab:
    mode = st.radio('Package source', ['Upload a .dtsx package', SAMPLE_MODE], horizontal=True)
    package = st.file_uploader('SSIS package', type=['dtsx']) if mode != SAMPLE_MODE else None
    if mode == SAMPLE_MODE: st.info('Uses the WWI sample and its adjacent connection mapping.')
    deps = st.file_uploader('Supporting files (optional)', type=['sql', 'txt', 'json'], accept_multiple_files=True)
    procedures, ddls, mappings = [], [], []
    errors = list(configuration_errors)
    for upload in deps or []:
        raw = upload.getvalue()
        role = classify_dependency(upload.name, raw)
        options = ['procedures', 'ddl', 'mixed_sql', 'connection_mapping', 'ignore']
        default = role if role in options else 'ignore'
        role = st.selectbox(f'Role: {upload.name}', options, index=options.index(default),
                            key='role_' + hashlib.sha256(raw + upload.name.encode()).hexdigest())
        if role == 'ignore':
            st.caption(f'{upload.name}: not supplied to the backend.')
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
    if len(mappings) > 1: errors.append('Supply one connection mapping file; it may contain multiple mappings.')
    for error in errors: st.error(error)
    st.write(f'Procedure files: {len(procedures)} · DDL files: {len(ddls)}')
    available = SAMPLE_PACKAGE.is_file() if mode == SAMPLE_MODE else package is not None
    valid = available and not errors and bool(catalog.strip() and default_schema.strip() and layer.strip())
    if st.button('Analyze & generate' if not dry_run else 'Preview assessment', type='primary', disabled=not valid):
        from ssis_migration.migrate import run_migration
        try:
            st.session_state.result = None
            st.session_state.output = None
            if mode == SAMPLE_MODE:
                source = SAMPLE_PACKAGE
            else:
                name = Path(package.name.replace('\\', '/')).name
                source = workspace / 'packages' / hashlib.sha256(package.getvalue()).hexdigest() / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(package.getvalue())
            output = workspace / 'artifacts'
            with st.spinner('Inspecting source work and generating assessment artifacts…'):
                report = run_migration(str(source), str(output), catalog=catalog.strip(),
                    connection_mapping=mappings[0] if mappings else None, base_path=str(ROOT),
                    procedures_paths=procedures, ddl_paths=ddls, layer=layer.strip(),
                    target_schema=target_schema.strip() or None, description=description,
                    default_schema=default_schema.strip(), ai=ai, strict_mode=strict, dry_run=dry_run,
                    planner_mode='graph' if planner_choice == 'Source graph' else 'legacy',
                    object_mappings=object_mappings, dependency_scopes=dependency_scopes)
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
                        st.write(f"Pending operations: {generation.get('pending_operations', 0)}")
                    pending_count = assessment.get('planning', {}).get('pending_section_count', len(pending_items))
                    st.write(f'Pending implementation sections: {pending_count}')
                    st.write(f"Execution readiness: **{assessment.get('execution_readiness', {}).get('status', 'NOT_ASSESSED')}**")
                    st.json(assessment['source_accounting'])
                for key, label in [('notebook','Download notebook draft'), ('review','Download review summary')]:
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
