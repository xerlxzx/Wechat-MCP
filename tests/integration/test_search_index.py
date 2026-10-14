import json

import pytest

from tests.integration.test_importer import import_case, factory
from tests.support.encrypted import Engine
from tests.support.schemas import TABLE


def matching(archive, generation, query):
    from wechat_bridge.search.tokenize import normalize, terms
    normalized = normalize(query)
    tokens = terms(normalized)
    wanted = [token for token in tokens if token.startswith('b')] or list(tokens)
    match = ' AND '.join('"' + t + '"' for t in [f'z{generation}', 'kmessages', *wanted])
    with archive.connection() as db:
        return [r['record_id'] for r in db.execute('SELECT record_id FROM record_search WHERE tokens MATCH ? AND instr(normalized,?)>0', (match, normalized))]


@pytest.mark.parametrize('text,query', [
    ('收到发票了', '发票'), ('ＡＢＣ 发票 INVＯICE', 'abc 发票 invoice'),
    ('猫🐈！', '🐈!'), ('cafe\u0301', 'CAFÉ'), ("a%' OR 1=1--", "%' OR 1=1--"),
])
def test_index_finds_normalized_literals_and_preserves_original(tmp_path, text, query):
    from wechat_bridge.archive.store import Archive
    from wechat_bridge.search.index import ensure_index
    cfg = import_case(tmp_path)
    archive = Archive(cfg); ensure_index(archive)
    generation = archive.begin_generation('test')
    record = {'message_id': 'message_test', 'conversation_id': 'conversation_test', 'sender_id': None,
              'type': 'text', 'utc_timestamp': '2026-01-01T00:00:00+00:00', 'source_timestamp': '1767225600', 'text': text}
    archive.write_records(generation, 'messages', [record]); archive.publish(generation, {})
    assert matching(archive, generation, query) == ['message_test']
    with archive.connection() as db:
        assert json.loads(db.execute('SELECT data FROM messages').fetchone()[0])['text'] == text
    assert matching(archive, generation, 'definitely missing') == []


def test_existing_archive_backfills_and_retention_removes_fts_rows(tmp_path):
    from wechat_bridge.archive.store import Archive
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.search.index import ensure_index
    cfg = import_case(tmp_path)
    # Construct a pre-index generation directly, using real adapter records.
    from dataclasses import asdict
    archive = Archive(cfg); adapter = factory(cfg)
    first = archive.begin_generation(adapter.inventory.account_id)
    chat = next(c for c in adapter.conversations() if c.kind == 'group')
    archive.write_records(first, 'messages', [asdict(m) for b in adapter.message_batches(chat.conversation_id) for m in b])
    archive.publish(first, {})
    ensure_index(archive)
    assert len(matching(archive, first, '发票')) == 1
    for _ in range(3):
        assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'complete'
    with archive.connection() as db:
        generations = {r[0] for r in db.execute('SELECT generation FROM generations WHERE state="complete"')}
        assert {r[0] for r in db.execute('SELECT DISTINCT generation FROM record_search')} == generations
        assert db.execute('SELECT count(*) FROM record_search WHERE kind="messages"').fetchone()[0] == 6


def test_failed_stage_leaves_no_searchable_partial_rows(tmp_path):
    from wechat_bridge.archive.store import Archive
    from wechat_bridge.search.index import ensure_index
    cfg = import_case(tmp_path); archive = Archive(cfg); ensure_index(archive)
    stage = archive.begin_generation('test')
    archive.write_records(stage, 'contacts', [{'contact_id': 'contact_test', 'display_name': 'SECRET partial'}])
    archive.fail(stage, 'interrupted_refresh')
    with archive.connection() as db:
        assert db.execute('SELECT count(*) FROM record_search').fetchone()[0] == 0


def test_index_migration_cannot_race_a_building_generation(tmp_path):
    from wechat_bridge.archive.store import Archive
    from wechat_bridge.search.index import ensure_index
    from wechat_bridge.core.errors import BridgeError
    cfg = import_case(tmp_path); archive = Archive(cfg); archive.begin_generation('test')
    with pytest.raises(BridgeError, match='archive_refresh_in_progress'):
        ensure_index(archive)
