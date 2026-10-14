from types import SimpleNamespace

import pytest

from wechat_bridge.core.errors import BridgeError
from wechat_bridge.setup.system import SystemBackend


@pytest.mark.parametrize("exit_code, expected", [
    (2, "capture_failed"), (3, "capture_timeout"), (4, "capture_process_ended"),
])
def test_capture_subprocess_reports_distinct_failure(tmp_path, monkeypatch, exit_code, expected):
    backend = SystemBackend()
    backend.lldb_python = ""
    monkeypatch.setattr(backend, "preflight", lambda: None)
    monkeypatch.setattr("wechat_bridge.setup.system.subprocess.Popen",
                        lambda *args, **kwargs: SimpleNamespace(wait=lambda **kw: exit_code))
    plan = SimpleNamespace(directory=tmp_path, copy_path=tmp_path / "trial.app",
                           config=SimpleNamespace(account_root=tmp_path / "account"))
    with pytest.raises(BridgeError, match=f"^{expected}$"):
        backend.capture(plan)
