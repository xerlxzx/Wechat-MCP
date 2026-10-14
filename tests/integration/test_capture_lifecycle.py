"""Exercise the real LLDB helper against our own harmless executable only."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.support.encrypted import create_database
from wechat_bridge.setup import lldb_capture
from wechat_bridge.setup.system import process_identity


@pytest.mark.parametrize("exit_code", [0, 255])
def test_native_process_exit_without_keys_is_not_timeout(tmp_path, exit_code):
    result, events, directory = run_probe(tmp_path, f"return {exit_code};")
    assert result.returncode == 4, (result.stdout, result.stderr)
    ended = next(e for e in events if e["type"] == "process_ended")
    assert ended["exit_status"] == exit_code
    assert not (directory / "captured.json").exists()


def test_native_capture_deadline_remains_timeout_and_closes_owned_process(tmp_path):
    result, events, directory = run_probe(tmp_path, "sleep(60); return 0;")
    assert result.returncode == 3, (result.stdout, result.stderr)
    assert not any(e["type"] == "process_ended" for e in events)
    owned = json.loads((directory / "owned.json").read_text())
    assert process_identity(owned["pid"]) is None
    assert not (directory / "captured.json").exists()


def run_probe(tmp_path, body):
    directory = tmp_path.resolve()
    source, executable = directory / "probe.c", directory / "probe"
    source.write_text("#include <unistd.h>\nint main(void) { " + body + " }\n")
    subprocess.run(["/usr/bin/clang", str(source), "-o", str(executable)],
                   check=True, capture_output=True, timeout=30)
    root = directory / "db"
    create_database(root / "fixture.db", wal=False).close()
    lldb_python = subprocess.check_output(["/usr/bin/lldb", "-P"], text=True).strip()
    result = subprocess.run([
        "/usr/bin/python3", str(Path(lldb_capture.__file__)),
        "--exe", str(executable), "--db-root", str(root),
        "--out", str(directory / "captured.json"),
        "--owned", str(directory / "owned.json"), "--timeout", "10",
    ], env=dict(os.environ, PYTHONPATH=lldb_python),
        capture_output=True, text=True, timeout=45)
    events = [json.loads(line) for line in result.stdout.splitlines()]
    return result, events, directory
