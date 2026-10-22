"""Explicit Databricks workspace settings shared by UI and artifact generators."""
from __future__ import annotations

import copy
import json
import re

from ssis_migration.schemas import validate_document


def validate_workspace_config(config):
    validate_document(config, 'databricks_workspace')
    if len({v.casefold() for v in config['schemas'].values()}) != 3:
        raise ValueError('Bronze, silver and gold schemas must have distinct names.')
    for name in ('source_path','artifact_path','data_destination_path','notebook_root','existing_cluster_id'):
        value = config[name]
        if value is not None and (value != value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError('Paths and cluster IDs cannot contain surrounding whitespace or control characters.')
    root = config['notebook_root']
    if '://' in config['artifact_path'] or config['artifact_path'].startswith('dbfs:'):
        raise ValueError('Artifact destination must be an app-readable filesystem path; use a mounted /Volumes/... path for durable storage.')
    if root is not None and (not root.startswith('/Workspace/') or '..' in root.split('/')):
        raise ValueError('Notebook destination must be an absolute /Workspace/... folder without parent traversal.')
    if config['existing_cluster_id'] is not None and not re.fullmatch(r'[A-Za-z0-9_-]+',config['existing_cluster_id']):
        raise ValueError('Existing cluster ID contains invalid characters.')
    namespace = config['catalog']+'.'+config['schemas'][config['target_layer']]+'.'
    if any(not table.startswith(namespace) for table in config['output_tables'].values()):
        raise ValueError('Proposed output tables must use the selected target catalog and layer schema.')
    return copy.deepcopy(config)


def target_schema(config):
    return config['schemas'][config['target_layer']]


def widget_code(config):
    """Target settings are literal data; neither paths nor identifiers become code."""
    config = validate_workspace_config(config)
    defaults = {'catalog':config['catalog'], 'schema':target_schema(config),
                **{layer+'_schema':name for layer,name in config['schemas'].items()},
                'target_layer':config['target_layer'],'source_export_path':config['source_path'],
                'destination_path':config['data_destination_path'] or ''}
    code = 'WORKSPACE_CONFIG = json.loads('+repr(json.dumps(config,ensure_ascii=False,allow_nan=False))+')\n'
    for name,value in defaults.items():
        code += f'dbutils.widgets.text({name!r}, {value!r})\n'
    code += "_catalog = dbutils.widgets.get('catalog')\n_schema = dbutils.widgets.get('schema')\n"
    code += "print('Target namespace:', _catalog + '.' + _schema, '| Mode: review before publication')\n"
    return code


def configure_job(job, config, *, variables=False):
    """Use explicit notebook/cluster settings without deploying resources."""
    config = validate_workspace_config(config)
    result = copy.deepcopy(job)
    for parameter in result.get('parameters',[]):
        if parameter['name'] == 'catalog':parameter['default'] = config['catalog']
        if parameter['name'] == 'schema':parameter['default'] = target_schema(config)
    for task in result['tasks']:
        notebook = task['notebook_task']
        root = '${var.notebook_root}' if variables else config['notebook_root'] or '${var.notebook_root}'
        name = notebook['notebook_path'].rsplit('/',1)[-1].removesuffix('.ipynb')
        notebook['notebook_path'] = root.rstrip('/')+'/'+name
        params = notebook.setdefault('base_parameters',{})
        params.update(catalog=config['catalog'],schema=target_schema(config),target_layer=config['target_layer'],
                      destination_path=config['data_destination_path'] or '')
        task.pop('job_cluster_key',None)
        task['existing_cluster_id'] = '${var.existing_cluster_id}' if variables else config['existing_cluster_id'] or '${var.existing_cluster_id}'
    result.pop('job_clusters',None)
    return result
