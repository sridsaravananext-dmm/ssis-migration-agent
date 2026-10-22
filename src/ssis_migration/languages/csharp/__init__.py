"""Offline C# triage; no compilation, execution or equivalence claims."""
from .assessment import assess_csharp

from .parser import parse_csharp

__all__ = ['assess_csharp', 'parse_csharp']
