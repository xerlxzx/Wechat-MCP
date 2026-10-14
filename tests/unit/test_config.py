import json
import os
import pytest


def test_private_config_roundtrip(tmp_path, source_root):
    from wechat_bridge.core.config import create_config, load_config
    cfg = create_config(tmp_path / "state", source_root)
    assert load_config(cfg.state_dir) == cfg
    assert cfg.state_dir.stat().st_mode & 0o777 == 0o700
    assert (cfg.state_dir / "config.json").stat().st_mode & 0o777 == 0o600
    assert str(source_root) not in repr(cfg)


def test_state_inside_repo_rejected(tmp_path, source_root):
    from wechat_bridge.core.config import create_config
    from wechat_bridge.core.errors import BridgeError
    with pytest.raises(BridgeError, match="state_inside_repository"):
        create_config(tmp_path / "repo" / "state", source_root, repo_root=tmp_path / "repo")


def test_state_symlink_rejected(tmp_path, source_root):
    from wechat_bridge.core.config import create_config
    from wechat_bridge.core.errors import BridgeError
    target = tmp_path / "target"; target.mkdir()
    link = tmp_path / "link"; link.symlink_to(target, target_is_directory=True)
    with pytest.raises(BridgeError, match="unsafe_state_path"):
        create_config(link / "state", source_root)


def test_permissive_private_file_rejected(tmp_path, source_root):
    from wechat_bridge.core.config import create_config, load_config
    from wechat_bridge.core.errors import BridgeError
    cfg = create_config(tmp_path / "state", source_root)
    (cfg.state_dir / "config.json").chmod(0o644)
    with pytest.raises(BridgeError, match="private_permissions"):
        load_config(cfg.state_dir)


def test_multiple_accounts_require_selection(tmp_path, source_root):
    from wechat_bridge.core.config import select_account
    from wechat_bridge.core.errors import BridgeError
    other = source_root.parent / "other" / "db_storage"; other.mkdir(parents=True)
    with pytest.raises(BridgeError, match="account_selection_required"):
        select_account(source_root.parent)
    assert select_account(source_root.parent, source_root.name) == source_root


def test_error_output_contains_no_secret():
    from wechat_bridge.core.errors import BridgeError
    err = BridgeError("source_unreadable")
    assert str(err) == "source_unreadable"


def test_default_repo_root_is_the_checkout_after_core_move(tmp_path, source_root, monkeypatch):
    # create_config derives the repo root from core/config.py's own location; a wrong
    # parents[] depth after the core/ move would silently let state live inside the repo.
    import wechat_bridge
    from pathlib import Path
    from wechat_bridge.core.config import create_config
    from wechat_bridge.core.errors import BridgeError
    root = Path(wechat_bridge.__file__).resolve().parents[2]
    assert (root / "pyproject.toml").is_file()
    leaked = root / "state-never-created"
    try:
        with pytest.raises(BridgeError, match="state_inside_repository"):
            create_config(leaked, source_root)
        assert not leaked.exists()
    finally:  # a regression would have created private state inside the checkout
        if leaked.exists():
            import shutil
            shutil.rmtree(leaked)


def test_symlinked_state_pointing_into_repo_is_rejected_before_creation(tmp_path, source_root):
    from wechat_bridge.core.config import create_config
    from wechat_bridge.core.errors import BridgeError
    with pytest.raises(BridgeError, match="state_inside_repository"):
        create_config(tmp_path / "r" / ".." / "r" / "s", source_root, repo_root=tmp_path / "r")


@pytest.mark.parametrize("patch", [
    {"version": 2}, {"account_root": "relative/path"}, {"sqlcipher_library": "lib.dylib"},
    {"account_root": 7},
])
def test_load_config_rejects_malformed_state(tmp_path, source_root, patch):
    from wechat_bridge.core.config import create_config, load_config, read_private_json, write_private_json
    from wechat_bridge.core.errors import BridgeError
    cfg = create_config(tmp_path / "state", source_root)
    path = cfg.state_dir / "config.json"
    write_private_json(path, read_private_json(path) | patch)
    with pytest.raises(BridgeError, match="invalid_private_state"):
        load_config(cfg.state_dir)


def test_load_config_rejects_boolean_version(tmp_path, source_root):
    from wechat_bridge.core.config import create_config, load_config, read_private_json, write_private_json
    from wechat_bridge.core.errors import BridgeError
    cfg = create_config(tmp_path / "state", source_root)
    path = cfg.state_dir / "config.json"
    write_private_json(path, read_private_json(path) | {"version": True})
    with pytest.raises(BridgeError, match="invalid_private_state"):
        load_config(cfg.state_dir)


def test_load_config_rejects_missing_keys_and_symlinked_account(tmp_path, source_root):
    from wechat_bridge.core.config import create_config, load_config, write_private_json
    from wechat_bridge.core.errors import BridgeError
    cfg = create_config(tmp_path / "state", source_root)
    write_private_json(cfg.state_dir / "config.json", {"version": 1})
    with pytest.raises(BridgeError, match="invalid_private_state"):
        load_config(cfg.state_dir)
    link = tmp_path / "linked-account"; link.symlink_to(source_root, target_is_directory=True)
    write_private_json(cfg.state_dir / "config.json", {"version": 1, "account_root": str(link),
                                                      "sqlcipher_library": "/lib.dylib"})
    with pytest.raises(BridgeError, match="source_symlink"):
        load_config(cfg.state_dir)


