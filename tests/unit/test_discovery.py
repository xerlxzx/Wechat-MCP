import os
import pytest


def config(tmp_path, source_root):
    from wechat_bridge.core.config import create_config
    return create_config(tmp_path / "state", source_root)


def test_inventory_private_stable_and_required(tmp_path, source_root):
    from wechat_bridge.source.discovery import discover
    cfg = config(tmp_path, source_root)
    a, b = discover(cfg), discover(cfg)
    assert len(a.databases) == 6
    assert all(d.required for d in a.databases)
    assert a.account_id == b.account_id
    assert [d.database_id for d in a.databases] == [d.database_id for d in b.databases]
    assert source_root.name not in repr(a)
    assert str(source_root) not in str(a.public_dict())
    assert a.public_dict()["required_count"] == 6


def test_new_shard_and_wal_discovered(tmp_path, source_root):
    from wechat_bridge.source.discovery import discover
    cfg = config(tmp_path, source_root); discover(cfg)
    path = source_root / "db_storage/message/message_2.db"
    path.write_bytes(b"2" * 32); path.with_name(path.name + "-wal").write_bytes(b"wal")
    inventory = discover(cfg)
    assert len(inventory.databases) == 7
    assert next(d for d in inventory.databases if d.relative_path.endswith("message_2.db")).wal_present


def test_same_path_replacement_changes_incarnation_and_stays_pending(tmp_path, source_root):
    from wechat_bridge.source.discovery import discover
    cfg = config(tmp_path, source_root); before = discover(cfg)
    target = source_root / "db_storage/contact/contact.db"
    replacement = target.with_suffix(".new"); replacement.write_bytes(b"x" * 32)
    replacement.replace(target)
    after, again = discover(cfg), discover(cfg)
    old = next(d for d in before.databases if d.relative_path == "contact/contact.db")
    new = next(d for d in after.databases if d.relative_path == "contact/contact.db")
    assert new.database_id != old.database_id
    assert new.replaced
    assert next(d for d in again.databases if d.relative_path == new.relative_path).replaced


def test_escaping_database_symlink_rejected(tmp_path, source_root):
    from wechat_bridge.source.discovery import discover
    from wechat_bridge.core.errors import BridgeError
    cfg = config(tmp_path, source_root)
    outside = tmp_path / "outside.db"; outside.write_bytes(b"x" * 32)
    (source_root / "db_storage/escape.db").symlink_to(outside)
    with pytest.raises(BridgeError, match="source_symlink"):
        discover(cfg)


def test_missing_required_is_not_empty_success(tmp_path, source_root):
    from wechat_bridge.source.discovery import discover
    cfg = config(tmp_path, source_root)
    (source_root / "db_storage/contact/contact.db").unlink()
    result = discover(cfg)
    assert "required_source_missing" in result.issues


def test_auxiliary_storage_is_reported(tmp_path, source_root):
    from wechat_bridge.source.discovery import discover
    cfg = config(tmp_path, source_root)
    (source_root / "db_storage/aux.kvdb-shm").write_bytes(b"x")
    assert "auxiliary_storage_unhandled" in discover(cfg).issues


def test_permission_denial_is_diagnostic(tmp_path, source_root, monkeypatch):
    from pathlib import Path
    from wechat_bridge.source.discovery import discover
    from wechat_bridge.core.errors import BridgeError
    cfg = config(tmp_path, source_root)
    original = Path.open
    def denied(path, *args, **kwargs):
        if path.suffix == ".db":
            raise PermissionError("private path must not leak")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(BridgeError, match="source_unreadable"):
        discover(cfg)


def test_rewritten_file_with_same_salt_keeps_incarnation_pending_reverification(tmp_path, source_root):
    from wechat_bridge.source.discovery import discover
    cfg = config(tmp_path, source_root); before = discover(cfg)
    target = source_root / "db_storage/contact/contact.db"
    copy = target.with_suffix(".new"); copy.write_bytes(target.read_bytes())
    copy.replace(target)
    after, again = discover(cfg), discover(cfg)
    old = next(d for d in before.databases if d.relative_path == "contact/contact.db")
    new = next(d for d in after.databases if d.relative_path == "contact/contact.db")
    assert new.database_id == old.database_id
    assert new.file_identity != old.file_identity
    assert new.replaced
    assert next(d for d in again.databases if d.relative_path == new.relative_path).database_id == old.database_id


def test_oversized_source_tree_is_rejected(tmp_path, source_root):
    from wechat_bridge.core.errors import BridgeError
    from wechat_bridge.source.discovery import discover
    cfg = config(tmp_path, source_root)
    extra = cfg.account_root / "db_storage" / "extra"
    extra.mkdir()
    for index in range(4100):
        (extra / f"n{index}.txt").write_bytes(b"")
    with pytest.raises(BridgeError, match="source_inventory_limit"):
        discover(cfg)
