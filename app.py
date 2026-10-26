from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import zipfile
from uuid import uuid4

import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT/'src'
if str(SRC) not in sys.path:sys.path.insert(0,str(SRC))

from ssis_migration.adapters.tm1.metadata.catalog import read_json
from ssis_migration.intake import describe_file
from ssis_migration.intake.source_requirements import source_export_guide
from ssis_migration.schemas import validate_document
from ssis_migration.ui import (artifact_archive, artifact_files, bundle_candidates, effective_mapping,
                               bind_connection_metadata, prepare_run_directory,
                               extract_bundle, find_target_mappings, input_inventory, report_summary,
                               run_frontend_migration, validate_bundle_scope)
from ssis_migration.workspace import validate_workspace_config

EXAMPLES = ROOT/'examples'
SAMPLE_PACKAGE = EXAMPLES/'wwi/DailyETLMain.dtsx'
TM1_SAMPLE = EXAMPLES/'tm1/databricks_demo'
RUN_ROOT = Path(tempfile.gettempdir())/'ssis_migration_app_runs'

st.set_page_config(page_title='Legacy ETL Migration Workbench',page_icon='🔄',layout='wide')
for key,value in {'migration_result':None,'migration_output':None,'ddl_text':None,'last_run_settings':None,'last_attempt_error':None}.items():
    if key not in st.session_state:st.session_state[key]=value
if 'workspace_session' not in st.session_state:
    RUN_ROOT.mkdir(parents=True,exist_ok=True)
    st.session_state.workspace_session=tempfile.mkdtemp(prefix='workspace-',dir=str(RUN_ROOT))
SESSION_ROOT=Path(st.session_state.workspace_session)

with st.sidebar:
    st.title('Migration Workbench')
    st.caption('Offline legacy exports → Databricks')
    platform_labels={'Detect from supplied files':None,'SSIS':'ssis','TM1 / Planning Analytics':'tm1',
                     'Informatica PowerCenter':'informatica','SAP Data Services (BODS)':'bods','Oracle Data Integrator (ODI)':'odi'}
    platform_choice=st.selectbox('Confirm source platform',list(platform_labels),key='platform_choice')
    source_platform=platform_labels[platform_choice]
    st.caption('SSIS generation, bounded TM1 candidates, and PowerCenter/BODS/ODI XML structural analysis are available.')

st.title('Legacy ETL Migration Workbench')
st.write('Configure exported sources, target namespaces and Databricks notebook parameters in one place.')
st.caption('Offline implementation continues · Execution and source equivalence remain unverified until evidence is supplied.')

def save_upload(upload, folder):
    raw=upload.getvalue();name=Path(upload.name.replace('\\','/')).name
    if not name or name in {'.','..'}:raise ValueError('Upload needs a regular filename.')
    directory=SESSION_ROOT/folder/hashlib.sha256(raw).hexdigest();directory.mkdir(parents=True,exist_ok=True)
    path=directory/name
    if not path.exists():path.write_bytes(raw)
    return path


def source_from_folder(folder):
    candidates=bundle_candidates(folder)
    if not candidates:
        st.error('No SSIS package, TM1 source manifest or supported PowerCenter/BODS/ODI XML export was found in this folder.')
        return None
    labels={str(p.relative_to(folder)):p for p in candidates}
    choice=st.selectbox('Source package / manifest',list(labels),key='bundle_manifest')
    return labels[choice]


def file_language(path):
    return {'.py':'python','.sql':'sql','.json':'json','.yml':'yaml','.yaml':'yaml','.md':'markdown'}.get(path.suffix,'text')

def _save_dependencies(uploaded_files):
    """Preserve every uploaded file, including duplicate basenames."""
    files = [(Path(item.name).name or "uploaded_file", item.getvalue()) for item in uploaded_files]
    digest = hashlib.sha256()
    for name, content in files:
        digest.update(name.encode("utf-8"))
        digest.update(hashlib.sha256(content).digest())
    signature = digest.hexdigest()
    if st.session_state.get("dependency_upload_signature") != signature:
        uploads_root = RUN_ROOT / "uploads"
        uploads_root.mkdir(parents=True, exist_ok=True)
        st.session_state.dependency_upload_directory = tempfile.mkdtemp(prefix="dependencies-", dir=str(uploads_root))
        st.session_state.dependency_upload_signature = signature
    directory = Path(st.session_state.dependency_upload_directory)
    counts = {name: sum(other_name == name for other_name, _ in files) for name, _ in files}
    saved = []
    for index, (name, content) in enumerate(files):
        # Duplicate names have no arbitrary winner for mapping resolution.
        parent = directory / f"duplicate-{index}" if counts[name] > 1 else directory
        parent.mkdir(parents=True, exist_ok=True)
        path = parent / name
        if not path.exists():
            path.write_bytes(content)
        saved.append((name, path))
    return directory, saved


