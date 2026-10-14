import os
import sqlite3

import pytest

from wechat_bridge.core.config import Config
from wechat_bridge.core.errors import BridgeError


def archive_case(tmp_path):
    from wechat_bridge.archive.store import Archive
    cfg = Config(tmp_path.resolve() / 'state', tmp_path.resolve() / 'account')
    return cfg, Archive(cfg)


def contact(value='Alice'):
    return {'contact_id': 'contact_test', 'display_name': value}


def active_contacts(archive):
    with archive.connection() as db:
        return db.execute('SELECT data FROM contacts WHERE generation=(SELECT active_generation FROM archive_state)').fetchall()


def test_generation_is_invisible_until_atomic_publication(tmp_path):
    import json
    cfg, archive = archive_case(tmp_path)
    generation = archive.begin_generation('account_test')
    archive.write_records(generation, 'contacts', [contact()])
    assert archive.status()['active_generation'] is None
    assert active_contacts(archive) == []
    archive.publish(generation, {'coverage': {'missing_message_tables': 2}})
    assert json.loads(active_contacts(archive)[0]['data']) == contact()
    assert archive.status()['counts']['contacts'] == 1
    assert archive.status()['coverage']['missing_message_tables'] == 2
    assert (cfg.state_dir / 'archive.sqlite3').stat().st_mode & 0o777 == 0o600
    assert cfg.state_dir.stat().st_mode & 0o777 == 0o700


def test_unpublished_stage_survives_crash_without_becoming_readable(tmp_path):
    from wechat_bridge.archive.store import Archive
    cfg, archive = archive_case(tmp_path)
    first = archive.begin_generation('account_test')
    archive.write_records(first, 'contacts', [contact()])
    archive.publish(first, {})
    stage = archive.begin_generation('account_test')
    archive.write_records(stage, 'contacts', [contact('Partial')])
    reopened = Archive(cfg)
    assert reopened.status()['active_generation'] == first
    assert 'Partial' not in active_contacts(reopened)[0]['data']
    retry = reopened.begin_generation('account_test')
    with reopened.connection() as db:
        assert db.execute('SELECT state FROM generations WHERE generation=?', (stage,)).fetchone()['state'] == 'failed'
    reopened.write_records(retry, 'contacts', [contact('Complete')])
    reopened.publish(retry, {})
    assert reopened.status()['active_generation'] == retry


def test_abandoned_building_stage_rows_are_reclaimed_on_next_begin(tmp_path):
    _, archive = archive_case(tmp_path)
    first = archive.begin_generation('account_test'); archive.publish(first, {})
    # Simulate a crash: a building stage with partial rows and no fail()/publish().
    stage = archive.begin_generation('account_test')
    archive.write_records(stage, 'contacts', [contact('Partial')])
    with archive.connection() as db:
        assert db.execute('SELECT count(*) FROM contacts WHERE generation=?', (stage,)).fetchone()[0] == 1
    retry = archive.begin_generation('account_test')
    with archive.connection() as db:
        assert db.execute('SELECT count(*) FROM contacts WHERE generation=?', (stage,)).fetchone()[0] == 0
        assert db.execute('SELECT state FROM generations WHERE generation=?', (stage,)).fetchone()['state'] == 'failed'
    archive.publish(retry, {})
    assert archive.status()['active_generation'] == retry


def test_failed_refresh_preserves_active_records_and_reports_staleness(tmp_path):
    _, archive = archive_case(tmp_path)
    first = archive.begin_generation('account_test')
    archive.write_records(first, 'contacts', [contact()])
    archive.publish(first, {})
    stage = archive.begin_generation('account_test')
    archive.fail(stage, 'source_unreadable')
    assert archive.status()['active_generation'] == first
    assert archive.status()['last_refresh_error'] == 'source_unreadable'
    assert len(active_contacts(archive)) == 1


def test_publish_rolls_back_all_changes_when_pointer_update_fails(tmp_path):
    _, archive = archive_case(tmp_path)
    first = archive.begin_generation('account_test'); archive.publish(first, {})
    stage = archive.begin_generation('account_test')
    with archive.connection(write=True) as db:
        db.execute("CREATE TRIGGER refuse_publish BEFORE UPDATE ON archive_state BEGIN SELECT RAISE(ABORT,'refused'); END")
    with pytest.raises(BridgeError):
        archive.publish(stage, {})
    assert archive.status()['active_generation'] == first
    with archive.connection() as db:
        assert db.execute('SELECT state FROM generations WHERE generation=?', (stage,)).fetchone()['state'] == 'building'


