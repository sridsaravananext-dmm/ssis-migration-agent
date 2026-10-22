"""Explicit source adapter selection; unsupported sources never use SSIS."""

from __future__ import annotations

from .base import SourceAdapter, UnsupportedSourceError


SOURCE_PLATFORMS = frozenset({"ssis", "tm1", "informatica", "bods", "odi"})
EXCLUDED_PLATFORMS = frozenset({"datastage", "ab_initio"})


def get_adapter(platform: str) -> SourceAdapter:
    """Return an implemented adapter, with no implicit platform fallback."""
    normalized = str(platform).strip().lower().replace("-", "_").replace(" ", "_")
    normalized = {
        "powercenter": "informatica",
        "informatica_powercenter": "informatica",
        "ibm_tm1": "tm1",
        "planning_analytics": "tm1",
        "sap_bods": "bods",
        "oracle_data_integrator": "odi",
        "ibm_infosphere_datastage": "datastage",
        "data_stage": "datastage",
        "abinitio": "ab_initio",
    }.get(normalized, normalized)
    if normalized in EXCLUDED_PLATFORMS:
        raise UnsupportedSourceError(
            f"Source platform {normalized!r} is explicitly excluded from this framework."
        )
    if normalized == "ssis":
        from .ssis import SSISAdapter

        return SSISAdapter()
    if normalized == "tm1":
        from .tm1.adapter import TM1Adapter

        return TM1Adapter()
    if normalized in SOURCE_PLATFORMS:
        raise UnsupportedSourceError(
            f"Source platform {normalized!r} is on the roadmap, but its adapter is not implemented."
        )
    raise UnsupportedSourceError(
        f"Source platform {platform!r} is unknown; no adapter was selected."
    )