tab_upload,tab_run,tab_artifacts,tab_validation=st.tabs(['📦 Upload & Configure','🚀 Run Migration','📂 Artifacts','🧪 Databricks Validation'])
with tab_upload:
    st.subheader('Source exports')
    with st.expander('Real source files to supply',expanded=False):
        source_guide=source_export_guide(source_platform)
        st.markdown(source_guide)
        st.download_button('Download real-source checklist',source_guide,'source_files_required.md',mime='text/markdown')
    st.caption('Bundled sample modes are regression demonstrations. Supply your own exported workflows and dependencies for work on your migration; no new synthetic source data or expected results will be authored.')
    package_mode=st.radio('Package source',['Upload a source package or script','Use WWI sample package',
                                          'Upload a source bundle (.zip)','Use TM1 sample bundle','Use source path'],
                          horizontal=True,key='package_mode')
    source_path=None;source_root=None;source_error=None
    try:
        if package_mode=='Use WWI sample package':
            source_path=SAMPLE_PACKAGE;source_root=source_path.parent
            st.caption('Bundled SSIS regression example. Source gaps remain visible in the review report.')
        elif package_mode=='Use TM1 sample bundle':
            source_path=TM1_SAMPLE/'manifest.json';source_root=TM1_SAMPLE
            st.caption('Synthetic TI, MDX and rule examples with four independently specified expected-result cases.')
            st.download_button('Download TM1 example bundle',artifact_archive(TM1_SAMPLE),'tm1_example_bundle.zip')
        elif package_mode=='Upload a source bundle (.zip)':
            uploaded_bundle=st.file_uploader('Upload source bundle ZIP',type=['zip'],key='bundle_upload')
            st.caption('Keep relative paths intact. Include source manifest, scripts, dimensional/process metadata and target mapping. Choose the primary manifest below.')
            if uploaded_bundle:
                raw=uploaded_bundle.getvalue();signature=hashlib.sha256(raw).hexdigest()
                if st.session_state.get('source_archive_signature')!=signature:
                    folder=SESSION_ROOT/('bundle-'+uuid4().hex)
                    extract_bundle(raw,folder)
                    st.session_state.source_archive_folder=str(folder)
                    st.session_state.source_archive_signature=signature
                source_root=Path(st.session_state.source_archive_folder)
                source_path=source_from_folder(source_root)
        elif package_mode=='Use source path':
            entered=st.text_input('Source export path',placeholder='/Volumes/catalog/schema/volume/source_exports')
            st.caption('A file or folder readable by this app, such as a mounted Volume. A path on your laptop must be uploaded instead.')
            if entered:
                path=Path(entered).expanduser()
                if path.is_symlink():raise ValueError('Select a regular source file or folder, not a symlink.')
                if path.is_dir():source_root=path.resolve();source_path=source_from_folder(source_root)
                elif path.is_file():source_path=path.resolve();source_root=source_path.parent
                else:raise ValueError('Source path is not readable by this app.')
        else:
            uploaded_package=st.file_uploader('Upload exported source package or script',key='package_upload')
            if uploaded_package:source_path=save_upload(uploaded_package,'sources');source_root=source_path.parent
        if source_path and not source_path.is_file():raise ValueError('Selected source file is missing.')
    except (ValueError,OSError,UnicodeError,zipfile.BadZipFile,NotImplementedError) as exc:
        source_error=str(exc);st.error(source_error)

    detected=describe_file(source_path)['format'] if source_path and source_path.is_file() else None
    native=source_platform=='tm1' or detected=='tm1_source_bundle_v1' or package_mode=='Use TM1 sample bundle'
    xml_native=source_platform in {'informatica','bods','odi'} or detected in {'powercenter_xml','bods_xml','odi_xml'}
    with st.sidebar:
        if (native or xml_native) and st.session_state.get('ai_enabled'):st.session_state.ai_enabled=False
        ai_enabled=st.checkbox('Enable AI-assisted generation',value=False,key='ai_enabled',disabled=native or xml_native)
        if native:st.caption('TM1 candidates use deterministic capability checks; unsupported source behavior blocks generation.')
        if xml_native:st.caption('Native XML adapters generate structural IR, dependencies and review contracts. Vendor target translation remains blocked.')
    if native and source_platform not in {None,'tm1'}:
        source_error='The confirmed platform conflicts with the supplied TM1 source. Select TM1 or automatic detection.'
        st.error(source_error)
    if xml_native and source_path and source_platform is not None and describe_file(source_path)['platform'] != source_platform:
        source_error='The confirmed platform conflicts with the supplied export content.';st.error(source_error)

    mapping_path=procedures_path=ddl_path=dependency_base=None
    dependency_files=[];file_roles={};selected_mapping_path=None;mapping_doc=None
    supplied_powercenter_exports=[]
    metadata_paths=[];connection_choices={};connection_bindings=[];effective_connection_doc=None;connection_error=None
    if not native and not xml_native:
        st.header("2. Upload Dependencies (optional)")
        st.caption("Supply schema metadata, procedure definitions, connection mappings, parameters and other dependencies as files. Review detected roles or confirm them below. Every supplied file remains in the inventory.")
        uploaded_deps = st.file_uploader(
            "Upload dependency files",
            accept_multiple_files=True,
            key="deps_upload",
        )

        mapping_path = None
        procedures_path = None
        ddl_path = None
        dependency_files = []
        file_roles = {}
        dependency_base = None

        bundled_dependencies=[]
        if source_path and source_root and package_mode=='Upload a source bundle (.zip)':
            bundled_dependencies=[(str(p.relative_to(source_root)),p) for p in artifact_files(source_root) if p!=source_path]
        if uploaded_deps or bundled_dependencies:
            from ssis_migration.intake import describe_file

            upload_dir, saved_dependencies = _save_dependencies(uploaded_deps) if uploaded_deps else (source_root,[])
            saved_dependencies=[*bundled_dependencies,*saved_dependencies]
            dependency_files = [path for _, path in saved_dependencies]
            role_choices = {
                "Detect automatically": None,
                "Connection mapping": "connection_mapping",
                "Database metadata": "database_metadata",
                "Procedure definitions": "procedures",
                "DDL schema": "ddl",
                "Parameters": "parameters",
                "Configuration": "configuration",
                "Script": "script",
                "Source package": "package",
            }
            selected_paths = {role: [] for role in ("connection_mapping", "procedures", "ddl")}
            duplicate_names = {name for name, _ in saved_dependencies if sum(other == name for other, _ in saved_dependencies) > 1}
            for name in sorted(duplicate_names):
                st.warning(f"Multiple files are named `{name}`. All are retained separately; references to that basename need disambiguation.")

            st.write("**Detected dependencies:**")
            for name, saved in saved_dependencies:
                record = describe_file(saved, source_root=source_root if (name,saved) in bundled_dependencies else upload_dir)
                detected_roles = record.get("detected_roles", [])
                st.write(f"- `{name}` → {', '.join(detected_roles) or 'unknown'}")
                chosen = st.selectbox(
                    f"Confirm role for {name if (name,saved) in bundled_dependencies else saved.relative_to(upload_dir)}",
                    options=list(role_choices),
                    key=f"dependency_role_{record['file_id']}",
                )
                confirmed_role = role_choices[chosen]
                if confirmed_role is not None:
                    file_roles[str(saved)] = confirmed_role
                effective_role = confirmed_role or record.get("role", "unknown")
                if effective_role=='database_metadata':metadata_paths.append(saved)
                if effective_role in selected_paths:
                    selected_paths[effective_role].append(saved)
                elif effective_role in {"ambiguous", "unknown"}:
                    st.warning(f"`{name}` needs a role decision and remains in the inventory.")

            for role, paths in selected_paths.items():
                if len(paths) > 1:
                    st.warning(f"Multiple files have role `{role}`. None is selected arbitrarily; resolve the intake findings before generation.")
            mapping_path = selected_paths["connection_mapping"][0] if len(selected_paths["connection_mapping"]) == 1 else None
            procedures_path = selected_paths["procedures"][0] if len(selected_paths["procedures"]) == 1 else None
            ddl_path = selected_paths["ddl"][0] if len(selected_paths["ddl"]) == 1 else None
            if mapping_path is not None:
                dependency_base = mapping_path.parent
            if ddl_path is not None:
                st.session_state.ddl_text = ddl_path.read_text(encoding="utf-8", errors="replace")
        if source_path:
            default_base=dependency_base or (source_root if package_mode=='Use source path' else ROOT)
            dependency_base=Path(st.text_input('Metadata reference base path',value=str(default_base),key='metadata_base_'+str(default_base)))
        if mapping_path:
            st.subheader('Connection metadata bindings')
            st.caption('Select a supplied metadata file for each connection when the declared path does not match your uploads. Renamed files, including names with (1), are never matched automatically.')
            try:
                connection_document=read_json(mapping_path.read_bytes())
                entries=connection_document.get('mappings') if isinstance(connection_document,dict) else None
                if not isinstance(entries,list):raise ValueError('Connection mapping must contain a mappings list.')
                mapping_signature=hashlib.sha256(mapping_path.read_bytes()).hexdigest()[:16]
                for index,entry in enumerate(entries):
                    if not isinstance(entry,dict):raise ValueError('Each connection mapping entry must be an object.')
                    st.caption(f"Connection {index+1}: {entry.get('connection_reference','Missing name')} · Role: {entry.get('role','Unspecified')} · Database: {entry.get('database_name','Unspecified')} · Declared file: {entry.get('metadata_file','Missing')}")
                    options=['Keep declared reference',*[str(p) for p in metadata_paths]]
                    chosen=st.selectbox(f'Metadata file for connection {index+1}',options,
                        format_func=lambda value:value if value=='Keep declared reference' else str(Path(value).name)+' · '+str(Path(value).parent),
                        key='connection_metadata_'+mapping_signature+'_'+str(index))
                    if chosen!='Keep declared reference':connection_choices[index]=Path(chosen)
                effective_connection_doc,connection_bindings,issues=bind_connection_metadata(
                    connection_document,connection_choices,allowed_files=metadata_paths,base_path=dependency_base)
                if issues:
                    connection_error='Resolve the missing connection metadata bindings before generation.'
                    for issue in issues:st.error(issue)
            except (ValueError,OSError,UnicodeError) as exc:
                connection_error=str(exc);st.error(connection_error)

    elif native:
        st.subheader('TM1 source bundle and target mapping')
        if source_path:
            try:
                source_document,consumed=validate_bundle_scope(source_path,source_root)
                st.write(f"**{source_document['name']}** · {len(source_document['units'])} source units · {len(consumed)} referenced files")
                with st.expander('Supplied source files'):
                    st.dataframe(input_inventory(artifact_files(source_root)),hide_index=True)
                paths=find_target_mappings(source_root)
                unreferenced=set(artifact_files(source_root))-consumed-set(paths)
                if unreferenced:
                    st.warning('These supplied files are not referenced by the selected source manifest: '+', '.join(str(p.relative_to(source_root)) for p in sorted(unreferenced)))
                choices={'Upload mapping JSON separately':None,**{str(p.relative_to(source_root)):p for p in paths}}
                chosen=st.selectbox('Target mapping',list(choices),index=1 if len(paths)==1 else 0)
                selected_mapping_path=choices[chosen]
                if selected_mapping_path is None:
                    uploaded_target=st.file_uploader('Upload TM1 target mapping',type=['json'],key='tm1_target_mapping')
                    if uploaded_target:selected_mapping_path=save_upload(uploaded_target,'target_mappings')
                if selected_mapping_path:
                    mapping_doc=read_json(selected_mapping_path.read_bytes());validate_document(mapping_doc,'tm1_databricks_mapping')
                    expected_hash=hashlib.sha256(source_path.read_bytes()).hexdigest()
                    if mapping_doc['source_bundle_sha256']!=expected_hash:raise ValueError('Target mapping SHA-256 does not match this source manifest.')
                else:st.info('Supply the target mapping with source bindings, semantic declarations and expected-result fixtures to generate notebooks.')
            except (ValueError,OSError,UnicodeError) as exc:
                source_error=str(exc);st.error(source_error)
        else:st.info('Upload a complete TM1 bundle ZIP, select a source path, or use the TM1 sample bundle.')
    else:
        st.subheader('Native XML structural analysis')
        st.info('This phase inventories the export, builds typed IR and declared dependency links, and downloads review contracts. It does not generate vendor migration notebooks. Unknown constructs and missing definitions remain blockers.')
        st.caption('XML supports UTF-8 and explicitly declared Windows-1252. BODS ATL, other XML encodings, ODI knowledge-module execution and source runtime semantics remain unsupported.')
        powercenter=source_platform=='informatica' or detected=='powercenter_xml'
        if powercenter:
            st.write('**PowerCenter dependency exports**')
            uploaded_exports=st.file_uploader('Upload PowerCenter dependency XML exports',type=['xml'],
                accept_multiple_files=True,key='powercenter_dependency_uploads')
            if uploaded_exports:
                _,saved_exports=_save_dependencies(uploaded_exports)
                supplied_powercenter_exports=[p for _,p in saved_exports]
            options=list(supplied_powercenter_exports)
            if source_root and package_mode in {'Upload a source bundle (.zip)','Use source path'}:
                options.extend(p for p in bundle_candidates(source_root) if p!=source_path and describe_file(p)['format']=='powercenter_xml')
            if package_mode=='Use source path':
                dependency_paths=st.text_area('Other PowerCenter export paths (one per line)',key='powercenter_dependency_paths')
                for value in dependency_paths.splitlines():
                    if not value.strip():continue
                    path=Path(value.strip()).expanduser().absolute()
                    if not path.is_file() or any(p.is_symlink() for p in (path,*path.parents)):
                        source_error='PowerCenter dependency path must identify a readable regular file: '+str(path)
                        st.error(source_error)
                    else:options.append(path);supplied_powercenter_exports.append(path)
            options=list(dict.fromkeys(p for p in options if p!=source_path))
            labels={str(p):p.name if sum(other.name==p.name for other in options)==1 else str(p) for p in options}
            selected=st.multiselect('PowerCenter dependency exports to bind',[str(p) for p in options],
                format_func=lambda value,labels=labels:labels[value],key='powercenter_dependencies')
            dependency_files=[Path(p) for p in selected]
            st.caption('Select each dependency to include in this review. Session mappings require matching repository, folder UUID and export version; missing or conflicting definitions remain blocked.')
        if source_path:st.dataframe(input_inventory([source_path,*dependency_files]),hide_index=True)
        if source_path and source_root:
            selected_paths={p.resolve() for p in [source_path,*dependency_files]}
            extras=list(dict.fromkeys(p for p in [*artifact_files(source_root),*supplied_powercenter_exports] if p.resolve() not in selected_paths))
            if extras:
                st.warning('These other supplied files are not bound to this review: '+', '.join(str(p.relative_to(source_root)) if p.is_relative_to(source_root) else p.name for p in extras)+'. Dependencies on unselected files remain unresolved.')

    st.divider()
    st.subheader('Databricks target')
    left,right=st.columns(2)
    with left:
        catalog=st.text_input('Target catalog',value='dev_catalog',key='target_catalog')
        target_layer=st.selectbox('Target layer',['bronze','silver','gold'],index=1,key='target_layer')
    with right:
        notebook_root=st.text_input('Destination workspace folder',placeholder='/Workspace/Shared/etl_migration',key='notebook_root')
        cluster_id=st.text_input('Existing cluster ID (optional)',placeholder='Your Databricks cluster ID',key='cluster_id')
    bronze,silver,gold=st.columns(3)
    schemas={}
    for layer,column in [('bronze',bronze),('silver',silver),('gold',gold)]:
        with column:schemas[layer]=st.text_input(layer.title()+' schema',value=layer,key=layer+'_schema')
    st.caption('Layer selection sets the target namespace. Source transformations determine the business logic; selecting a layer does not invent an ETL stage.')
    data_destination=st.text_input('Target data path (proposed)',placeholder='/Volumes/catalog/schema/volume/migrated_data',key='data_destination')
    st.caption('Optional data storage location, such as /Volumes/catalog/schema/volume/data. Leave blank for preview. A /Workspace/... notebook folder is not a data path.')
    artifact_destination=st.text_input('Artifact destination path',value=str(SESSION_ROOT/'artifacts'),key='artifact_destination')
    st.caption('App-local storage: keep the default /tmp/... folder and download the ZIP. /Workspace/... belongs only in Destination workspace folder; the app does not write there directly.')
    st.button('Use app temporary artifact storage',on_click=lambda:st.session_state.update(artifact_destination=str(SESSION_ROOT/'artifacts')))
    input_mode=st.selectbox('Notebook input mode',['fixtures','tables'],key='input_mode',disabled=not native)
    if not native:st.caption('SSIS retains schema references from the supplied source/metadata; the selected layer supplies notebook and job defaults.')

    output_tables={};model_edits={};unit_edits={};effective_doc=None;configuration_error=None
    if native and mapping_doc:
        st.subheader('Runtime bindings')
        signature=hashlib.sha256(selected_mapping_path.read_bytes()).hexdigest()[:16]
        for model in mapping_doc['models']:
            model_edits[model['model_id']]=st.text_input('Cell snapshot table · '+model['cube'],value=model['input_table'] or '',
                placeholder=f"{catalog}.{schemas['bronze']}.{model['model_id']}_cells",key='cells_'+signature+'_'+model['model_id'])
        for unit in mapping_doc['units']:
            with st.expander('Unit · '+unit['unit_id'],expanded=True):
                row_table=unit['datasource_table'] or ''
                if unit['order_column']:
                    row_table=st.text_input('Ordered datasource table · '+unit['unit_id'],value=row_table,
                        placeholder=f"{catalog}.{schemas['bronze']}.ordered_rows",key='rows_'+signature+'_'+unit['unit_id'])
                    st.caption('Explicit source record ordinal: '+unit['order_column'])
                parameters=st.text_area('Parameters JSON · '+unit['unit_id'],value=json.dumps(unit['parameters'],indent=2),key='params_'+signature+'_'+unit['unit_id'])
                try:
                    parsed_parameters=read_json(parameters.encode('utf-8'))
                    if not isinstance(parsed_parameters,dict):raise ValueError('Parameters must be a JSON object.')
                    unit_edits[unit['unit_id']]={'datasource_table':row_table,'parameters':parsed_parameters}
                except ValueError as exc:configuration_error=str(exc);st.error(configuration_error)
                default=re.sub('[^A-Za-z0-9_]','_',unit['unit_id'])
                if not default or not default[0].isalpha():default='unit_'+default
                table_name=st.text_input('Proposed output table · '+unit['unit_id'],value=default,key='output_'+signature+'_'+unit['unit_id'])
                output_tables[unit['unit_id']]=f"{catalog}.{schemas[target_layer]}.{table_name}"
        with st.expander('Semantic profile and fixture evidence'):
            st.json(mapping_doc['semantic_profile'])
            st.dataframe([{'case':c['case_id'],'unit':c['unit_id'],'evidence':c['provenance']['kind']} for c in mapping_doc['fixture_cases']],hide_index=True)
        try:
            if not configuration_error:effective_doc=effective_mapping(mapping_doc,model_edits,unit_edits,input_mode=input_mode)
        except ValueError as exc:configuration_error=str(exc);st.error(configuration_error)

    workspace_config={'config_version':'1.0','catalog':catalog,'schemas':schemas,'target_layer':target_layer,
        'source_path':str(source_path) if source_path else 'No source selected','artifact_path':artifact_destination,
        'data_destination_path':data_destination or None,'notebook_root':notebook_root or None,
        'existing_cluster_id':cluster_id or None,'input_mode':input_mode,'output_tables':output_tables}
    try:
        validate_workspace_config(workspace_config)
        if source_root and Path(artifact_destination).expanduser().resolve().is_relative_to(source_root.resolve()):
            raise ValueError('Choose an artifact destination outside the supplied source folder.')
    except ValueError as exc:configuration_error=str(exc);st.error(configuration_error)
    can_run=bool(source_path and not source_error and not connection_error and not configuration_error and (not native or effective_doc))
    st.write('**Target namespace:** `'+catalog+'.'+schemas[target_layer]+'`')
    if source_path:st.caption('Source: '+str(source_path))
    with st.expander('Review run configuration'):
        st.json(workspace_config)
    st.download_button('Download workspace configuration',json.dumps(workspace_config,indent=2),'workspace_config.json',mime='application/json')

