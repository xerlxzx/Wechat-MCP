import os
import pytest
from tests.unit.test_setup import prepared
from wechat_bridge.core.config import read_private_json


def run(tmp_path, *, drop=0):
    from wechat_bridge.setup.capture import run_capture
    plan, backend, _ = prepared(tmp_path)
    backend.running = False
    for key in list(backend.candidates)[:drop]: del backend.candidates[key]
    return plan, backend, run_capture(plan, backend=backend)


def copies(plan): return [plan.directory / n for n in ("captured.json", "candidates.json")]


def test_verified_and_recovered_removes_both_copies(tmp_path):
    plan, backend, result = run(tmp_path)
    assert (result.status, result.recovery_status) == ("verified", "recovered")
    assert result.key_copies_removed is True
    assert not any(p.exists() for p in copies(plan))
    assert (plan.config.state_dir / "keys.json").is_file()
    assert (plan.directory / "plan.json").is_file() and (plan.directory / "journal.json").is_file()
    assert (plan.directory / "WeChat.disabled").exists()


def test_partial_coverage_keeps_copies(tmp_path):
    plan, _, result = run(tmp_path, drop=1)
    assert result.status == "partial_coverage" and result.recovery_status == "recovered"
    assert result.key_copies_removed is False
    assert all(p.exists() for p in copies(plan))


def test_unverified_keys_keep_copies(tmp_path):
    plan, backend, _ = prepared(tmp_path)
    from wechat_bridge.setup.capture import run_capture
    backend.running = False
    backend.candidates = {k: {"enc_key": "00" * 32} for k in backend.candidates}
    result = run_capture(plan, backend=backend)
    assert result.status != "verified" and result.key_copies_removed is False
    assert all(p.exists() for p in copies(plan))


def test_incomplete_recovery_keeps_copies_until_recover_completes(tmp_path):
    from wechat_bridge.setup.capture import run_capture
    from wechat_bridge.setup.recovery import recover_setup
    plan, backend, _ = prepared(tmp_path)
    backend.running = False
    backend.owned_identity = (1, "a", "/x"); backend.current_identity = (1, "b", "/y")
    result = run_capture(plan, backend=backend)
    assert result.status == "verified" and result.recovery_status == "manual_restore_required"
    assert result.key_copies_removed is False
    assert all(p.exists() for p in copies(plan))
    backend.current_identity = backend.owned_identity
    recovered = recover_setup(plan, backend=backend)
    assert recovered.status == "recovered" and recovered.key_copies_removed is True
    assert not any(p.exists() for p in copies(plan))


def test_symlinked_copy_is_not_followed(tmp_path):
    from wechat_bridge.setup.recovery import recover_setup
    plan, backend, _ = run(tmp_path)
    target = tmp_path / "outside.json"; target.write_text("keep")
    link = plan.directory / "candidates.json"; link.symlink_to(target)
    result = recover_setup(plan, backend=backend)
    assert result.status == "recovered" and result.key_copies_removed is False
    assert link.is_symlink() and target.read_text() == "keep"


def test_unrelated_files_untouched_and_second_run_idempotent(tmp_path):
    from wechat_bridge.setup.recovery import recover_setup
    plan, backend, _ = prepared(tmp_path)
    from wechat_bridge.setup.capture import run_capture
    backend.running = False
    (plan.directory / "notes.txt").write_text("x")
    assert run_capture(plan, backend=backend).key_copies_removed is True
    keys = (plan.config.state_dir / "keys.json").read_bytes()
    again = recover_setup(plan, backend=backend)
    assert again.status == "recovered" and again.key_copies_removed is True
    assert (plan.directory / "notes.txt").read_text() == "x"
    assert (plan.config.state_dir / "keys.json").read_bytes() == keys


def test_deletion_failure_does_not_fail_setup(tmp_path, monkeypatch):
    from wechat_bridge.setup import recovery
    real = os.unlink
    def failing(path, *args, **kwargs):
        if path == "captured.json": raise PermissionError("denied")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(recovery.os, "unlink", failing)
    plan, _, result = run(tmp_path)
    assert (result.status, result.recovery_status) == ("verified", "recovered")
    assert result.key_copies_removed is False
    assert (plan.directory / "captured.json").exists()
    assert read_private_json(plan.directory / "journal.json")["recovered"] is True


def _verified_plan_with_copies(tmp_path):
    from wechat_bridge.core.config import write_private_json
    plan, _, result = run(tmp_path)
    assert result.key_copies_removed is True
    for path in copies(plan): path.write_text("{}")
    return plan, plan.directory / "journal.json", write_private_json


def test_guard_control_removes_when_journal_and_keys_are_good(tmp_path):
    from wechat_bridge.setup.recovery import _remove_key_copies
    plan, *_ = _verified_plan_with_copies(tmp_path)
    assert _remove_key_copies(plan) is True
    assert not any(p.exists() for p in copies(plan))


@pytest.mark.parametrize("change", [
    {"capture_status": "partial_coverage"},
    {"capture_status": "cancelled"},
    {"capture_status": None},
    {"recovered": False},
    {"recovered": "yes"},
    {"coverage": None},
    {"coverage": {"ready": False, "verified_count": 1}},
    {"coverage": {"ready": "true", "verified_count": 1}},
])
def test_each_journal_guard_alone_keeps_copies(tmp_path, change):
    from wechat_bridge.core.config import read_private_json
    from wechat_bridge.setup.recovery import _remove_key_copies
    plan, journal_path, write = _verified_plan_with_copies(tmp_path)
    write(journal_path, read_private_json(journal_path) | change)
    assert _remove_key_copies(plan) is False
    assert all(p.exists() for p in copies(plan))


@pytest.mark.parametrize("keys", [None, {}, {"entries": []}, {"entries": "x"}])
def test_missing_or_empty_keys_json_keeps_copies(tmp_path, keys):
    from wechat_bridge.core.config import write_private_json
    from wechat_bridge.setup.recovery import _remove_key_copies
    plan, *_ = _verified_plan_with_copies(tmp_path)
    keys_path = plan.config.state_dir / "keys.json"
    keys_path.unlink() if keys is None else write_private_json(keys_path, keys)
    assert _remove_key_copies(plan) is False
    assert all(p.exists() for p in copies(plan))


def test_fewer_stored_keys_than_verified_keeps_copies(tmp_path):
    from wechat_bridge.core.config import read_private_json, write_private_json
    from wechat_bridge.setup.recovery import _remove_key_copies
    plan, *_ = _verified_plan_with_copies(tmp_path)
    keys_path = plan.config.state_dir / "keys.json"
    data = read_private_json(keys_path)
    assert len(data["entries"]) >= 2
    write_private_json(keys_path, data | {"entries": data["entries"][:-1]})
    assert _remove_key_copies(plan) is False
    assert all(p.exists() for p in copies(plan))
