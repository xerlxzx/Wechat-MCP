from dataclasses import replace
import os
import pytest
from tests.support.encrypted import create_database, KEY
from wechat_bridge.core.config import create_config
from wechat_bridge.source.discovery import discover


def case(tmp_path):
    root = tmp_path / "account"
    paths = ("contact/contact.db", "session/session.db", "message/message_resource.db",
             "hardlink/hardlink.db", "message/message_0.db")
    for i, relative in enumerate(paths):
        create_database(root / "db_storage" / relative, wal=False, key=bytes([i + 1]) * 32).close()
    cfg = create_config(tmp_path / "state", root)
    inventory = discover(cfg)
    from wechat_bridge.source.keys import KeyEntry, KeyManifest
    keys = {path: bytes([i + 1]) * 32 for i, path in enumerate(paths)}
    manifest = KeyManifest(tuple(KeyEntry(d.account_id, d.relative_path, d.database_id,
        d.salt_hex, keys[d.relative_path]) for d in inventory.databases))
    return cfg, inventory, manifest


def test_distinct_keys_verify_each_required_database(tmp_path):
    from wechat_bridge.source.keys import verify_keys, publish_verified_manifest, load_manifest
    cfg, inventory, manifest = case(tmp_path)
    coverage = verify_keys(inventory, manifest, cfg)
    assert coverage.ready
    assert coverage.statuses() == ("verified",) * 5
    assert coverage.public_dict()["verification_scope"] == "schema_read"
    assert not coverage.public_dict()["full_page_integrity_checked"]
    publish_verified_manifest(manifest, coverage, cfg)
    saved = load_manifest(cfg.state_dir / "keys.json")
    assert len(saved.entries) == 5
    assert all(e.verified_at for e in saved.entries)


def test_wrong_key_preserves_existing_good_manifest(tmp_path):
    from wechat_bridge.source.keys import verify_keys, publish_verified_manifest, KeyManifest
    cfg, inventory, good = case(tmp_path)
    publish_verified_manifest(good, verify_keys(inventory, good, cfg), cfg)
    before = (cfg.state_dir / "keys.json").read_bytes()
    bad = KeyManifest(tuple(replace(e, raw_key=b"z" * 32) for e in good.entries))
    report = verify_keys(inventory, bad, cfg)
    publish_verified_manifest(bad, report, cfg)
    assert not report.ready
    assert set(report.statuses()) == {"key_or_format_mismatch"}
    assert (cfg.state_dir / "keys.json").read_bytes() == before
    assert (b"z" * 32).hex() not in repr(report)


def test_other_account_cannot_supply_key_by_filename(tmp_path):
    from wechat_bridge.source.keys import verify_keys, KeyManifest
    cfg, inventory, manifest = case(tmp_path)
    wrong_account = KeyManifest(tuple(replace(e, account_id="other") for e in manifest.entries))
    report = verify_keys(inventory, wrong_account, cfg)
    assert set(report.statuses()) == {"missing_key"}
    assert not report.ready


def test_new_shard_and_replaced_salt_remain_incomplete(tmp_path):
    from wechat_bridge.source.keys import verify_keys
    cfg, inventory, manifest = case(tmp_path)
    create_database(cfg.account_root / "db_storage/message/message_1.db", wal=False).close()
    target = cfg.account_root / "db_storage/contact/contact.db"
    new = target.with_suffix(".new"); create_database(new, wal=False).close(); new.replace(target)
    report = verify_keys(discover(cfg), manifest, cfg)
    assert "missing_key" in report.statuses()
    assert "replaced" in report.statuses()
    assert not report.ready


def test_failed_atomic_publication_preserves_old_manifest(tmp_path, monkeypatch):
    from wechat_bridge.source.keys import verify_keys, publish_verified_manifest
    from wechat_bridge.core.errors import BridgeError
    cfg, inventory, manifest = case(tmp_path)
    coverage = verify_keys(inventory, manifest, cfg)
    publish_verified_manifest(manifest, coverage, cfg)
    before = (cfg.state_dir / "keys.json").read_bytes()
    def fail(*args, **kwargs): raise OSError("injected private error")
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(BridgeError, match="state_write_failed"):
        publish_verified_manifest(manifest, coverage, cfg)
    assert (cfg.state_dir / "keys.json").read_bytes() == before
    assert not list(cfg.state_dir.glob(".pending-*"))