with tab_run:
    st.subheader('Generate migration artifacts')
    st.write('Source intake → native IR → migration contract → capability checks → generation → local validation')
    if xml_native:st.caption('For native XML exports this run stops at structural analysis and review contracts; target generation remains blocked.')
    if not can_run:st.info('Complete the source and target configuration in Upload & Configure before generating artifacts.')
    if st.button('🚀 Run Migration',type='primary',disabled=not can_run):
        try:
            st.session_state.last_attempt_error=None
            output_dir=prepare_run_directory(artifact_destination)
            configuration_dir=output_dir/'configuration';configuration_dir.mkdir(parents=True,exist_ok=False)
            runtime_mapping=None
            runtime_connection_mapping=mapping_path
            runtime_dependencies=list(dependency_files);runtime_roles=dict(file_roles)
            if connection_bindings:
                # Source intake excludes generated output directories. Keep
                # the effective input outside output, and archive exact bytes.
                binding_dir=SESSION_ROOT/'bindings'/uuid4().hex
                binding_dir.mkdir(parents=True,exist_ok=False)
                runtime_connection_mapping=binding_dir/'effective_connection_mapping.json'
                runtime_connection_mapping.write_text(json.dumps(effective_connection_doc,indent=2,allow_nan=False)+'\n')
                (configuration_dir/'effective_connection_mapping.json').write_bytes(runtime_connection_mapping.read_bytes())
                (configuration_dir/'original_connection_mapping.json').write_bytes(mapping_path.read_bytes())
                (configuration_dir/'connection_metadata_bindings.json').write_text(json.dumps({
                    'original_mapping':str(mapping_path),'original_sha256':hashlib.sha256(mapping_path.read_bytes()).hexdigest(),
                    'effective_sha256':hashlib.sha256(runtime_connection_mapping.read_bytes()).hexdigest(),
                    'bindings':connection_bindings},indent=2)+'\n')
                # The original is archived as superseded evidence. Intake must
                # analyze only the explicitly selected effective mapping.
                runtime_dependencies=[p for p in dependency_files if p.resolve()!=mapping_path.resolve()]
                runtime_roles.pop(str(mapping_path),None)
            if native:
                runtime_mapping=configuration_dir/'effective_target_mapping.json'
                runtime_mapping.write_text(json.dumps(effective_doc,indent=2,allow_nan=False)+'\n')
                (configuration_dir/'original_target_mapping.json').write_bytes(selected_mapping_path.read_bytes())
            inventory_paths=artifact_files(source_root) if package_mode in {'Upload a source bundle (.zip)','Use TM1 sample bundle','Use source path'} and source_root else [source_path]
            inventory_paths=[*inventory_paths,*dependency_files,*supplied_powercenter_exports]
            if selected_mapping_path:inventory_paths.append(selected_mapping_path)
            (configuration_dir/'uploaded_input_inventory.json').write_text(json.dumps(input_inventory(inventory_paths),indent=2)+'\n')
            (configuration_dir/'workspace_config.json').write_text(json.dumps(workspace_config,indent=2)+'\n')
            with st.status('Generating and checking artifacts…',expanded=True) as progress:
                st.write('Checking supplied evidence and target configuration.')
                report=run_frontend_migration(source_path,output_dir,platform=source_platform,
                    target_mapping=runtime_mapping,workspace_config=workspace_config,catalog=catalog,ai=ai_enabled,
                    mapping=runtime_connection_mapping,procedures=procedures_path,ddl=ddl_path,dependencies=runtime_dependencies,
                    file_roles=runtime_roles,base_path=dependency_base or ROOT)
                st.session_state.migration_result=report
                st.session_state.migration_output=str(output_dir)
                st.session_state.last_run_settings=workspace_config
                progress.update(label='Artifact generation finished',state='complete',expanded=False)
        except Exception as exc:
            st.session_state.last_attempt_error=str(exc)
            with st.expander('Developer error details'):st.exception(exc)
    if st.session_state.last_attempt_error:
        st.error('Latest generation attempt failed: '+st.session_state.last_attempt_error)
    report=st.session_state.migration_result
    output_value=st.session_state.migration_output
    if report:
        summary=report_summary(report)
        if summary['status']=='BLOCKED':st.warning('Artifacts generated with blocking findings. Review the validation results before running them.')
        elif not st.session_state.last_attempt_error:st.success('Artifacts generated. Databricks execution and source equivalence still require review.')
        st.subheader('Last run results')
        cols=st.columns(4)
        for column,(label,value) in zip(cols,[('Analyzed',summary['analyzed']),('Generated',summary['generated']),('Failed',summary['failed']),('Accepted',str(summary['accepted']))]):column.metric(label,value)
        st.write('**Status:** `'+summary['status']+'` · **Execution:** `'+summary['execution']+'`')
        st.caption('Results and downloads below belong to the last completed run; editing the form requires a new run to apply settings.')
        st.json(st.session_state.last_run_settings,expanded=False)
        if report.get('behavior_analysis'):
            with st.expander('PowerCenter behavior requirements'):
                st.write(str(report['behavior_analysis']['requirement_count'])+' source behavior requirements · semantic verification: NOT_VERIFIED')
                for issue in report['behavior_analysis']['unresolved_items']:st.write('• '+issue)
                behavior_path=Path(report['artifacts']['source_behavior_analysis'])
                if behavior_path.is_file():
                    st.download_button('Download PowerCenter behavior analysis',behavior_path.read_bytes(),'powercenter_behavior_analysis.json',mime='application/json')
        intake=report.get('source_intake',{})
        intake_path=report.get('intake_manifest')
        if intake_path and Path(intake_path).is_file():intake=read_json(Path(intake_path).read_bytes())
        if intake:
            with st.expander('Source intake findings'):
                for diagnostic in intake.get('diagnostics',[]):
                    st.write(f"`{diagnostic.get('severity','INFO')}` `{diagnostic.get('code','INTAKE')}`: {diagnostic.get('message','')}")
        for package in report.get('packages',[]):
            with st.expander('Package · '+package.get('name','Unknown')):
                st.json({'status':package.get('lifecycle_status'),'stages':package.get('stages',{}),'error':package.get('error')})
        if output_value and Path(output_value).is_dir():
            st.download_button('📥 Download artifacts (.zip)',artifact_archive(output_value),'migration_artifacts.zip')
            for name in ('Migration.ipynb','Validation.ipynb','DATABRICKS_REVIEW.md'):
                path=report.get('artifacts',{}).get(name)
                if path and Path(path).is_file():st.download_button('Download '+name,Path(path).read_bytes(),name,key='direct_'+name)

