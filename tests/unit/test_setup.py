from dataclasses import replace
import pytest
from tests.support.setup import FakeBackend
from tests.integration.test_key_coverage import case
from wechat_bridge.core.config import read_private_json


def prepared(tmp_path):
    from wechat_bridge.setup.prepare import prepare_setup
    cfg, inventory, manifest = case(tmp_path)
    backend = FakeBackend(tmp_path)
    backend.candidates = {e.relative_path: {"enc_key": e.raw_key.hex()} for e in manifest.entries}
    plan = prepare_setup(cfg, inventory, backend=backend)
    return plan, backend, manifest


def test_prepare_is_read_only_toward_app(tmp_path):
    plan, backend, manifest = prepared(tmp_path)
    assert (backend.app / "Contents/MacOS/WeChat").read_bytes() == b"installed-app"
    assert backend.running
    assert backend.signed == []
    assert backend.launched == 0
    assert (plan.directory / "plan.json").stat().st_mode & 0o777 == 0o600
    assert not plan.copy_path.exists()
    assert str(plan.config.account_root) not in str(plan.public_dict())


def test_run_requires_user_to_close_client(tmp_path):
    from wechat_bridge.setup.capture import run_capture
    from wechat_bridge.core.errors import BridgeError
    plan, backend, _ = prepared(tmp_path)
    with pytest.raises(BridgeError, match="client_exit_required"): run_capture(plan, backend=backend)
    assert not plan.copy_path.exists()


@pytest.mark.parametrize("kind", ["expired", "signature", "source"])
def test_stale_setup_refused_before_copy(tmp_path, kind):
    from wechat_bridge.setup.capture import run_capture
    from wechat_bridge.core.errors import BridgeError
    plan, backend, _ = prepared(tmp_path); backend.running = False
    if kind == "expired": plan = replace(plan, expires_at=0)
    elif kind == "signature": backend.signature_value = "changed"
    else:
        path = plan.config.account_root / "db_storage/message/message_0.db"
        replacement = path.with_suffix(".new"); replacement.write_bytes(b"x" * 4096); replacement.replace(path)
    with pytest.raises(BridgeError, match="setup_plan_stale"): run_capture(plan, backend=backend)
    assert not plan.copy_path.exists()


@pytest.mark.parametrize("failure", ["capture_timeout", "phone_confirmation_timeout", "sign_failed", "cancel"])
def test_failure_preserves_installed_app_and_saved_keys(tmp_path, failure):
    from wechat_bridge.setup.capture import run_capture
    from wechat_bridge.source.keys import verify_keys, publish_verified_manifest
    from wechat_bridge.source.discovery import discover
    plan, backend, manifest = prepared(tmp_path)
    publish_verified_manifest(manifest, verify_keys(discover(plan.config), manifest, plan.config), plan.config)
    before = (plan.config.state_dir / "keys.json").read_bytes()
    backend.running = False; backend.fail = failure
    result = run_capture(plan, backend=backend)
    assert result.status == ("cancelled" if failure == "cancel" else failure)
    assert (backend.app / "Contents/MacOS/WeChat").read_bytes() == b"installed-app"
    assert (plan.config.state_dir / "keys.json").read_bytes() == before
    assert not plan.copy_path.exists()
    assert backend.launched == 1


def test_success_requires_native_key_verification(tmp_path):
    from wechat_bridge.setup.capture import run_capture
    from wechat_bridge.source.keys import load_manifest
    plan, backend, _ = prepared(tmp_path); backend.running = False
    result = run_capture(plan, backend=backend)
    assert result.status == "verified"
    assert len(load_manifest(plan.config.state_dir / "keys.json").entries) == 5
    assert all(p == plan.copy_path for p in backend.signed)
    assert backend.launched == 1


def test_journal_error_does_not_skip_recovery(tmp_path, monkeypatch):
    from wechat_bridge.setup import capture
    from wechat_bridge.core.errors import BridgeError
    plan, backend, _ = prepared(tmp_path); backend.running = False
    original = capture.update_journal
    def fail_final(plan, **changes):
        if 'capture_status' in changes:
            raise BridgeError('state_write_failed')
        return original(plan, **changes)
    monkeypatch.setattr(capture, 'update_journal', fail_final)
    result = capture.run_capture(plan, backend=backend)
    assert result.status == 'journal_write_failed'
    assert not plan.copy_path.exists()
    assert backend.launched == 1


def test_concurrent_plans_cannot_enter_capture(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from wechat_bridge.setup.prepare import prepare_setup
    from wechat_bridge.setup.capture import run_capture
    from wechat_bridge.source.discovery import discover
    from wechat_bridge.core.errors import BridgeError
    plan, backend, _ = prepared(tmp_path)
    second = prepare_setup(plan.config, discover(plan.config), backend=backend)
    backend.running = False
    entered, release = threading.Event(), threading.Event()
    capture = backend.capture
    def slow(plan):
        entered.set()
        assert release.wait(3)
        capture(plan)
    backend.capture = slow
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_capture, plan, backend=backend)
        assert entered.wait(2)
        second_run = pool.submit(run_capture, second, backend=backend)
        try:
            error = second_run.exception(timeout=0.3)
            assert isinstance(error, BridgeError)
            assert error.code == 'setup_busy'
        finally:
            release.set()
        assert first.result(timeout=3).status == 'verified'


def test_launch_rechecks_client_state_after_signing(tmp_path):
    from wechat_bridge.setup.capture import run_capture
    plan, backend, _ = prepared(tmp_path); backend.running = False
    sign = backend.sign_copy
    def reopen(path):
        sign(path); backend.running = True
    backend.sign_copy = reopen
    result = run_capture(plan, backend=backend)
    assert result.status == 'client_exit_required'
    assert not (plan.directory / 'captured.json').exists()


def test_unrecovered_prior_plan_blocks_new_setup(tmp_path):
    from wechat_bridge.setup.capture import run_capture
    from wechat_bridge.core.config import write_private_json
    from wechat_bridge.core.errors import BridgeError
    plan, backend, _ = prepared(tmp_path); backend.running = False
    write_private_json(plan.config.state_dir / 'setup/active.json', {'plan_id': 'other-plan'})
    with pytest.raises(BridgeError, match='setup_recovery_required'):
        run_capture(plan, backend=backend)
    assert not plan.copy_path.exists()