def test_completed_generations_cannot_be_mutated_or_mixed_accounts(tmp_path):
    _, archive = archive_case(tmp_path)
    generation = archive.begin_generation('account_test'); archive.publish(generation, {})
    with pytest.raises(BridgeError, match='invalid_generation'):
        archive.write_records(generation, 'contacts', [contact()])
    with pytest.raises(BridgeError, match='archive_account_mismatch'):
        archive.begin_generation('other_account')
    with archive.connection() as db:
        with pytest.raises(sqlite3.DatabaseError):
            db.execute('DELETE FROM generations')


def test_generation_retention_keeps_current_and_previous_complete(tmp_path):
    _, archive = archive_case(tmp_path)
    for i in range(4):
        generation = archive.begin_generation('account_test')
        archive.write_records(generation, 'contacts', [contact(str(i))])
        archive.publish(generation, {})
    with archive.connection() as db:
        assert [r['generation'] for r in db.execute('SELECT generation FROM generations WHERE state="complete" ORDER BY generation')] == [3, 4]
        assert db.execute('SELECT count(*) FROM contacts').fetchone()[0] == 2


def test_unsupported_archive_schema_and_unsafe_files_fail_closed(tmp_path):
    from wechat_bridge.archive.store import Archive
    cfg, archive = archive_case(tmp_path)
    with archive.connection(write=True) as db:
        db.execute('PRAGMA user_version=999')
    with pytest.raises(BridgeError, match='unsupported_archive_schema'):
        Archive(cfg)
    os.chmod(cfg.state_dir / 'archive.sqlite3', 0o644)
    with pytest.raises(BridgeError, match='private_permissions'):
        Archive(cfg)


def test_archive_symlink_and_unbounded_batch_are_rejected(tmp_path):
    from wechat_bridge.archive.store import Archive
    cfg, archive = archive_case(tmp_path)
    generation = archive.begin_generation('account_test')
    with pytest.raises(BridgeError, match='invalid_batch'):
        archive.write_records(generation, 'contacts', [contact()] * 201)
    with pytest.raises(BridgeError, match='invalid_record_kind'):
        archive.write_records(generation, 'arbitrary_sql', [])
    (cfg.state_dir / 'archive.sqlite3').unlink()
    (cfg.state_dir / 'archive.sqlite3').symlink_to(tmp_path.resolve() / 'other.db')
    with pytest.raises(BridgeError, match='unsafe_archive_path'):
        Archive(cfg)


def test_reader_keeps_previous_complete_view_during_publication(tmp_path):
    _, archive = archive_case(tmp_path)
    first = archive.begin_generation('account_test'); archive.publish(first, {})
    stage = archive.begin_generation('account_test')
    with archive.connection() as reader:
        assert reader.execute('SELECT active_generation FROM archive_state').fetchone()[0] == first
        archive.publish(stage, {})
        assert reader.execute('SELECT active_generation FROM archive_state').fetchone()[0] == first
    assert archive.status()['active_generation'] == stage


def test_empty_header_after_interrupted_schema_creation_can_retry(tmp_path):
    from wechat_bridge.core.config import ensure_private_dir
    from wechat_bridge.archive.store import Archive
    cfg = Config(tmp_path.resolve() / 'state', tmp_path.resolve() / 'account')
    ensure_private_dir(cfg.state_dir)
    path = cfg.state_dir / 'archive.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
    os.chmod(path, 0o600)
    assert path.stat().st_size > 0
    assert Archive(cfg).status()['active_generation'] is None


def test_repeated_failures_keep_only_the_latest_failed_generation(tmp_path):
    _, archive = archive_case(tmp_path)
    first = archive.begin_generation('account_test')
    archive.write_records(first, 'contacts', [contact()])
    archive.publish(first, {})
    for code in ('source_unreadable', 'key_coverage_incomplete', 'source_changed'):
        archive.fail(archive.begin_generation('account_test'), code)
    building = archive.begin_generation('account_test')
    with archive.connection() as db:
        rows = [(r['state'], r['error']) for r in db.execute('SELECT state,error FROM generations ORDER BY generation')]
    assert rows == [('complete', None), ('failed', 'source_changed'), ('building', None)]
    archive.fail(building, 'source_unreadable')
    assert archive.status()['active_generation'] == first
    assert archive.status()['last_refresh_error'] == 'source_unreadable'
    assert len(active_contacts(archive)) == 1
