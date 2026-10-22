"""Dialect-aware, offline structural SQL analysis."""
from .analysis import SQLDocument, analyze_sql, parse_sql, parameter_name, range_candidates, normalize_dialect

__all__ = ['SQLDocument', 'analyze_sql', 'parse_sql', 'parameter_name', 'range_candidates', 'normalize_dialect']
