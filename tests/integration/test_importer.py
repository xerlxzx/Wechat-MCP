import json
from pathlib import Path

import pytest

from tests.support.schemas import account_case, CHAT, TABLE, SCHEMAS
from tests.support.samples import QUOTE
from tests.support.encrypted import Engine, KEY
from wechat_bridge.adapters.wechat_4_1 import open_adapter
from wechat_bridge.core.errors import BridgeError


def factory(cfg):
    return open_adapter(cfg, build='270102')


def import_case(tmp_path):
    return account_case(tmp_path, changes={
        'message/message_0.db': f"INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(1,123,1,1,1700000000,'发票 invoice',0),(2,124,99,4,NULL,'unknown',0)",
        'message/message_1.db': f"INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(1,125,{(57<<32)|49},1,1700000001,x'{QUOTE.hex()}',0)",
    })


def active_rows(cfg, kind='messages'):
    from wechat_bridge.archive.store import Archive
    with Archive(cfg).connection() as db:
        return [json.loads(r['data']) for r in db.execute(f'SELECT data FROM {kind} WHERE generation=(SELECT active_generation FROM archive_state) ORDER BY record_id')]


def test_reconciliation_replays_edits_deletions_and_delayed_replies(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    cfg = import_case(tmp_path)
    first = refresh_archive(cfg, adapter_factory=factory)
    assert first['status'] == 'complete'
    assert first['counts']['messages'] == 3
    assert first['coverage']['missing_message_tables'] == 1
    rows = active_rows(cfg)
    assert any(m['sender_id'] is None and m['parse_status'] == 'unsupported' for m in rows)
    assert next(m for m in rows if m['reply'])['reply']['resolution_status'] == 'unresolved'
    ids = {m['message_id'] for m in rows}
    assert refresh_archive(cfg, adapter_factory=factory)['counts']['messages'] == 3
    assert {m['message_id'] for m in active_rows(cfg)} == ids
    with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
        writer.run(f"UPDATE {TABLE} SET message_content='Edited' WHERE local_id=1; DELETE FROM {TABLE} WHERE local_id=2; INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(3,9007199254740993,1,1,1700000000,'Later target',0)")
    assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'complete'
    rows = active_rows(cfg)
    assert len(rows) == 3
    assert any(m['text'] == 'Edited' for m in rows)
    target = next(m for m in rows if m['text'] == 'Later target')
    reply = next(m for m in rows if m['reply'])['reply']
    assert reply['target_message_id'] == target['message_id']
    assert len(active_rows(cfg, 'membership')) == 2
    assert len(active_rows(cfg, 'replies')) == 1


@pytest.mark.parametrize('code', ['source_unreadable', 'unsupported_schema', 'setup_required'])
def test_failed_source_read_never_publishes_deletion(tmp_path, code):
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.archive.store import Archive
    cfg = import_case(tmp_path)
    first = refresh_archive(cfg, adapter_factory=factory)
    def fail(_):
        raise BridgeError(code)
    failed = refresh_archive(cfg, adapter_factory=fail)
    assert failed['status'] == 'failed'
    assert failed['error'] == code
    assert Archive(cfg).status()['active_generation'] == first['active_generation']
    assert len(active_rows(cfg)) == 3


def test_source_mutation_during_import_rejects_mixed_generation(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.archive.store import Archive
    cfg = import_case(tmp_path)
    first = refresh_archive(cfg, adapter_factory=factory)
    def changing(cfg):
        adapter = factory(cfg)
        original = adapter.message_batches
        def batches(*args, **kwargs):
            yield from original(*args, **kwargs)
            with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
                writer.run(f"UPDATE {TABLE} SET message_content='Mutated' WHERE local_id=1")
        adapter.message_batches = batches
        return adapter
    failed = refresh_archive(cfg, adapter_factory=changing)
    assert failed['error'] == 'source_changed'
    assert Archive(cfg).status()['active_generation'] == first['active_generation']
    assert not any(m['text'] == 'Mutated' for m in active_rows(cfg))


def test_interrupt_after_committed_batch_preserves_old_generation(tmp_path, monkeypatch):
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.archive.store import Archive
    cfg = import_case(tmp_path); first = refresh_archive(cfg, adapter_factory=factory)
    original = Archive.write_records
    def interrupt(self, generation, kind, records):
        original(self, generation, kind, records)
        if kind == 'messages':
            raise KeyboardInterrupt
    monkeypatch.setattr(Archive, 'write_records', interrupt)
    with pytest.raises(KeyboardInterrupt):
        refresh_archive(cfg, adapter_factory=factory)
    status = Archive(cfg).status()
    assert status['active_generation'] == first['active_generation']
    assert status['last_refresh_error'] == 'operation_cancelled'
    assert len(active_rows(cfg)) == 3
    monkeypatch.setattr(Archive, 'write_records', original)
    assert refresh_archive(cfg, adapter_factory=factory)['counts']['messages'] == 3


def test_publication_failure_preserves_previous_complete_generation(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.archive.store import Archive
    cfg = import_case(tmp_path); first = refresh_archive(cfg, adapter_factory=factory)
    with Archive(cfg).connection(write=True) as db:
        db.execute("CREATE TRIGGER refuse_publish BEFORE UPDATE ON archive_state BEGIN SELECT RAISE(ABORT,'refused'); END")
    failed = refresh_archive(cfg, adapter_factory=factory)
    assert failed['status'] == 'failed'
    assert Archive(cfg).status()['active_generation'] == first['active_generation']
    assert len(active_rows(cfg)) == 3


def test_replaced_shard_requires_verified_key_and_changes_incarnation_ids(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.source.discovery import discover
    from wechat_bridge.source.keys import KeyEntry, KeyManifest, verify_keys, publish_verified_manifest
    cfg = import_case(tmp_path); refresh_archive(cfg, adapter_factory=factory)
    old = next(m['message_id'] for m in active_rows(cfg) if m['text'] == '发票 invoice')
    path = cfg.account_root / 'db_storage/message/message_0.db'
    path.unlink()
    for suffix in ('-wal', '-shm'):
        Path(str(path) + suffix).unlink(missing_ok=True)
    with Engine(path) as writer:
        writer.run(SCHEMAS['message/message_0.db'])
        writer.run(f"INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(1,123,1,1,1700000000,'Rebuilt',0)")
    assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'failed'
    assert len(active_rows(cfg)) == 3
    inv = discover(cfg)
    keys = KeyManifest(tuple(KeyEntry(d.account_id, d.relative_path, d.database_id, d.salt_hex, KEY) for d in inv.databases))
    publish_verified_manifest(keys, verify_keys(inv, keys, cfg), cfg)
    assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'complete'
    assert next(m['message_id'] for m in active_rows(cfg) if m['text'] == 'Rebuilt') != old


def test_rewritten_shard_with_same_salt_keeps_message_ids(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    cfg = import_case(tmp_path); refresh_archive(cfg, adapter_factory=factory)
    before = {m['message_id'] for m in active_rows(cfg)}
    path = cfg.account_root / 'db_storage/message/message_0.db'
    with Engine(path) as writer:
        writer.run('PRAGMA wal_checkpoint(TRUNCATE)')
    copy = path.with_suffix('.new'); copy.write_bytes(path.read_bytes()); copy.replace(path)
    assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'complete'
    assert {m['message_id'] for m in active_rows(cfg)} == before


def test_fingerprint_detects_committed_wal_and_rejects_symlink(tmp_path):
    from wechat_bridge.archive.reconcile import source_fingerprint
    cfg = import_case(tmp_path); before = source_fingerprint(cfg)
    with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
        writer.run(f"UPDATE {TABLE} SET message_content='WAL edit' WHERE local_id=1")
        assert source_fingerprint(cfg) != before
    extra = cfg.account_root / 'db_storage/message/escape.db'
    extra.symlink_to(cfg.account_root / 'db_storage/message/message_0.db')
    with pytest.raises(BridgeError, match='source_symlink'):
        source_fingerprint(cfg)


def test_simultaneous_imports_cannot_abandon_each_others_staging(tmp_path):
    import fcntl
    from wechat_bridge.archive.importer import refresh_archive
    cfg = import_case(tmp_path)
    with (cfg.state_dir / '.archive-import.lock').open('wb') as lock:
        import os
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(BridgeError, match='archive_refresh_in_progress'):
            refresh_archive(cfg, adapter_factory=factory)


def test_unsupported_membership_is_preserved_without_blocking_archive(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    cfg = import_case(tmp_path)
    with Engine(cfg.account_root / 'db_storage/session/session.db') as writer:
        writer.run("INSERT INTO SessionTable VALUES('unknown-room@chatroom',1)")
    result = refresh_archive(cfg, adapter_factory=factory)
    assert result['status'] == 'complete'
    assert result['counts']['messages'] == 3
    unknown = next(c for c in active_rows(cfg, 'conversations') if c['source_username'] == 'unknown-room@chatroom')
    assert unknown['membership_status'] == 'unsupported'
    assert not any(m['conversation_id'] == unknown['conversation_id'] for m in active_rows(cfg, 'membership'))


def test_unmapped_message_table_cannot_silently_remove_indexed_history(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.archive.store import Archive
    cfg = import_case(tmp_path); prior = refresh_archive(cfg, adapter_factory=factory)
    # The table/history still exists, but the structured conversation row vanishes.
    with Engine(cfg.account_root / 'db_storage/session/session.db') as writer:
        writer.run(f"DELETE FROM SessionTable WHERE username='{CHAT}'")
    with Engine(cfg.account_root / 'db_storage/contact/contact.db') as writer:
        writer.run('DELETE FROM chat_room')
    failed = refresh_archive(cfg, adapter_factory=factory)
    assert failed['status'] == 'failed'
    assert failed['error'] == 'message_table_mapping_incomplete'
    assert Archive(cfg).status()['active_generation'] == prior['active_generation']
    assert len(active_rows(cfg)) == 3


@pytest.mark.parametrize('remove', ['rename', 'unlink'])
def test_missing_known_shard_preserves_history_and_reports_stale(tmp_path, remove):
    from wechat_bridge.archive.importer import refresh_archive, archive_status
    cfg = import_case(tmp_path); prior = refresh_archive(cfg, adapter_factory=factory)
    path = cfg.account_root / 'db_storage/message/message_1.db'
    if remove == 'rename':
        path.rename(path.with_suffix('.missing'))
    else:
        path.unlink()
    failed = refresh_archive(cfg, adapter_factory=factory)
    assert failed['status'] == 'failed'
    assert failed['error'] == 'source_shard_missing'
    assert failed['active_generation'] == prior['active_generation']
    assert len(active_rows(cfg)) == 3
    assert archive_status(cfg)['stale'] is True
    if remove == 'rename':
        path.with_suffix('.missing').rename(path)
        assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'complete'
        assert len(active_rows(cfg)) == 3


def test_watcher_refresh_cancelled_midway_like_client_disconnect_preserves_and_recovers(tmp_path, monkeypatch):
    import threading
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.archive.store import Archive
    from wechat_bridge.archive.watcher import ArchiveWatcher
    cfg = import_case(tmp_path)
    assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'complete'
    before = Archive(cfg).status()['active_generation']
    # The stdio server passes its lifespan stop event to the watcher as the refresh
    # cancel_event, so setting it during an active write models a client disconnect
    # mid native refresh.
    with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
        writer.run(f"UPDATE {TABLE} SET message_content='disconnect edit' WHERE local_id=1")
    stop = threading.Event()
    original = Archive.write_records
    def disconnect_midway(self, generation, kind, records):
        original(self, generation, kind, records)
        if kind == 'messages':
            stop.set()
    monkeypatch.setattr(Archive, 'write_records', disconnect_midway)
    watcher = ArchiveWatcher(cfg, adapter_factory=factory)
    assert watcher.poll(now=0.0, cancel_event=stop) is None            # debounce/periodic not yet due
    result = watcher.poll(now=100.0, cancel_event=stop)                # periodic reconcile triggers the refresh
    assert result['status'] == 'failed' and result['error'] == 'operation_cancelled'
    assert Archive(cfg).status()['active_generation'] == before
    assert not any(m['text'] == 'disconnect edit' for m in active_rows(cfg))
    monkeypatch.setattr(Archive, 'write_records', original)
    assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'complete'
    assert any(m['text'] == 'disconnect edit' for m in active_rows(cfg))


def test_refresh_reads_conversations_a_bounded_number_of_times(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    images = ','.join(f"({i},{900 + i},3,1,{1700000100 + i},'<msg><img /></msg>',0)" for i in range(10, 16))
    cfg = account_case(tmp_path, changes={'message/message_0.db': f"INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES{images}"})
    calls = []
    def counting(config):
        adapter = factory(config); original = adapter.conversations
        adapter.conversations = lambda: (calls.append(1), original())[1]
        return adapter
    assert refresh_archive(cfg, adapter_factory=counting)['status'] == 'complete'
    assert sum(m['type'] == 'image' for m in active_rows(cfg)) == 6
    assert len(calls) <= 2
