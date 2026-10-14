from tests.support.schemas import account_case, CHAT
from tests.support.encrypted import Engine


def test_duplicate_names_and_rename_preserve_source_identity(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path)
    contacts = {c.source_username: c for c in open_adapter(cfg, build="270102").contacts()}
    alice, bob = contacts["fictional-alice"], contacts["fictional-bob"]
    assert alice.display_name == bob.display_name == "Same name"
    assert alice.contact_id != bob.contact_id
    with Engine(cfg.account_root / "db_storage/contact/contact.db") as w:
        w.run("UPDATE contact SET remark='Renamed' WHERE id=1")
    renamed = next(c for c in open_adapter(cfg, build="270102").contacts() if c.source_username == "fictional-alice")
    assert renamed.contact_id == alice.contact_id
    assert renamed.display_name == "Renamed"


def test_group_membership_uses_recorded_rows_and_observation_time(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path)
    adapter = open_adapter(cfg, build="270102")
    group = next(c for c in adapter.conversations() if c.source_username == CHAT)
    members = tuple(adapter.memberships(group.conversation_id))
    assert len(members) == 2
    assert all(m.observed_at.endswith("+00:00") for m in members)
    assert all(m.role is None for m in members)
    assert all(m.group_nickname is None for m in members)
    assert {m.contact_id for m in members} == {c.contact_id for c in adapter.contacts() if c.source_username in ("fictional-alice", "fictional-bob")}


def test_dangling_member_does_not_invent_a_contact(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path, changes={"contact/contact.db": "INSERT INTO chatroom_member VALUES(10,999)"})
    adapter = open_adapter(cfg, build="270102")
    group = next(c for c in adapter.conversations() if c.source_username == CHAT)
    missing = next(m for m in adapter.memberships(group.conversation_id) if m.contact_id is None)
    assert missing.parse_status == "partial"
    assert "member_contact_missing" in missing.diagnostic_codes


def test_malformed_group_metadata_retains_membership_diagnostic(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path, changes={"contact/contact.db": "UPDATE chat_room SET ext_buffer=x'1280'"})
    adapter = open_adapter(cfg, build="270102")
    group = next(c for c in adapter.conversations() if c.source_username == CHAT)
    members = tuple(adapter.memberships(group.conversation_id))
    assert len(members) == 2
    assert all("malformed_protobuf" in m.diagnostic_codes for m in members)


def test_group_nickname_requires_recorded_member_username(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    # Top field 1; member fields 1=username, 2=card, 4=inviter.
    member = b"\x0a\x0ffictional-alice\x12\x04Card\x22\x0dfictional-bob"
    encoded = b"\x0a" + bytes([len(member)]) + member
    cfg = account_case(tmp_path, changes={"contact/contact.db": "UPDATE chat_room SET ext_buffer=x'" + encoded.hex() + "'"})
    adapter = open_adapter(cfg, build="270102")
    group = next(c for c in adapter.conversations() if c.source_username == CHAT)
    alice_id = next(c.contact_id for c in adapter.contacts() if c.source_username == "fictional-alice")
    members = {m.contact_id: m for m in adapter.memberships(group.conversation_id)}
    assert members[alice_id].group_nickname == "Card"
    assert all(m.group_nickname != "fictional-bob" for m in members.values())


def test_nonpositive_rowids_are_not_silently_skipped(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path, changes={"contact/contact.db": "INSERT INTO contact VALUES(0,'zero-contact','Zero',NULL); INSERT INTO contact VALUES(-1,'negative-contact','Negative',NULL)"})
    names = {c.source_username for c in open_adapter(cfg, build="270102").contacts()}
    assert "zero-contact" in names
    assert "negative-contact" in names
