import pytest
from tests.support.schemas import account_case
from tests.support.encrypted import Engine, KEY
from wechat_bridge.core.errors import BridgeError


def test_untested_build_is_explicitly_unsupported(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path)
    with pytest.raises(BridgeError, match="unsupported_build"):
        open_adapter(cfg, build="270103")


@pytest.mark.parametrize("build", ["270102", "270135"])
def test_schema_identical_builds_are_supported(tmp_path, build):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    assert open_adapter(account_case(tmp_path), build=build).build == build


@pytest.mark.parametrize("build", ["270101", "270134", "270136", "", "270102 "])
def test_neighbouring_builds_stay_unsupported(tmp_path, build):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    with pytest.raises(BridgeError, match="unsupported_build"):
        open_adapter(account_case(tmp_path), build=build)


def test_required_mapping_missing_is_not_empty_success(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path, changes={"contact/contact.db": "ALTER TABLE contact RENAME COLUMN username TO mystery"})
    with pytest.raises(BridgeError, match="unsupported_schema"):
        open_adapter(cfg, build="270102")


def test_unknown_column_and_missing_membership_are_partial(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path, changes={"contact/contact.db": "ALTER TABLE contact ADD mystery BLOB; DROP TABLE chatroom_member"})
    adapter = open_adapter(cfg, build="270102")
    assert adapter.parse_status == "partial"
    group = next(c for c in adapter.conversations() if c.kind == "group")
    assert group.membership_status == "unsupported"
    with pytest.raises(BridgeError, match="membership_unsupported"):
        tuple(adapter.memberships(group.conversation_id))


def test_schema_pagination_and_fingerprint_ignore_private_table_hashes(tmp_path):
    from wechat_bridge.adapters.schema import inspect_schema
    from wechat_bridge.source.discovery import discover
    from wechat_bridge.source.reader import open_source
    cfg = account_case(tmp_path)
    db = next(d for d in discover(cfg).databases if d.relative_path == "contact/contact.db")
    with Engine(cfg.account_root / "db_storage/contact/contact.db") as w:
        for n in range(205):
            w.run(f"CREATE TABLE auxiliary_{n}(a TEXT)")
    with open_source(db, KEY, config=cfg) as r:
        schema = inspect_schema(r)
    assert len(schema.tables) == 208
    assert len(schema.fingerprint) == 64


def test_new_or_replaced_shard_requires_its_own_verified_key(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path)
    path = cfg.account_root / "db_storage/message/message_1.db"
    path.unlink()
    with Engine(path, b"b" * 32) as w:
        w.run("CREATE TABLE Name2Id(user_name TEXT)")
    with pytest.raises(BridgeError, match="key_coverage_incomplete"):
        open_adapter(cfg, build="270102")


@pytest.mark.parametrize('relative,sql',[
    ('contact/contact.db',"ALTER TABLE contact RENAME TO old_contact; CREATE TABLE contact(id INTEGER,username TEXT,nick_name TEXT,remark TEXT); INSERT INTO contact VALUES(2,'fictional-alice','Alice',NULL),(1,'fictional-bob','Bob',NULL),(3,'fictional-room@chatroom','Room',NULL)"),
    ('contact/contact.db',"ALTER TABLE contact RENAME TO old_contact; CREATE TABLE contact(id INTEGER PRIMARY KEY DESC,username TEXT,nick_name TEXT,remark TEXT); INSERT INTO contact VALUES(2,'fictional-alice','Alice',NULL),(1,'fictional-bob','Bob',NULL)"),
    ('message/message_0.db',None)])
def test_lost_identity_constraints_are_rejected_before_records(tmp_path,relative,sql):
    from tests.support.schemas import TABLE
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    if sql is None:
        sql=f"ALTER TABLE {TABLE} RENAME TO OldMessages; CREATE TABLE {TABLE}(local_id INTEGER,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content TEXT,compress_content TEXT,packed_info_data BLOB,WCDB_CT_message_content INTEGER); INSERT INTO {TABLE}(local_id) VALUES(1),(1)"
    cfg=account_case(tmp_path,changes={relative:sql})
    with pytest.raises(BridgeError,match='unsupported_schema'):
        open_adapter(cfg,build='270102')


@pytest.mark.parametrize('relative,table,column',[
    ('hardlink/hardlink.db','file_hardlink_info_v4','type'),
    ('message/message_resource.db','MessageResourceDetail','status')])
def test_consumed_attachment_columns_are_mandatory(tmp_path,relative,table,column):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg=account_case(tmp_path,changes={relative:f'ALTER TABLE {table} DROP COLUMN {column}'})
    with pytest.raises(BridgeError,match='unsupported_schema'):
        open_adapter(cfg,build='270102')


@pytest.mark.parametrize('relative,table',[
    ('contact/contact.db','chat_room'),('contact/contact.db','chatroom_member'),
    ('message/message_0.db',None),('message/message_resource.db','MessageResourceInfo'),
    ('hardlink/hardlink.db','file_hardlink_info_v4')])
def test_unknown_interpreted_columns_are_partial(tmp_path,relative,table):
    from tests.support.schemas import TABLE
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg=account_case(tmp_path,changes={relative:f'ALTER TABLE {table or TABLE} ADD mystery BLOB'})
    assert open_adapter(cfg,build='270102').parse_status=='partial'


def test_mismatched_verified_entry_cannot_shadow_another_databases_key(tmp_path):
    from dataclasses import replace
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    from wechat_bridge.source.discovery import discover
    from wechat_bridge.source.keys import KeyEntry, KeyManifest, load_manifest
    from wechat_bridge.core.config import write_private_json
    cfg = account_case(tmp_path)
    manifest = load_manifest(cfg.state_dir / "keys.json")
    contact = next(e for e in manifest.entries if e.relative_path == "contact/contact.db")
    extra = cfg.account_root / "db_storage/zz_extra.db"
    with Engine(extra, b"z" * 32) as w:
        w.run("CREATE TABLE t(a TEXT)")
    db = next(d for d in discover(cfg).databases if d.relative_path == "zz_extra.db")
    shadow = KeyEntry(db.account_id, db.relative_path, contact.database_id, db.salt_hex, b"z" * 32)
    write_private_json(cfg.state_dir / "keys.json", KeyManifest(manifest.entries + (shadow,)).private_dict())
    adapter = open_adapter(cfg, build="270102")
    assert adapter._keys[contact.database_id].relative_path == "contact/contact.db"


@pytest.mark.parametrize("build,ok", [("270135", True), ("270136", False)])
def test_installed_build_gates_live_adapter(tmp_path, monkeypatch, build, ok):
    from tests.support.proof import installed_build
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path)
    installed_build(monkeypatch, tmp_path, build)
    if ok:
        assert open_adapter(cfg).build == build
    else:
        with pytest.raises(BridgeError, match="unsupported_build"):
            open_adapter(cfg)
