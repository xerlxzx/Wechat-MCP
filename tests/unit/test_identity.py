import pytest
from wechat_bridge.core.errors import BridgeError


def test_id_encoding_has_no_component_or_kind_collisions():
    from wechat_bridge.adapters.identity import stable_id
    key = bytes(range(32))
    assert stable_id("contact", ("ab", "c"), key) != stable_id("contact", ("a", "bc"), key)
    assert stable_id("contact", ("a",), key) != stable_id("conversation", ("a",), key)
    assert stable_id("message", ("acct", "db1", "table", "1"), key) != stable_id("message", ("acct", "db2", "table", "1"), key)


def test_identity_secret_persists_privately(tmp_path):
    from wechat_bridge.adapters.identity import load_identity_key, stable_id
    from tests.support.schemas import account_case
    cfg = account_case(tmp_path)
    first = load_identity_key(cfg)
    assert len(first) == 32
    assert stable_id("contact", ("a", "b"), first) == stable_id("contact", ("a", "b"), load_identity_key(cfg))
    assert (cfg.state_dir / "identity.json").stat().st_mode & 0o777 == 0o600


def test_sender_rows_are_scoped_to_database_and_conflicts_are_explicit():
    from wechat_bridge.adapters.identity import IdentityLookup
    lookup = IdentityLookup("account", bytes(range(32)))
    lookup.add_sender("db1", 1, "alice")
    lookup.add_sender("db2", 1, "bob")
    assert lookup.sender("db1", 1).contact_id != lookup.sender("db2", 1).contact_id
    assert lookup.sender("db1", 99).status == "missing"
    lookup.add_sender("db1", 1, "other")
    assert lookup.sender("db1", 1).status == "conflicting"
    assert lookup.sender("db1", 1).contact_id is None


@pytest.mark.parametrize("key", [b"", b"a" * 31])
def test_invalid_identity_key_is_rejected(key):
    from wechat_bridge.adapters.identity import stable_id
    with pytest.raises(BridgeError, match="invalid_identity_key"):
        stable_id("contact", ("a",), key)
