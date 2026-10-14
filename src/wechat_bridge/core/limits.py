"""Public record/query API limits, reported verbatim by the MCP status tool.

These describe the client-facing query contract only. Unrelated bounds that
happen to share a value (SQLite row caps, archive/import batch sizes, schema
inspection caps) are deliberately NOT defined here, so changing one never moves
another.
"""
DEFAULT_RECORDS = 50
MAX_RECORDS = 200
MAX_CONTEXT_NEIGHBORS = 100
MAX_QUERY_CHARACTERS = 256
