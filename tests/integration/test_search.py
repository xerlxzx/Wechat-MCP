import pytest

from tests.integration.test_importer import import_case, factory
from tests.support.schemas import TABLE
from tests.support.encrypted import Engine
from wechat_bridge.core.errors import BridgeError


def repo_case(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.search.repository import QueryRepository
    cfg = import_case(tmp_path)
    assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'complete'
    return cfg, QueryRepository(cfg)


def test_short_chinese_and_structured_filters_use_index_only(tmp_path, monkeypatch):
    cfg, repo = repo_case(tmp_path)
    (cfg.state_dir / 'keys.json').unlink()
    def forbidden(*args, **kwargs):
        raise AssertionError('query must not import/decrypt source')
    monkeypatch.setattr('wechat_bridge.archive.importer.refresh_archive', forbidden)
    result = repo.search_messages('发票')
    assert len(result['items']) == 1
    hit = result['items'][0]
    assert hit['excerpt'] == '发票 invoice'
    assert hit['sender_id'] is not None
    assert repo.search_messages('invoice', sender_id=hit['sender_id'], type='text',
                                start='2023-11-14T22:13:20Z', end='2023-11-14T22:13:20Z')['items'][0]['message_id'] == hit['message_id']
    assert repo.search_messages('invoice', start='2023-11-14T22:13:20.5Z')['items'] == []
    assert repo.search_messages('absent')['items'] == []


def test_unicode_and_sql_looking_queries_are_literal(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    cfg, _ = repo_case(tmp_path)
    with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
        writer.run(f"UPDATE {TABLE} SET message_content='ＡＢＣ 发票 INVＯICE café 🐈！' WHERE local_id=1")
    refresh_archive(cfg, adapter_factory=factory)
    from wechat_bridge.search.repository import QueryRepository
    repo = QueryRepository(cfg)
    for query in ('abc 发票 invoice', 'CAFÉ', '🐈!'):
        assert len(repo.search_messages(query)['items']) == 1
    assert repo.search_messages("%' OR 1=1--")['items'] == []


def test_duplicate_names_group_members_and_source_coverage_stay_explicit(tmp_path):
    cfg, repo = repo_case(tmp_path)
    contacts = repo.list_contacts('Same name')['items']
    assert len(contacts) == 2
    assert contacts[0]['contact_id'] != contacts[1]['contact_id']
    assert all('source_username' not in c for c in contacts)
    group = next(c for c in repo.list_conversations()['items'] if c['kind'] == 'group')
    result = repo.get_group(group['conversation_id'])
    assert result['group']['membership_status'] == 'recorded'
    assert len(result['items']) == 2
    direct = next(c for c in repo.list_conversations()['items'] if c['kind'] == 'direct')
    timeline = repo.get_messages(direct['conversation_id'])
    assert timeline['items'] == []
    assert timeline['local_history_status'] == 'no_local_message_table'
    assert timeline['coverage']['complete_phone_history'] is False


def test_context_includes_invalid_time_messages_with_bounded_neighbors(tmp_path):
    _, repo = repo_case(tmp_path)
    chat = next(c for c in repo.list_conversations()['items'] if c['kind'] == 'group')
    messages = repo.get_messages(chat['conversation_id'])['items']
    assert len(messages) == 3
    invalid = next(m for m in messages if m['utc_timestamp'] is None)
    result = repo.get_context(invalid['message_id'], before=50, after=50)
    assert result['message']['message_id'] == invalid['message_id']
    assert len(result['items']) == 3
    with pytest.raises(BridgeError, match='invalid_context_limit'):
        repo.get_context(invalid['message_id'], before=51, after=50)


@pytest.mark.parametrize('options', [{'limit': 0}, {'limit': 201}, {'limit': True}, {'start': '2026-01-01'},
                                    {'start': '2026-02-01Z', 'end': '2026-01-01Z'}, {'sender_id': '../escape'}])
def test_invalid_query_bounds_and_filters_are_rejected(tmp_path, options):
    _, repo = repo_case(tmp_path)
    with pytest.raises(BridgeError):
        repo.search_messages('发票', **options)


@pytest.mark.parametrize('query', ['', '   ', 'a' * 257])
def test_empty_or_oversized_search_is_rejected(tmp_path, query):
    _, repo = repo_case(tmp_path)
    with pytest.raises(BridgeError, match='invalid_query'):
        repo.search_messages(query)


def test_unknown_valid_ids_and_unprepared_index_are_distinct(tmp_path):
    from wechat_bridge.search.repository import QueryRepository
    from wechat_bridge.archive.store import Archive
    _, repo = repo_case(tmp_path)
    with pytest.raises(BridgeError, match='message_not_found'):
        repo.get_context('message_' + '0' * 64)
    cfg = import_case(tmp_path / 'empty'); Archive(cfg)
    with pytest.raises(BridgeError, match='search_not_ready'):
        QueryRepository(cfg)


def test_failed_refresh_keeps_search_readable_with_staleness(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    cfg, repo = repo_case(tmp_path)
    def fail(_):
        raise BridgeError('source_unreadable')
    refresh_archive(cfg, adapter_factory=fail)
    result = repo.search_messages('发票')
    assert len(result['items']) == 1
    assert result['freshness']['stale']


def test_attachment_metadata_filters_and_private_refs_are_separate(tmp_path):
    from tests.support.proof import proof_case
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.search.repository import QueryRepository
    case = proof_case(tmp_path); cfg = case.config
    refresh_archive(cfg, adapter_factory=factory); repo = QueryRepository(cfg)
    files = repo.list_attachments(kind='file', availability='local_unverified')
    assert len(files['items']) == 1
    item = files['items'][0]
    assert item['original_filename'] == 'test.bin'
    assert item['verification_status'] == 'not_checked'
    assert 'relative_path' not in item and 'file_identity' not in item
    ref, generation = repo.attachment_ref(item['attachment_id'], generation=files['generation'])
    assert ref.relative_path == 'msg/file/2023-11/test.bin'
    assert isinstance(ref.file_identity, tuple)
    assert generation == files['generation']
    assert repo.list_attachments(kind='image')['items'][0]['kind'] == 'image'
    assert repo.list_attachments(availability='not_local')['items'] == []


@pytest.mark.parametrize('prefix, original, query', [
    ('heading' + '\n' * 300, '发票', '发票'),
    ('ﬃ' * 300, 'café', 'CAFÉ'),
    ('ß' * 300, 'ＡＢＣ', 'abc'),
    ('á' * 300, 'Invoice', 'invoice'),
    ('각' * 300, 'ｶﾞ', 'ガ'),
    ('ͅ ' * 300, 'Straße', 'STRASSE'),
], ids=['collapsed-whitespace', 'compatibility-expansion', 'casefold-expansion',
        'combining-composition', 'hangul-and-katakana', 'leading-combining'])
def test_normalized_search_excerpt_contains_original_match(tmp_path, prefix, original, query):
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.search.repository import QueryRepository
    cfg, _ = repo_case(tmp_path)
    text = prefix + ' ' + original + ' tail'
    with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
        writer.run(f"UPDATE {TABLE} SET message_content='{text}' WHERE local_id=1")
    assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'complete'
    hit = QueryRepository(cfg).search_messages(query)['items'][0]
    assert original in hit['excerpt']
    assert hit['excerpt'] in text
    assert len(hit['excerpt']) <= 240
    assert hit['excerpt_is_partial']
