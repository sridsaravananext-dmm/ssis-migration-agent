"""File-backed frontend services; Streamlit is kept out of archive and routing logic."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import tempfile
import zipfile

from ssis_migration.metadata.json_io import read_json
from ssis_migration.schemas import validate_document

MAX_FILES = 256
MAX_ARCHIVE_BYTES = 64 * 1048576


def prepare_run_directory(artifact_path):
    from ssis_migration.workspace import is_workspace_path
    if is_workspace_path(artifact_path):
        raise ValueError('Use the default /tmp/... artifact folder; /Workspace/... belongs in Destination workspace folder.')
    root=Path(artifact_path).expanduser().resolve()
    try:
        root.mkdir(parents=True,exist_ok=True)
        return Path(tempfile.mkdtemp(prefix='run-',dir=str(root)))
    except OSError as exc:
        raise ValueError('Artifact destination is not writable by this app: '+str(root)+'. Use the default /tmp/... folder and download the ZIP, or choose an app-writable mounted folder.') from exc


def bind_connection_metadata(document, choices, *, allowed_files, base_path):
    """Rebind only explicitly selected mapping entries; retain all original fields."""
    if not isinstance(document,dict) or not isinstance(document.get('mappings'),list):
        raise ValueError('Connection mapping must contain a mappings list.')
    if any(type(i) is not int or i<0 or i>=len(document['mappings']) for i in choices):
        raise ValueError('Metadata selection identifies an unknown connection mapping entry.')
    allowed={Path(p).resolve() for p in allowed_files}
    result=copy.deepcopy(document);bindings=[];issues=[]
    for index,entry in enumerate(result['mappings']):
        if (not isinstance(entry,dict) or not isinstance(entry.get('connection_reference'),str)
            or not entry['connection_reference'].strip() or not isinstance(entry.get('metadata_file'),str)
            or not entry['metadata_file'].strip()):
            raise ValueError('Each mapping requires connection_reference and metadata_file strings.')
        original=entry['metadata_file']
        if index in choices:
            selected=Path(choices[index]).resolve()
            if selected not in allowed or not selected.is_file():
                raise ValueError('Select a supplied database metadata file for '+entry['connection_reference'])
            entry['metadata_file']=str(selected)
            bindings.append({'locator':f'json:/mappings/{index}/metadata_file',
                             'connection_reference':entry['connection_reference'],
                             'original_reference':original,'selected_file':str(selected),
                             'sha256':hashlib.sha256(selected.read_bytes()).hexdigest(),
                             'resolution':'USER_SELECTED'})
        else:
            referenced=Path(original).expanduser()
            if not referenced.is_absolute():referenced=Path(base_path)/referenced
            if not referenced.is_file():
                issues.append('Metadata file '+original+' is missing for '+entry['connection_reference']+'. Select its uploaded metadata file below or correct the mapping/base path.')
    return result,bindings,issues


def extract_bundle(raw, destination):
    """Extract a bounded user ZIP without traversal, links or name collisions."""
    if len(raw) > MAX_ARCHIVE_BYTES:raise ValueError('Bundle ZIP exceeds 64 MiB.')
    destination = Path(destination).resolve()
    if destination.exists():raise ValueError('Use a new directory for each uploaded bundle.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist(); seen = set(); total = 0
        if len(entries) > MAX_FILES:raise ValueError('Bundle exceeds 256 archive entries.')
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (not entry.filename or path.is_absolute() or '\\' in entry.filename or ':' in entry.filename
                or any(p in {'..','.'} for p in entry.filename.rstrip('/').split('/'))
                or any(ord(c)<32 for c in entry.filename)):
                raise ValueError('Unsafe archive path: '+entry.filename)
            folded = str(path).casefold()
            if folded in seen:raise ValueError('Duplicate/colliding archive path: '+entry.filename)
            seen.add(folded)
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0,stat.S_IFREG,stat.S_IFDIR}):
                raise ValueError('Archive links and special files are unsupported.')
            if entry.flag_bits & 1:raise ValueError('Encrypted archives are unsupported.')
            total += entry.file_size
            if total > MAX_ARCHIVE_BYTES:raise ValueError('Uncompressed bundle exceeds 64 MiB.')
            if any(p.casefold() in {'.git','.venv','__pycache__'} for p in path.parts):
                raise ValueError('Upload source exports only; remove repository/environment directories.')
        destination.mkdir(parents=True)
        for entry in entries:
            target = destination.joinpath(*PurePosixPath(entry.filename).parts)
            if entry.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                # CRC and real byte count are checked before writing.
                data = archive.read(entry)
                if len(data) != entry.file_size:raise ValueError('Archive file size changed.')
                with target.open('xb') as stream:stream.write(data)
    return destination


def bundle_candidates(root):
    root = Path(root); candidates = []
    for path in sorted(root.rglob('*')):
        if not path.is_file():continue
        if path.suffix.lower() == '.dtsx':candidates.append(path)
        elif path.suffix.lower() == '.xml' and path.stat().st_size <= 16*1048576:
            from ssis_migration.intake import describe_file
            if describe_file(path)['format'] in {'powercenter_xml','bods_xml','odi_xml'}:candidates.append(path)
        elif path.suffix.lower() == '.json' and path.stat().st_size <= 1048576:
            try:document = read_json(path.read_bytes())
            except (ValueError,UnicodeError):continue
            if isinstance(document,dict) and document.get('export_format') == 'tm1_source_bundle_v1':candidates.append(path)
    return candidates


def contained_reference(parent, reference, root):
    if not isinstance(reference,str) or '\\' in reference or ':' in reference:
        raise ValueError('Bundle references must be portable relative paths.')
    path = Path(reference)
    if path.is_absolute():raise ValueError('Bundle references cannot use absolute paths.')
    candidate = parent.parent/path
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()):raise ValueError('Bundle reference escapes the supplied source folder: '+reference)
    if not resolved.is_file() or any(p.is_symlink() for p in (candidate,*candidate.parents)):
        raise ValueError('Bundle reference must identify a supplied regular file: '+reference)
    return resolved


def validate_bundle_scope(manifest, root):
    """A web upload must never turn a manifest into a read of host files."""
    manifest = Path(manifest); root = Path(root)
    if not manifest.resolve().is_relative_to(root.resolve()):raise ValueError('Manifest is outside the selected source folder.')
    document = read_json(manifest.read_bytes());validate_document(document,'tm1_source_bundle')
    consumed = {manifest.resolve()}
    for entry in document['metadata_files'] + document['units']:
        path = contained_reference(manifest,entry['path'],root);consumed.add(path)
        if entry.get('language') == 'tm1_turbointegrator':
            process = read_json(path.read_bytes());validate_document(process,'tm1_ti_manifest')
            for section in process['sections'].values():
                consumed.add(contained_reference(path,section['path'],root))
    return document,consumed


def find_target_mappings(root):
    paths = []
    for path in sorted(Path(root).rglob('*.json')):
        if path.stat().st_size > 8*1048576:continue
        try:document = read_json(path.read_bytes())
        except (ValueError,UnicodeError):continue
        if isinstance(document,dict) and document.get('export_format') == 'tm1_databricks_mapping_v1':paths.append(path)
    return paths


def effective_mapping(document, model_edits, unit_edits, *, input_mode):
    """Only explicit target/runtime edits; source roles, hashes and fixtures stay intact."""
    validate_document(document,'tm1_databricks_mapping')
    if set(model_edits)-{m['model_id'] for m in document['models']} or set(unit_edits)-{u['unit_id'] for u in document['units']}:
        raise ValueError('Runtime edits reference an unknown model or unit.')
    result = copy.deepcopy(document)
    for model in result['models']:
        if model['model_id'] in model_edits:model['input_table'] = model_edits[model['model_id']] or None
        if input_mode == 'tables' and not model['input_table']:raise ValueError('Assign a cell snapshot table for model '+model['model_id'])
    for unit in result['units']:
        edit = unit_edits.get(unit['unit_id'],{})
        if edit:
            unit['datasource_table'] = edit['datasource_table'] or None
            unit['parameters'] = copy.deepcopy(edit['parameters'])
        if input_mode == 'tables' and unit['order_column'] and not unit['datasource_table']:
            raise ValueError('Assign an ordered datasource table for unit '+unit['unit_id'])
    validate_document(result,'tm1_databricks_mapping')
    return result


def input_inventory(paths):
    return [{'path':str(p),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest(),
             'size_bytes':Path(p).stat().st_size} for p in sorted(set(map(Path,paths)))]


def run_frontend_migration(source, output, *, platform=None, target_mapping=None, workspace_config=None,
                           catalog=None, ai=False, mapping=None, procedures=None, ddl=None,
                           dependencies=(), file_roles=None, base_path=None):
    from ssis_migration.intake import describe_file
    from ssis_migration.migrate import run_migration
    native = platform == 'tm1' or (platform is None and describe_file(source)['format']=='tm1_source_bundle_v1')
    if native:
        if ai:raise ValueError('AI generation is unavailable for the native TM1 candidate path.')
        return run_migration(source=str(source),output=str(output),source_platform=platform,target_mapping=str(target_mapping) if target_mapping else None,
                             workspace_config=workspace_config,execute=False)
    confirmed = str(platform or '').lower().replace(' ','_').replace('-','_')
    xml_native = confirmed in {'informatica','powercenter','informatica_powercenter','bods','sap_bods','odi','oracle_data_integrator'} or (
        platform is None and describe_file(source)['format'] in {'powercenter_xml','bods_xml','odi_xml'})
    if xml_native:
        if ai or target_mapping or mapping or procedures or ddl or dependencies or file_roles:
            raise ValueError('Native XML structural analysis requires a self-contained export; SSIS dependency bindings and AI are not consumed.')
        return run_migration(source=str(source),output=str(output),source_platform=platform,
                             workspace_config=workspace_config,execute=False)
    return run_migration(source=str(source),output=str(output),catalog=catalog,ai=ai,ai_provider='databricks',
                         connection_mapping=str(mapping) if mapping else None,procedures_path=str(procedures) if procedures else None,
                         ddl_path=str(ddl) if ddl else None,dependency_files=[str(p) for p in dependencies],
                         file_roles=file_roles or {},source_platform=platform,base_path=str(base_path) if base_path else None,
                         workspace_config=workspace_config,execute=False)


def report_summary(report):
    lifecycle = report.get('lifecycle_status') or report.get('status','UNKNOWN')
    accepted = report.get('migration_accepted',report.get('acceptance',{}).get('accepted',False))
    return {'status':lifecycle,'analyzed':report.get('analyzed_package_count',0),
            'generated':report.get('generated_artifact_count',report.get('migrated_package_count',0)),
            'failed':report.get('failed_package_count',0),'accepted':accepted,
            'execution':report.get('execution_status',report.get('execution',{}).get('status','NOT_RUN'))}


def reconciliation_bundle(source_bytes, target_bytes):
    """Produce a portable comparison and original capture ZIP without execution."""
    from ssis_migration.validator.reconciliation import reconcile
    if len(source_bytes)>8*1048576 or len(target_bytes)>8*1048576:
        raise ValueError('Each observation upload must fit the 8 MiB evidence limit.')
    source=read_json(source_bytes);target=read_json(target_bytes)
    report=reconcile(source,target)
    captures={'source_observation.json':source_bytes,'target_observation.json':target_bytes}
    report['observation_files']={side:{'path':name,'sha256':hashlib.sha256(captures[name]).hexdigest(),'size_bytes':len(captures[name])}
                                 for side,name in [('source','source_observation.json'),('target','target_observation.json')]}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in captures.items():archive.writestr(name,raw)
        archive.writestr('reconciliation.json',json.dumps(report,indent=2,allow_nan=False))
    return report,buffer.getvalue()


def artifact_files(root):
    root=Path(root).resolve()
    return sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root))


def artifact_archive(root):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in artifact_files(root):archive.write(path,str(path.relative_to(Path(root).resolve())))
    return data.getvalue()
