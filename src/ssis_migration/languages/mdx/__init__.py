"""MDX is a multidimensional language; it never falls through to SQL."""
from .analysis import analyze_mdx, mdx_analysis_id, validate_mdx_analysis

__all__ = ['analyze_mdx', 'mdx_analysis_id', 'validate_mdx_analysis']