def test_schema_corruption_is_not_definitely_a_wrong_key(tmp_path, monkeypatch):
    from wechat_bridge.source import keys
    from wechat_bridge.core.errors import BridgeError
    cfg, inventory, manifest = case(tmp_path)
    def corrupt(*args, **kwargs): raise BridgeError("source_corrupt")
    monkeypatch.setattr(keys, "open_source", corrupt)
    assert set(keys.verify_keys(inventory, manifest, cfg).statuses()) == {"unreadable"}


def rotated_registry(cfg, relative):
    from wechat_bridge.core.config import read_private_json, write_private_json
    path = cfg.state_dir / "inventory.json"
    registry = read_private_json(path)
    registry["databases"][relative] |= {"database_id": "00000000-0000-4000-8000-000000000000", "replaced": True}
    write_private_json(path, registry)


def test_rewritten_database_with_same_salt_verifies_under_original_id(tmp_path):
    from wechat_bridge.source.keys import verify_keys
    cfg, inventory, manifest = case(tmp_path)
    target = cfg.account_root / "db_storage/contact/contact.db"
    copy = target.with_suffix(".new"); copy.write_bytes(target.read_bytes()); copy.replace(target)
    current = discover(cfg)
    report = verify_keys(current, manifest, cfg)
    assert report.ready
    assert [d.database_id for d in current.databases] == [d.database_id for d in inventory.databases]


def test_verified_key_restores_rotated_incarnation_id(tmp_path):
    from wechat_bridge.core.config import read_private_json
    from wechat_bridge.source.keys import verify_keys, publish_verified_manifest
    cfg, inventory, manifest = case(tmp_path)
    rotated_registry(cfg, "contact/contact.db")
    report = verify_keys(discover(cfg), manifest, cfg)
    assert not report.ready
    publish_verified_manifest(manifest, report, cfg)
    row = read_private_json(cfg.state_dir / "inventory.json")["databases"]["contact/contact.db"]
    original = next(d.database_id for d in inventory.databases if d.relative_path == "contact/contact.db")
    assert row["database_id"] == original and not row["replaced"]
    assert verify_keys(discover(cfg), manifest, cfg).ready


def test_wrong_key_never_restores_rotated_incarnation_id(tmp_path):
    from wechat_bridge.core.config import read_private_json
    from wechat_bridge.source.keys import KeyManifest, verify_keys, publish_verified_manifest
    cfg, inventory, manifest = case(tmp_path)
    rotated_registry(cfg, "contact/contact.db")
    wrong = KeyManifest(tuple(replace(e, raw_key=b"\x09" * 32) if e.relative_path == "contact/contact.db" else e
                              for e in manifest.entries))
    report = verify_keys(discover(cfg), wrong, cfg)
    publish_verified_manifest(wrong, report, cfg)
    row = read_private_json(cfg.state_dir / "inventory.json")["databases"]["contact/contact.db"]
    assert row["database_id"] == "00000000-0000-4000-8000-000000000000" and row["replaced"]
    assert not verify_keys(discover(cfg), wrong, cfg).ready


def test_restored_id_never_collides_or_rebinds_an_unrotated_row(tmp_path):
    from wechat_bridge.core.config import read_private_json
    from wechat_bridge.source.keys import KeyManifest, load_manifest, verify_keys, publish_verified_manifest
    cfg, inventory, manifest = case(tmp_path)
    publish_verified_manifest(manifest, verify_keys(inventory, manifest, cfg), cfg)
    rotated_registry(cfg, "contact/contact.db")
    session = next(d.database_id for d in inventory.databases if d.relative_path == "session/session.db")
    foreign = KeyManifest(tuple(replace(e, database_id=session if e.relative_path == "contact/contact.db"
                                        else "11111111-1111-4111-8111-111111111111" if e.relative_path == "hardlink/hardlink.db"
                                        else e.database_id) for e in manifest.entries))
    publish_verified_manifest(foreign, verify_keys(discover(cfg), foreign, cfg), cfg)
    rows = read_private_json(cfg.state_dir / "inventory.json")["databases"]
    assert rows["contact/contact.db"]["database_id"] == "00000000-0000-4000-8000-000000000000"
    hardlink = next(d.database_id for d in inventory.databases if d.relative_path == "hardlink/hardlink.db")
    assert rows["hardlink/hardlink.db"]["database_id"] == hardlink
    saved = {e.relative_path: e.database_id for e in load_manifest(cfg.state_dir / "keys.json").entries}
    assert saved == {e.relative_path: e.database_id for e in manifest.entries}
