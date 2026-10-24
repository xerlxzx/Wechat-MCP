import ctypes
from pathlib import Path
import pytest
from tests.support.encrypted import create_database, KEY, LIBRARY


def source_case(tmp_path, *, wal=False):
    from wechat_bridge.core.config import create_config
    from wechat_bridge.source.discovery import discover
    root = tmp_path / "account"
    path = root / "db_storage/message/message_0.db"
    writer = create_database(path, wal=wal)
    cfg = create_config(tmp_path / "state", root)
    db = discover(cfg).databases[0]
    return path, writer, cfg, db


def test_native_fixture_is_encrypted_and_readable(tmp_path):
    path = tmp_path / "fixture.db"
    with create_database(path, wal=False) as writer:
        assert writer.run("SELECT id FROM messages") == [{"id": "1"}]
        assert writer.run("PRAGMA cipher_version")[0]["cipher_version"].startswith("4.")
    assert path.read_bytes()[:16] != b"SQLite format 3\x00"


def test_typed_values_and_bound_parameters(tmp_path):
    from wechat_bridge.source.reader import open_source
    path, writer, cfg, db = source_case(tmp_path)
    try:
        with open_source(db, KEY, config=cfg) as reader, reader.transaction() as tx:
            assert tx.query("SELECT 9007199254740993 AS n, NULL AS nil, payload, text FROM messages") == [
                {"n": 9007199254740993, "nil": None, "payload": b"\x00\xff\x00", "text": "发票 🐈"}]
            assert tx.query("SELECT ? AS value", ("x'; DROP TABLE messages; --",)) == [
                {"value": "x'; DROP TABLE messages; --"}]
            assert tx.query("SELECT ? AS value", (b"\x00\xff",)) == [{"value": b"\x00\xff"}]
    finally: writer.close()


@pytest.mark.parametrize("sql", [
    "DELETE FROM messages", "CREATE TABLE forbidden(x)", "ATTACH ':memory:' AS other",
    "PRAGMA rekey='bad'", "PRAGMA wal_checkpoint(TRUNCATE)", "PRAGMA query_only=OFF",
    "SELECT load_extension('bad')", "SELECT 1; DELETE FROM messages"])
def test_source_mutations_denied_without_changing_bytes(tmp_path, sql):
    from wechat_bridge.source.reader import open_source
    from wechat_bridge.core.errors import BridgeError
    path, writer, cfg, db = source_case(tmp_path); writer.close()
    before = path.read_bytes()
    with open_source(db, KEY, config=cfg) as reader, reader.transaction() as tx:
        with pytest.raises(BridgeError): tx.query(sql)
    assert path.read_bytes() == before


def test_wrong_key_rejected_and_redacted(tmp_path, capfd):
    from wechat_bridge.source.reader import open_source
    from wechat_bridge.core.errors import BridgeError
    path, writer, cfg, db = source_case(tmp_path); writer.close()
    with pytest.raises(BridgeError, match="key_or_format_mismatch") as error:
        open_source(db, b"z" * 32, config=cfg)
    assert str(path) not in str(error.value)
    out = capfd.readouterr()
    assert (b"z" * 32).hex() not in out.out + out.err


def test_missing_native_cipher_symbols_fail_closed():
    from wechat_bridge.source.native import load_native
    from wechat_bridge.core.errors import BridgeError
    with pytest.raises(BridgeError, match="sqlcipher_unavailable"):
        load_native(Path("/usr/lib/libSystem.B.dylib"))


def test_limits_and_statement_cleanup(tmp_path):
    from wechat_bridge.source.reader import open_source
    from wechat_bridge.core.errors import BridgeError
    path, writer, cfg, db = source_case(tmp_path)
    writer.close()
    with open_source(db, KEY, config=cfg) as reader:
        with reader.transaction() as tx:
            with pytest.raises(BridgeError, match="result_limit"):
                tx.query("SELECT 1 UNION ALL SELECT 2", max_rows=1)
            with pytest.raises(BridgeError, match="result_too_large"):
                tx.query("SELECT zeroblob(8388609) AS payload")
            assert tx.query("SELECT count(*) AS n FROM messages") == [{"n": 1}]
        with pytest.raises(KeyboardInterrupt):
            with reader.transaction() as tx:
                tx.query("SELECT id FROM messages")
                raise KeyboardInterrupt
        with reader.transaction() as tx:
            assert tx.query("SELECT id FROM messages") == [{"id": 1}]
    with pytest.raises(BridgeError, match="reader_closed"):
        with reader.transaction(): pass


def test_query_deadline_interrupts_expensive_query(tmp_path):
    from wechat_bridge.source.reader import open_source
    from wechat_bridge.core.errors import BridgeError
    path, writer, cfg, db = source_case(tmp_path); writer.close()
    with open_source(db, KEY, config=cfg) as reader:
        reader.query_timeout = 0.001
        with reader.transaction() as tx:
            with pytest.raises(BridgeError, match="query_timeout"):
                tx.query("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10000000) SELECT sum(x) FROM n")


def test_source_replacement_after_discovery_rejected(tmp_path):
    from wechat_bridge.source.reader import open_source
    from wechat_bridge.core.errors import BridgeError
    path, writer, cfg, db = source_case(tmp_path); writer.close()
    other = path.with_suffix(".replacement"); create_database(other, wal=False).close(); other.replace(path)
    with pytest.raises(BridgeError, match="source_changed"):
        open_source(db, KEY, config=cfg)


def test_actual_sigint_during_native_step_cancels_without_callback_noise(tmp_path):
    import subprocess
    import sys
    code = r'''
from pathlib import Path
from tempfile import TemporaryDirectory
import os, signal, threading, time
from tests.integration.test_reader import source_case
from tests.support.encrypted import KEY
from wechat_bridge.source.reader import open_source
# Python only installs its default SIGINT handler when SIGINT is not inherited as
# ignored. Backgrounded shell jobs and some CI runners start children that way, and
# the reader deliberately leaves a caller's explicit SIG_IGN alone, so pin the
# disposition this test is about instead of depending on how pytest was launched.
signal.signal(signal.SIGINT, signal.default_int_handler)
with TemporaryDirectory() as directory:
    _, writer, config, db = source_case(Path(directory).resolve()); writer.close()
    with open_source(db, KEY, config=config) as reader:
        reader.query_timeout = 0.5
        timer = threading.Timer(0.05, lambda: os.kill(os.getpid(), signal.SIGINT))
        try:
            with reader.transaction() as tx:
                timer.start()
                tx.query("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n")
        except KeyboardInterrupt:
            print("cancelled")
        finally:
            timer.cancel(); timer.join()
        with reader.transaction() as tx:
            assert tx.query("SELECT count(*) AS n FROM messages") == [{"n": 1}]
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'cancelled'
    assert 'Exception ignored' not in result.stderr
