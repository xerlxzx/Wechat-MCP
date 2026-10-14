"""The single compact JSON encoding used for archive storage and MCP text payloads.

Insertion order is preserved (no sort_keys) and non-ASCII is kept literally;
NaN/Infinity are rejected. Cursor signing (search.cursor) deliberately uses
different arguments and must not be routed through this helper.
"""
import json


def compact_json(value):
    """Encode value as compact JSON; raises ValueError on NaN or Infinity."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
