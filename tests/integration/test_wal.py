import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from tests.support.encrypted import Engine, KEY
from tests.integration.test_reader import source_case


def test_snapshot_excludes_uncommitted_and_sees_next_commit(tmp_path):
    from wechat_bridge.source.reader import open_source
    path, writer, cfg, db = source_case(tmp_path, wal=True)
    try:
        writer.run("BEGIN; INSERT INTO messages VALUES(2, 'pending', NULL)")
        with open_source(db, KEY, config=cfg) as reader:
            with reader.transaction() as tx:
                assert tx.query("SELECT id FROM messages ORDER BY id") == [{"id": 1}]
                writer.run("COMMIT")
                assert tx.query("SELECT id FROM messages ORDER BY id") == [{"id": 1}]
            with reader.transaction() as tx:
                assert tx.query("SELECT id FROM messages ORDER BY id") == [{"id": 1}, {"id": 2}]
        with open_source(db, KEY, config=cfg) as reader, reader.transaction() as tx:
            assert tx.query("SELECT count(*) AS n FROM messages") == [{"n": 2}]
        writer.run("BEGIN; INSERT INTO messages VALUES(3, 'rolled back', NULL); ROLLBACK")
        with open_source(db, KEY, config=cfg) as reader, reader.transaction() as tx:
            assert tx.query("SELECT count(*) AS n FROM messages") == [{"n": 2}]
    finally: writer.close()


def test_busy_timeout_is_bounded(tmp_path):
    from wechat_bridge.source.reader import open_source
    from wechat_bridge.core.errors import BridgeError
    path, writer, cfg, db = source_case(tmp_path)
    writer.run("BEGIN EXCLUSIVE")
    start = time.monotonic()
    try:
        with pytest.raises(BridgeError, match="source_busy"):
            open_source(db, KEY, config=cfg)
        assert time.monotonic() - start < 3.5
    finally:
        writer.run("ROLLBACK"); writer.close()


@pytest.mark.parametrize("damage", ["no_shm", "truncated", "invalid_salt"])
def test_stopped_wal_fixture_matches_engine_oracle(tmp_path, damage):
    from wechat_bridge.core.config import create_config
    from wechat_bridge.source.discovery import discover
    from wechat_bridge.source.reader import open_source
    from wechat_bridge.core.errors import BridgeError
    root = tmp_path / "account"; path = root / "db_storage/message/message_0.db"
    # Abrupt exit leaves a committed WAL without asking our reader to checkpoint.
    code = "from tests.support.encrypted import create_database; import os,sys; w=create_database(__import__('pathlib').Path(sys.argv[1])); os._exit(0)"
    subprocess.run([sys.executable, "-c", code, str(path)], check=True)
    wal = path.with_name(path.name + "-wal")
    original = wal.read_bytes()
    if damage == "truncated": wal.write_bytes(original[:-100])
    if damage == "invalid_salt": wal.write_bytes(original[:16] + bytes(8) + original[24:])
    path.with_name(path.name + "-shm").unlink(missing_ok=True)
    expected = None
    try:
        with Engine(path, readonly=True) as oracle:
            expected = oracle.run("SELECT id FROM messages ORDER BY id")
    except RuntimeError:
        pass
    cfg = create_config(tmp_path / "state", root); db = discover(cfg).databases[0]
    if expected is None:
        with pytest.raises(BridgeError):
            with open_source(db, KEY, config=cfg) as reader, reader.transaction() as tx:
                tx.query("SELECT id FROM messages ORDER BY id")
    else:
        with open_source(db, KEY, config=cfg) as reader, reader.transaction() as tx:
            assert tx.query("SELECT id FROM messages ORDER BY id") == [{"id": int(r["id"])} for r in expected]
