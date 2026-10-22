from __future__ import annotations

from ssis_migration.languages.sql import parse_sql


def detect_required_features(source_sql: str, *, source_dialect: str) -> set[str]:
    """Preserve the existing feature API using complete, dialect-aware analysis.

    INCREMENTAL_CUTOFF is a compatibility label for a parameterized range
    candidate. It does not establish a watermark update or incremental load.
    Unsupported syntax raises explicitly rather than returning an empty set.
    """
    if not source_sql or not source_sql.strip():
        return set()
    document = parse_sql(source_sql, dialect=source_dialect)
    if not document.supported:
        raise ValueError('; '.join(d['message'] for d in document.report['diagnostics']))
    features = {f for s in document.report['statements'] for f in s['features']}
    selected = features & {'UNION_ALL', 'LEFT_JOIN', 'COALESCE', 'TEMPORAL_TABLE_AS_OF'}
    if 'PARAMETERIZED_RANGE' in features:
        selected.add('INCREMENTAL_CUTOFF')
    return selected
