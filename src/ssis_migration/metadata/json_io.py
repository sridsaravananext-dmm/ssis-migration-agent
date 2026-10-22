"""Strict offline JSON parsing shared by adapters and common validation."""
import json
import math


def read_json(raw: bytes):
    """Reject duplicate keys, nesting overflow and non-finite numbers."""
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result: raise ValueError('Duplicate metadata/report JSON key: '+key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError('Non-finite metadata/report JSON value: '+value)

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result): raise ValueError('Non-finite metadata/report JSON number: '+value)
        return result

    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
                          parse_constant=nonfinite, parse_float=finite_float)
    except RecursionError as exc:
        raise ValueError('Metadata/report JSON nesting exceeds the supported structure.') from exc
