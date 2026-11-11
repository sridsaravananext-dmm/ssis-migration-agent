"""Validated run configuration shared by CLI, UI, and backend callers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AssessmentConfig:
    catalog: str = "main"
    layer: str = "unspecified"
    target_schema: str | None = None
    description: str = ""
    default_schema: str = "dbo"
    strict_mode: bool = False
    dry_run: bool = False
    respect_human_edits: bool = True
    planner_mode: str = "graph"
    object_mappings: dict = field(default_factory=dict)
    dependency_scopes: dict = field(default_factory=dict)
    destination_write_mode: str = 'source'
    audit_mode: str = 'source'
    audit_catalog: str | None = None
    audit_schema: str | None = None
    audit_capture_table_stats: bool = True
    audit_value_bindings: dict = field(default_factory=dict)
    source_audit_rules: dict = field(default_factory=dict)
    audit_contract: dict = field(default_factory=dict)

    def __post_init__(self):
        for key in ('catalog', 'layer', 'description', 'default_schema'):
            if not isinstance(getattr(self, key), str):
                raise ValueError(f'{key} must be text')
        for key in ('strict_mode', 'dry_run', 'respect_human_edits'):
            if not isinstance(getattr(self, key), bool):
                raise ValueError(f'{key} must be a boolean')
        if self.target_schema is not None and not isinstance(self.target_schema, str):
            raise ValueError('Target schema must be text')
        if not self.catalog.strip() or not self.default_schema.strip():
            raise ValueError("Catalog and default SQL schema must be non-empty")
        if not self.layer.strip():
            raise ValueError("Layer must be non-empty; use 'unspecified' if unknown")
        if self.target_schema is not None and not self.target_schema.strip():
            raise ValueError("Target schema must be non-empty when supplied")

        if not isinstance(self.planner_mode, str) or self.planner_mode not in {'graph', 'legacy'}:
            raise ValueError('Planner mode must be graph or legacy')
        if not isinstance(self.destination_write_mode,str) or self.destination_write_mode not in {'source','review','append_existing'}:
            raise ValueError('Destination write mode must be source, review, or append_existing')
        if self.planner_mode != 'graph' and self.destination_write_mode == 'append_existing':
            raise ValueError('Native destination writes require the source graph planner')
        if not isinstance(self.audit_mode,str) or self.audit_mode not in {'source','target'}:raise ValueError('Audit mode must be source or target')
        if self.audit_mode=='target' and self.planner_mode!='graph':raise ValueError('Target auditing requires the source graph planner')
        if not isinstance(self.audit_capture_table_stats,bool):raise ValueError('Audit table statistics setting must be a boolean')
        from ssis_migration.mapping import location_identifier
        for key in ('audit_catalog','audit_schema'):
            value=getattr(self,key)
            if value is not None:location_identifier(value,key)
        if not isinstance(self.audit_value_bindings,dict) or any(not isinstance(k,str) or not isinstance(v,str) or v not in {'runtime_input','notebook_key'} for k,v in self.audit_value_bindings.items()):
            raise ValueError('Audit value bindings map scoped variable keys to runtime_input or notebook_key')
        from ssis_migration.audit_contract import storage_contract,source_rules
        storage_contract(self.audit_contract);source_rules(self.source_audit_rules,self.default_schema)
        if self.planner_mode == 'graph':
            from ssis_migration.mapping import location_identifier
            location_identifier(self.catalog, 'Target catalog')
            if self.target_schema: location_identifier(self.target_schema, 'Target schema')
        if not isinstance(self.object_mappings, dict) or not isinstance(self.dependency_scopes, dict):
            raise ValueError('Mappings and dependency scopes must be objects')
        from ssis_migration.mapping import target_table
        from ssis_migration.sql_text import normalize_name
        keys = set()
        for key, value in self.object_mappings.items():
            if not isinstance(key, str) or not key.strip(): raise ValueError('Mapping source must be non-empty text')
            prefix, separator, name = key.rpartition('|')
            canonical = (prefix if separator else '', normalize_name(name if separator else key, self.default_schema))
            if canonical in keys: raise ValueError(f'Duplicate normalized mapping: {key}')
            keys.add(canonical)
            if isinstance(value, dict) and set(value) - {'target','layer'}:
                raise ValueError('Object mappings accept only target and layer; write strategy is a separate decision')
            if separator and (not prefix.strip() or not name.strip()):
                raise ValueError('Scoped mappings require connection_reference|source_object')
            target_table(value)
            if isinstance(value, dict) and 'layer' in value and (not isinstance(value['layer'], str) or not value['layer'].strip()):
                raise ValueError('Mapped output layer must be non-empty text')
        for name, connection in self.dependency_scopes.items():
            if not isinstance(name, str) or not isinstance(connection, str) or not connection.strip():
                raise ValueError('Dependency scopes map file names to non-empty connection references')

    def to_dict(self):
        return asdict(self)


def dependency_paths(single=None, multiple=()):
    """Retain the old single-file API while accepting repeatable files."""
    values = ([single] if single else []) + list(multiple or ())
    result = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Dependency file not found: {path}")
        if path not in result:
            result.append(path)
    return result
