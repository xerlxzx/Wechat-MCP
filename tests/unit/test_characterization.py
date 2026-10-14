"""Characterization tests pinning behavior that the behavior-preserving refactor touches.

These assert what the code does today so later refactor commits cannot drift it.
They are added in Commit 0 and pass against the unchanged code.
"""
import json
import sqlite3

import pytest

from wechat_bridge.core.errors import BridgeError


# --- Compact-JSON serializers are byte-identical (guards the shared-helper commit) ---

JSON_CORPUS = [
    {'b': 1, 'a': 2},                       # key order preserved (no sort_keys)
    {'text': '发票 🐈 café'},                 # non-ASCII kept literally (ensure_ascii=False)
    {'nested': {'x': [1, 2, {'y': None}]}},
    [1, True, False, None],
    {'big': 9007199254740993},              # int > 2**53
    {'s': 'a"b\\c\n\t'},                     # escaping
    {},
    'scalar 发票',
    123,
]


@pytest.mark.parametrize('value', JSON_CORPUS)
def test_compact_json_serializers_are_byte_identical(value):
    from wechat_bridge.archive.store import encode
    from wechat_bridge.mcp.server import _json
    expected = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    assert encode(value) == expected
    assert _json(value) == expected


def test_compact_json_serializers_reject_nan():
    from wechat_bridge.archive.store import encode
    from wechat_bridge.mcp.server import _json
    for serialize in (encode, _json):
        with pytest.raises(ValueError):
            serialize(float('nan'))


def test_cursor_binding_is_order_insensitive_for_filters():
    # cursor.binding uses sort_keys=True by design; this pins that so a future
    # "unify the serializers" refactor cannot fold cursor.binding into the
    # insertion-order-preserving compact-JSON helper.
    from wechat_bridge.search.cursor import binding
    assert binding('messages', {'a': 1, 'b': 2}, 50) == binding('messages', {'b': 2, 'a': 1}, 50)


# --- Attachment availability: SQL and Python rules agree on the reachable domain ---

def _sql_availability(record):
    from wechat_bridge.search.repository import AVAILABILITY_SQL
    db = sqlite3.connect(':memory:')
    try:
        return db.execute(f'SELECT {AVAILABILITY_SQL} FROM (SELECT json(?) AS data) r',
                          (json.dumps(record),)).fetchone()[0]
    finally:
        db.close()


@pytest.mark.parametrize('provenance', ['conflicting', 'resource_hardlink', 'resource_layout', 'explicit_resource', 'unsupported'])
@pytest.mark.parametrize('relative_path', [None, 'msg/file/2023-11/test.bin'])
@pytest.mark.parametrize('declared_local', [True, False, None])
@pytest.mark.parametrize('file_identity', [None, [1, 2, 3, 4, 5]])
def test_availability_sql_and_python_agree_on_reachable_records(provenance, relative_path, declared_local, file_identity):
    from wechat_bridge.search.repository import attachment_availability
    record = {'mapping_provenance': provenance, 'relative_path': relative_path,
              'declared_local': declared_local, 'file_identity': file_identity}
    assert _sql_availability(record) == attachment_availability(record)


def test_availability_empty_path_divergence_is_pinned_not_merged():
    # mapping.py only ever emits relative_path=None or a well-formed path, never ''.
    # On the unreachable '' input the SQL ('relative_path IS NULL') and Python
    # (falsy) rules diverge. The refactor documents this invariant instead of
    # merging the rules; this test pins the current divergence so neither side is
    # "cleaned up" without the other.
    from wechat_bridge.search.repository import attachment_availability
    record = {'mapping_provenance': 'resource_hardlink', 'relative_path': '',
              'declared_local': True, 'file_identity': [1, 2, 3, 4, 5]}
    assert _sql_availability(record) == 'local_unverified'        # SQL: '' IS NOT NULL
    assert attachment_availability(record) == 'not_local'         # Python: '' is falsy


# --- Strict integer limits keep their distinct error codes (guards require_int_range) ---

@pytest.mark.parametrize('bad', [True, False, 1.0, '1', None, 0, 201])
def test_repository_valid_limit_rejects_nonint_and_out_of_range(bad):
    from wechat_bridge.search.repository import valid_limit
    with pytest.raises(BridgeError, match='invalid_limit'):
        valid_limit(bad)


@pytest.mark.parametrize('ok', [1, 50, 200])
def test_repository_valid_limit_accepts_boundaries(ok):
    from wechat_bridge.search.repository import valid_limit
    valid_limit(ok)


@pytest.mark.parametrize('bad', [True, False, 0, 52428801, 1.0, '1'])
def test_decode_image_rejects_bad_max_bytes_with_content_limit_code(bad):
    import io
    from wechat_bridge.attachments.images import decode_image
    with pytest.raises(BridgeError, match='invalid_content_limit'):
        decode_image(io.BytesIO(b'x'), material=None, max_bytes=bad)
