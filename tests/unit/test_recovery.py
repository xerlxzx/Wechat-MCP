from tests.unit.test_setup import prepared
from wechat_bridge.core.config import read_private_json, write_private_json


def test_recovery_idempotent_and_never_stops_reused_pid(tmp_path):
    from wechat_bridge.setup.recovery import recover_setup
    plan, backend, _ = prepared(tmp_path)
    backend.running = False
    backend.copy_bundle(backend.app, plan.copy_path)
    backend.owned_identity = (123, "start-a", str(plan.copy_path))
    backend.current_identity = (123, "start-b", "/other-app")
    result = recover_setup(plan, backend=backend)
    assert result.status == "manual_restore_required"
    assert backend.stopped == []
    assert plan.copy_path.exists()
    backend.current_identity = backend.owned_identity
    result = recover_setup(plan, backend=backend)
    assert result.status == "recovered"
    assert backend.stopped == [backend.owned_identity]
    assert not plan.copy_path.exists()
    assert backend.launched == 1
    recover_setup(plan, backend=backend)
    assert backend.launched == 1


def test_recovery_does_not_start_previously_closed_client(tmp_path):
    from dataclasses import replace
    from wechat_bridge.setup.recovery import recover_setup
    plan, backend, _ = prepared(tmp_path)
    plan = replace(plan, restore_original=False); backend.running = False
    result = recover_setup(plan, backend=backend)
    assert result.status == "recovered"
    assert backend.launched == 0


def test_changed_installed_signature_is_not_launched(tmp_path):
    from wechat_bridge.setup.recovery import recover_setup
    plan, backend, _ = prepared(tmp_path)
    backend.signature_value = "changed"; backend.running = False
    result = recover_setup(plan, backend=backend)
    assert result.status == "original_signature_changed"
    assert backend.launched == 0


def test_unknown_process_inspection_does_not_prove_exit(tmp_path, monkeypatch):
    import subprocess
    from wechat_bridge.setup.system import SystemBackend
    plan, _, _ = prepared(tmp_path)
    write_private_json(plan.directory / 'owned.json', {
        'pid': 999999, 'started': 'saved-start',
        'executable': str(plan.copy_path / 'Contents/MacOS/WeChat')})
    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired('ps', 5)
    monkeypatch.setattr(subprocess, 'run', unavailable)
    assert SystemBackend().stop_owned(plan) is False
