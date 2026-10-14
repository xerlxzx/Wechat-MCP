import threading

import pytest

from tests.integration.test_importer import import_case, factory, active_rows
from tests.support.encrypted import Engine
from tests.support.schemas import TABLE
from wechat_bridge.core.errors import BridgeError


def prepared(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    cfg = import_case(tmp_path)
    result = refresh_archive(cfg, adapter_factory=factory)
    return cfg, result


def test_wal_change_triggers_debounced_refresh(tmp_path):
    from wechat_bridge.archive.watcher import ArchiveWatcher
    from wechat_bridge.archive.importer import archive_status
    cfg, prior = prepared(tmp_path)
    watcher = ArchiveWatcher(cfg, debounce_seconds=1, reconcile_seconds=10, adapter_factory=factory)
    assert watcher.poll(now=0) is None
    with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
        writer.run(f"UPDATE {TABLE} SET message_content='WAL changed' WHERE local_id=1")
        assert archive_status(cfg)['stale']
        assert watcher.poll(now=1) is None
        assert watcher.poll(now=1.5) is None
        result = watcher.poll(now=2.1)
        assert result['active_generation'] != prior['active_generation']
        assert any(m['text'] == 'WAL changed' for m in active_rows(cfg))


def test_periodic_reconciliation_covers_missed_event(tmp_path, monkeypatch):
    from wechat_bridge.archive.watcher import ArchiveWatcher
    from wechat_bridge.archive.store import Archive
    cfg, _ = prepared(tmp_path)
    watcher = ArchiveWatcher(cfg, reconcile_seconds=10, adapter_factory=factory)
    assert watcher.poll(now=0) is None
    old = Archive(cfg).status()['source_fingerprint']
    # Only event detection is held unchanged; native reconciliation remains real.
    monkeypatch.setattr('wechat_bridge.archive.watcher.source_fingerprint', lambda _: old)
    with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
        writer.run(f"UPDATE {TABLE} SET message_content='Missed event' WHERE local_id=1")
    assert watcher.poll(now=9) is None
    assert watcher.poll(now=10)['status'] == 'complete'
    assert any(m['text'] == 'Missed event' for m in active_rows(cfg))


def test_failed_refresh_retries_periodically_without_losing_active_data(tmp_path):
    from wechat_bridge.archive.watcher import ArchiveWatcher
    from wechat_bridge.archive.importer import archive_status
    cfg, prior = prepared(tmp_path)
    failing = True
    def flaky(config):
        if failing:
            raise BridgeError('source_unreadable')
        return factory(config)
    watcher = ArchiveWatcher(cfg, reconcile_seconds=10, adapter_factory=flaky)
    assert watcher.poll(now=0) is None
    assert watcher.poll(now=10)['status'] == 'failed'
    assert archive_status(cfg)['active_generation'] == prior['active_generation']
    assert archive_status(cfg)['stale']
    assert len(active_rows(cfg)) == 3
    failing = False
    assert watcher.poll(now=11) is None
    assert watcher.poll(now=20)['status'] == 'complete'
    assert archive_status(cfg)['last_refresh_error'] is None


@pytest.mark.parametrize('code', ['key_coverage_incomplete', 'setup_required'])
def test_key_dependent_failure_waits_for_key_state_change(tmp_path, code):
    from wechat_bridge.archive.watcher import ArchiveWatcher
    from wechat_bridge.archive.store import Archive
    from wechat_bridge.core.config import read_private_json, write_private_json
    from wechat_bridge.source.discovery import discover
    cfg, prior = prepared(tmp_path)
    failing = True
    def blocked(config):
        discover(config)  # Like open_adapter, every attempt rewrites inventory.json.
        if failing:
            raise BridgeError(code)
        return factory(config)
    def latest_generation():
        with Archive(cfg).connection() as db:
            return db.execute('SELECT max(generation) FROM generations').fetchone()[0]
    watcher = ArchiveWatcher(cfg, debounce_seconds=1, reconcile_seconds=10, adapter_factory=blocked)
    assert watcher.poll(now=0) is None
    assert watcher.poll(now=10)['error'] == code
    attempted = latest_generation()
    assert Archive(cfg).status()['last_refresh_error'] == code
    # Neither the periodic reconcile nor a source write can change key coverage.
    with Engine(cfg.account_root / 'db_storage/message/message_0.db') as writer:
        writer.run(f"UPDATE {TABLE} SET message_content='blocked edit' WHERE local_id=1")
        assert watcher.poll(now=11) is None
        assert watcher.poll(now=12.5) is None
    assert watcher.poll(now=30) is None
    assert watcher.poll(now=3609) is None
    assert latest_generation() == attempted
    assert Archive(cfg).status()['active_generation'] == prior['active_generation']
    # A transient read can masquerade as missing coverage, so the hourly ceiling still retries.
    assert watcher.poll(now=3610)['error'] == code
    assert latest_generation() == attempted + 1
    assert watcher.poll(now=3700) is None
    failing = False
    keys = cfg.state_dir / 'keys.json'
    write_private_json(keys, read_private_json(keys))
    result = watcher.poll(now=3701)
    assert result['status'] == 'complete'
    assert any(m['text'] == 'blocked edit' for m in active_rows(cfg))


def test_watcher_stops_cleanly_and_requires_explicit_initial_import(tmp_path):
    from wechat_bridge.archive.watcher import ArchiveWatcher
    from wechat_bridge.archive.store import Archive
    cfg = import_case(tmp_path)
    with pytest.raises(BridgeError, match='archive_not_ready'):
        ArchiveWatcher(cfg)
    cfg, prior = prepared(tmp_path / 'prepared')
    watcher = ArchiveWatcher(cfg, adapter_factory=factory)
    stop = threading.Event(); stop.set()
    watcher.run(stop)
    assert Archive(cfg).status()['active_generation'] == prior['active_generation']


@pytest.mark.parametrize('values', [{'debounce_seconds': 0}, {'reconcile_seconds': -1}, {'reconcile_seconds': float('nan')}])
def test_invalid_watch_intervals_are_rejected(tmp_path, values):
    from wechat_bridge.archive.watcher import ArchiveWatcher
    cfg, _ = prepared(tmp_path)
    with pytest.raises(BridgeError, match='invalid_watch_interval'):
        ArchiveWatcher(cfg, **values)


def test_unsupported_build_backs_off_until_hourly_ceiling(tmp_path):
    from wechat_bridge.archive.watcher import ArchiveWatcher
    from wechat_bridge.archive.store import Archive
    cfg, prior = prepared(tmp_path)
    attempts = []
    def unsupported(config):
        attempts.append(1)
        raise BridgeError('unsupported_build')
    watcher = ArchiveWatcher(cfg, debounce_seconds=1, reconcile_seconds=10, adapter_factory=unsupported)
    assert watcher.poll(now=0) is None
    assert watcher.poll(now=10)['error'] == 'unsupported_build'
    for now in (20, 30, 600, 3609):
        assert watcher.poll(now=now) is None
    assert len(attempts) == 1
    assert watcher.poll(now=3610)['error'] == 'unsupported_build'
    assert len(attempts) == 2
    assert Archive(cfg).status()['active_generation'] == prior['active_generation']


def test_transient_failures_are_not_backed_off():
    from wechat_bridge.archive.watcher import BLOCKED_ERRORS
    assert 'source_unreadable' not in BLOCKED_ERRORS
    assert {'unsupported_build', 'setup_required', 'key_coverage_incomplete'} <= BLOCKED_ERRORS


def test_watcher_lock_admits_one_holder_and_survives_stale_file(tmp_path):
    import os
    from wechat_bridge.archive.watcher import _try_watcher_lock
    cfg, _ = prepared(tmp_path)
    first = _try_watcher_lock(cfg)
    assert first is not None
    assert _try_watcher_lock(cfg) is None
    os.close(first)
    # The lock file persists after release; a stale file never blocks.
    assert (cfg.state_dir / '.archive-watcher.lock').exists()
    again = _try_watcher_lock(cfg)
    assert again is not None
    os.close(again)


def test_run_exclusive_waits_while_held_then_takes_over(tmp_path, monkeypatch):
    import os
    from wechat_bridge.archive import watcher as module
    cfg, _ = prepared(tmp_path)
    held = module._try_watcher_lock(cfg)
    ran = threading.Event()
    class Fake:
        def __init__(self, config, **kwargs): pass
        def run(self, stop_event): ran.set()
    monkeypatch.setattr(module, 'ArchiveWatcher', Fake)
    stop = threading.Event()
    thread = threading.Thread(target=module.run_exclusive, args=(cfg, stop), kwargs={'retry_seconds': 0.05})
    thread.start()
    try:
        assert not ran.wait(0.3)
        os.close(held)
        assert ran.wait(3)
        thread.join(3)
        assert not thread.is_alive()
        free = module._try_watcher_lock(cfg)  # the holder released on exit
        assert free is not None
        os.close(free)
    finally:
        stop.set()
        thread.join(3)


def test_run_exclusive_stops_while_waiting(tmp_path):
    import os
    from wechat_bridge.archive import watcher as module
    cfg, _ = prepared(tmp_path)
    held = module._try_watcher_lock(cfg)
    stop = threading.Event()
    thread = threading.Thread(target=module.run_exclusive, args=(cfg, stop), kwargs={'retry_seconds': 30})
    thread.start()
    stop.set()
    thread.join(3)
    os.close(held)
    assert not thread.is_alive()
