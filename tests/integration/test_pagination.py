import pytest

from tests.integration.test_search import repo_case
from tests.integration.test_importer import factory
from tests.support.schemas import TABLE
from tests.support.encrypted import Engine
from wechat_bridge.core.errors import BridgeError


def test_cursor_binds_filters_limit_and_authenticates_payload(tmp_path):
    _, repo = repo_case(tmp_path)
    page = repo.list_contacts(limit=1)
    token = page['next_cursor']; assert token
    next_page = repo.list_contacts(limit=1, cursor=token)
    assert next_page['items'][0]['contact_id'] != page['items'][0]['contact_id']
    for options in ({'limit': 2}, {'limit': 1, 'query': 'Same'}, {'limit': 1, 'cursor': token[:-2] + 'XX'}):
        with pytest.raises(BridgeError):
            repo.list_contacts(cursor=token, **options) if 'cursor' not in options else repo.list_contacts(**options)


def test_staged_and_new_generations_cannot_move_existing_rank_cursor(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    cfg, _ = repo_case(tmp_path)
    with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
        writer.run(f"UPDATE {TABLE} SET local_type=1,message_content='发票 invoice',create_time=1700000000 WHERE local_id=2; INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(3,126,1,1,1700000000,'发票 invoice',0)")
    refresh_archive(cfg, adapter_factory=factory)
    from wechat_bridge.search.repository import QueryRepository
    from wechat_bridge.archive.store import Archive
    repo = QueryRepository(cfg)
    expected = [m['message_id'] for m in repo.search_messages('发票')['items']]
    assert len(expected) == 3
    first = repo.search_messages('发票', limit=1)
    archive = Archive(cfg); stage = archive.begin_generation(archive.status()['account_id'])
    contacts = [{'contact_id': 'contact_' + f'{i:064x}', 'display_name': '发票 invoice'} for i in range(200)]
    archive.write_records(stage, 'contacts', contacts)
    second = repo.search_messages('发票', limit=1, cursor=first['next_cursor'])
    archive.fail(stage, 'synthetic interruption')
    refresh_archive(cfg, adapter_factory=factory)
    third = repo.search_messages('发票', limit=1, cursor=second['next_cursor'])
    assert [first['items'][0]['message_id'], second['items'][0]['message_id'], third['items'][0]['message_id']] == expected
    assert third['generation'] == first['generation']
    assert third['next_cursor'] is None


def test_removed_generation_cursor_requires_explicit_retry(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    cfg, repo = repo_case(tmp_path)
    cursor = repo.list_contacts(limit=1)['next_cursor']
    refresh_archive(cfg, adapter_factory=factory)
    refresh_archive(cfg, adapter_factory=factory)
    with pytest.raises(BridgeError, match='cursor_expired'):
        repo.list_contacts(limit=1, cursor=cursor)


def test_search_cursor_handles_invalid_source_time_without_integer_overflow(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    cfg, _ = repo_case(tmp_path)
    with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
        writer.run(f"UPDATE {TABLE} SET local_type=1,message_content='发票 invoice',create_time=NULL WHERE local_id IN (1,2)")
    refresh_archive(cfg, adapter_factory=factory)
    from wechat_bridge.search.repository import QueryRepository
    repo = QueryRepository(cfg)
    first = repo.search_messages('发票', limit=1)
    second = repo.search_messages('发票', limit=1, cursor=first['next_cursor'])
    assert first['items'][0]['message_id'] != second['items'][0]['message_id']
    assert second['next_cursor'] is None