with tab_artifacts:
    st.subheader('Artifact browser')
    output_value=st.session_state.migration_output
    if not output_value:st.info('Generate artifacts to inspect notebooks, contracts, reports and job templates.')
    else:
        output=Path(output_value);files=artifact_files(output)
        if files:
            lookup={str(p.relative_to(output)):p for p in files}
            selected=st.selectbox('Artifact',list(lookup));path=lookup[selected]
            raw=path.read_bytes()
            st.caption(f'{selected} · {len(raw):,} bytes')
            if path.suffix.lower()!='.zip':
                content=raw.decode('utf-8',errors='replace')
                if path.suffix=='.ipynb':
                    notebook=json.loads(content)
                    for index,cell in enumerate(notebook.get('cells',[])):
                        if cell['cell_type']=='code':
                            with st.expander('Code cell '+str(index+1)):st.code(''.join(cell['source']),language='python')
                        else:st.text(''.join(cell['source']))
                else:st.code(content[:50000],language=file_language(path))
            st.download_button('Download selected artifact',raw,path.name,key='artifact_download')
        else:st.info('No artifact files found.')

with tab_validation:
    st.subheader('Offline evidence comparison')
    st.caption('Compare supplied observations without connecting to a source server or Databricks. Synthetic captures do not verify source equivalence.')
    source_observation=st.file_uploader('Source observation JSON',type=['json'],key='source_observation')
    target_observation=st.file_uploader('Target observation JSON',type=['json'],key='target_observation')
    if source_observation and target_observation:
        from ssis_migration.ui import reconciliation_bundle
        try:
            comparison,comparison_zip=reconciliation_bundle(source_observation.getvalue(),target_observation.getvalue())
            st.write('**Observed case:** `'+comparison['status']+'` · **Evidence:** `'+comparison['evidence_scope']+'`')
            st.json(comparison)
            st.download_button('Download comparison and original captures',comparison_zip,'observation_comparison.zip')
        except (ValueError,OSError,UnicodeError) as exc:st.error('Observation comparison failed: '+str(exc))
    st.subheader('Optional Databricks validation handoff')
    st.write('1. Download the generated notebooks and import them into your destination workspace folder.')
    st.write('2. Attach Python/PySpark compute. For TM1, run Validation.ipynb first; every supplied fixture must pass.')
    st.write('3. Run the migration notebook with fixture inputs, inspect results, then review explicit table bindings for your test data.')
    st.write('4. Save notebook output and runtime/input snapshot details. Compare against independently expected source results before acceptance.')
    st.caption('Generated job/DAB templates describe the reviewed workspace and cluster settings. Publication and workload execution remain manual.')
    report=st.session_state.migration_result
    if report:
        if report.get('fixture_results'):
            st.subheader('Local target fixture checks');st.dataframe(report['fixture_results'],hide_index=True)
        if report.get('validation'):st.json(report['validation'])
        if report.get('unsupported'):
            st.error('Source behavior remains blocked.');st.json(report['unsupported'])
        if report.get('coverage'):st.json(report['coverage'])
        for action in report.get('required_human_actions',[]):st.write('• '+action)
    if st.session_state.ddl_text:
        with st.expander('Uploaded DDL preview'):st.code(st.session_state.ddl_text,language='sql')

st.divider()
st.caption('Databricks target · Source evidence retained · Generation does not establish migration acceptance')