def test_create_config_with_different_account_conflicts_instead_of_overwriting(tmp_path, source_root):
    from wechat_bridge.core.config import create_config
    from wechat_bridge.core.errors import BridgeError
    cfg = create_config(tmp_path / "state", source_root)
    other = tmp_path / "other"; (other / "db_storage").mkdir(parents=True)
    with pytest.raises(BridgeError, match="account_selection_conflict"):
        create_config(cfg.state_dir, other)
    assert create_config(cfg.state_dir, source_root) == cfg


def test_create_config_requires_db_storage(tmp_path):
    from wechat_bridge.core.config import create_config
    from wechat_bridge.core.errors import BridgeError
    bare = tmp_path / "bare"; bare.mkdir()
    with pytest.raises(BridgeError, match="source_not_found"):
        create_config(tmp_path / "state", bare)
    assert not (tmp_path / "state" / "config.json").exists()


def test_permissive_state_directory_is_rejected(tmp_path):
    from wechat_bridge.core.config import ensure_private_dir
    from wechat_bridge.core.errors import BridgeError
    state = tmp_path / "state"; state.mkdir(mode=0o755); state.chmod(0o755)
    with pytest.raises(BridgeError, match="private_permissions"):
        ensure_private_dir(state)


def test_private_json_rejects_symlink_nonobject_oversize_and_garbage(tmp_path):
    from wechat_bridge.core.config import ensure_private_dir, read_private_json
    from wechat_bridge.core.errors import BridgeError
    ensure_private_dir(tmp_path / "s")
    d = tmp_path / "s"

    def put(name, data):
        p = d / name; p.write_bytes(data); p.chmod(0o600); return p
    with pytest.raises(BridgeError, match="invalid_private_state"):
        read_private_json(put("list.json", b"[1]"))
    with pytest.raises(BridgeError, match="invalid_private_state"):
        read_private_json(put("bad.json", b"{not json"))
    with pytest.raises(BridgeError, match="invalid_private_state"):
        read_private_json(put("utf.json", b"\xff\xfe{"))
    with pytest.raises(BridgeError, match="state_too_large"):
        read_private_json(put("big.json", b'{"a":"' + b"x" * (8 * 1024 * 1024) + b'"}'))
    real = put("real.json", b"{}")
    (d / "link.json").symlink_to(real)
    with pytest.raises(BridgeError, match="unsafe_state_path"):
        read_private_json(d / "link.json")
    with pytest.raises(BridgeError, match="invalid_private_state"):
        read_private_json(d / "missing.json")


def test_write_private_json_is_atomic_owner_only_and_leaves_no_temp_on_failure(tmp_path, monkeypatch):
    import os
    from wechat_bridge.core import config as module
    from wechat_bridge.core.errors import BridgeError
    path = tmp_path / "s" / "value.json"
    module.write_private_json(path, {"a": 1})
    assert path.stat().st_mode & 0o777 == 0o600
    assert module.read_private_json(path) == {"a": 1}

    def boom(*_):
        raise OSError("disk full")
    monkeypatch.setattr(module.os, "replace", boom)
    with pytest.raises(BridgeError, match="state_write_failed") as info:
        module.write_private_json(path, {"a": 2})
    assert info.value.__cause__ is None and "disk full" not in str(info.value)
    monkeypatch.undo()
    assert module.read_private_json(path) == {"a": 1}
    assert sorted(p.name for p in path.parent.iterdir()) == ["value.json"]


def test_private_fd_check_rejects_group_readable_and_non_regular(tmp_path):
    import os
    from wechat_bridge.core.config import check_private_fd
    from wechat_bridge.core.errors import BridgeError
    f = tmp_path / "f"; f.write_bytes(b""); f.chmod(0o640)
    fd = os.open(f, os.O_RDONLY)
    try:
        with pytest.raises(BridgeError, match="private_permissions"):
            check_private_fd(fd)
    finally:
        os.close(fd)
    fd = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(BridgeError, match="private_permissions"):
            check_private_fd(fd)
    finally:
        os.close(fd)


def test_state_lock_serializes_holders(tmp_path, source_root):
    import fcntl, os
    from wechat_bridge.core.config import create_config, state_lock
    cfg = create_config(tmp_path / "state", source_root)
    with state_lock(cfg):
        fd = os.open(cfg.state_dir / ".lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
    fd = os.open(cfg.state_dir / ".lock", os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


def test_select_account_reports_missing_and_unreadable_sources(tmp_path):
    from wechat_bridge.core.config import select_account
    from wechat_bridge.core.errors import BridgeError
    (tmp_path / "empty").mkdir()
    with pytest.raises(BridgeError, match="source_not_found"):
        select_account(tmp_path / "empty")
    with pytest.raises(BridgeError, match="source_unreadable"):
        select_account(tmp_path / "absent")
