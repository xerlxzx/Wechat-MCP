import json
import os
import pytest
from wechat_bridge.core.config import write_private_json


def manifest_data():
    return {"version": 1, "entries": [{"account_id": "account-a", "relative_path": "message/message_0.db",
        "database_id": "db-a", "salt_hex": "12" * 16, "raw_key_hex": "ab" * 32,
        "profile": "sqlcipher4", "verified_at": None}]}


def test_manifest_private_and_redacted(tmp_path):
    from wechat_bridge.source.keys import load_manifest
    path = tmp_path / "keys.json"; tmp_path.chmod(0o700)
    write_private_json(path, manifest_data())
    manifest = load_manifest(path)
    assert manifest.entries[0].raw_key == bytes.fromhex("ab" * 32)
    assert "ab" * 32 not in repr(manifest)
    assert "message/message_0.db" not in repr(manifest)


@pytest.mark.parametrize("change", ["duplicate", "absolute", "traversal", "key", "salt", "profile", "version", "missing"])
def test_invalid_manifest_rejected(tmp_path, change):
    from wechat_bridge.source.keys import load_manifest
    from wechat_bridge.core.errors import BridgeError
    data = manifest_data(); entry = data["entries"][0]
    if change == "duplicate": data["entries"].append(dict(entry))
    elif change == "absolute": entry["relative_path"] = "/outside.db"
    elif change == "traversal": entry["relative_path"] = "../outside.db"
    elif change == "key": entry["raw_key_hex"] = "bad"
    elif change == "salt": entry["salt_hex"] = "bad"
    elif change == "profile": entry["profile"] = "guess"
    elif change == "version": data["version"] = 2
    elif change == "missing": del entry["database_id"]
    path = tmp_path / "keys.json"; tmp_path.chmod(0o700); write_private_json(path, data)
    with pytest.raises(BridgeError, match="invalid_key_manifest"): load_manifest(path)


def test_key_permissions_rejected(tmp_path):
    from wechat_bridge.source.keys import load_manifest
    from wechat_bridge.core.errors import BridgeError
    path = tmp_path / "keys.json"; path.write_text(json.dumps(manifest_data())); path.chmod(0o644)
    with pytest.raises(BridgeError, match="private_permissions"): load_manifest(path)
