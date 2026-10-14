"""Hand-authored encrypted schemas and fictional identities; no account samples."""
import hashlib

from wechat_bridge.core.config import create_config
from wechat_bridge.source.discovery import discover
from wechat_bridge.source.keys import KeyEntry, KeyManifest, publish_verified_manifest, verify_keys
from tests.support.encrypted import Engine, KEY, LIBRARY

CHAT = "fictional-room@chatroom"
TABLE = "Msg_" + hashlib.md5(CHAT.encode()).hexdigest()

SCHEMAS = {
    "contact/contact.db": """
CREATE TABLE contact(id INTEGER PRIMARY KEY, username TEXT, nick_name TEXT, remark TEXT);
CREATE TABLE chat_room(id INTEGER PRIMARY KEY, username TEXT, owner TEXT, ext_buffer BLOB);
CREATE TABLE chatroom_member(room_id INTEGER, member_id INTEGER);
INSERT INTO contact VALUES(1,'fictional-alice','Same name','');
INSERT INTO contact VALUES(2,'fictional-bob','Same name',NULL);
INSERT INTO contact VALUES(3,'fictional-room@chatroom','Fictional group','');
INSERT INTO chat_room VALUES(10,'fictional-room@chatroom','fictional-alice',x'');
INSERT INTO chatroom_member VALUES(10,1),(10,2);
""",
    "session/session.db": """
CREATE TABLE SessionTable(username TEXT PRIMARY KEY, type INTEGER);
INSERT INTO SessionTable VALUES('fictional-room@chatroom',1),('fictional-alice',0);
""",
    "message/message_0.db": f"""
CREATE TABLE Name2Id(user_name TEXT, is_session INTEGER);
INSERT INTO Name2Id(rowid,user_name,is_session) VALUES(1,'fictional-alice',0),(2,'fictional-bob',0),(3,'fictional-room@chatroom',1);
CREATE TABLE {TABLE}(local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER, sort_seq INTEGER,
real_sender_id INTEGER, create_time INTEGER, message_content TEXT, compress_content TEXT,
packed_info_data BLOB, WCDB_CT_message_content INTEGER);
""",
    "message/message_1.db": f"""
CREATE TABLE Name2Id(user_name TEXT, is_session INTEGER);
INSERT INTO Name2Id(rowid,user_name,is_session) VALUES(1,'fictional-bob',0),(2,'fictional-alice',0),(3,'fictional-room@chatroom',1);
CREATE TABLE {TABLE}(local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER, sort_seq INTEGER,
real_sender_id INTEGER, create_time INTEGER, message_content TEXT, compress_content TEXT,
packed_info_data BLOB, WCDB_CT_message_content INTEGER);
""",
    "message/message_resource.db": """
CREATE TABLE ChatName2Id(user_name TEXT, update_time INTEGER);
CREATE TABLE SenderName2Id(user_name TEXT, update_time INTEGER);
CREATE TABLE MessageResourceInfo(message_id INTEGER,chat_id INTEGER,sender_id INTEGER,message_local_type INTEGER,
message_create_time INTEGER,message_local_id INTEGER,message_svr_id INTEGER,message_origin_source INTEGER,packed_info BLOB);
CREATE TABLE MessageResourceDetail(resource_id INTEGER,message_id INTEGER,type INTEGER,size INTEGER,
create_time INTEGER,access_time INTEGER,status INTEGER,data_index TEXT,packed_info BLOB);
""",
    "hardlink/hardlink.db": """
CREATE TABLE dir2id(username TEXT);
CREATE TABLE file_hardlink_info_v4(md5_hash INTEGER,md5 TEXT,type INTEGER,file_name TEXT,file_size INTEGER,
modify_time INTEGER,dir1 INTEGER,dir2 INTEGER,_rowid_ INTEGER,extra_buffer BLOB);
CREATE TABLE image_hardlink_info_v4(md5_hash INTEGER,md5 TEXT,type INTEGER,file_name TEXT,file_size INTEGER,
modify_time INTEGER,dir1 INTEGER,dir2 INTEGER,_rowid_ INTEGER,extra_buffer BLOB);
""",
}


def account_case(tmp_path, *, changes=None):
    root = (tmp_path / "synthetic-account").resolve()
    for relative, sql in SCHEMAS.items():
        path = root / "db_storage" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with Engine(path) as writer:
            writer.run(sql)
            if changes and relative in changes:
                writer.run(changes[relative])
    (root / "msg/file").mkdir(parents=True)
    (root / "msg/attach").mkdir(parents=True)
    cfg = create_config((tmp_path / "state").resolve(), root, sqlcipher_library=LIBRARY)
    inv = discover(cfg)
    manifest = KeyManifest(tuple(KeyEntry(d.account_id, d.relative_path, d.database_id, d.salt_hex, KEY)
                                 for d in inv.databases))
    coverage = verify_keys(inv, manifest, cfg)
    assert coverage.ready
    publish_verified_manifest(manifest, coverage, cfg)
    return cfg
