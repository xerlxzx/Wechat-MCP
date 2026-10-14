import pytest
from wechat_bridge.core.errors import BridgeError
from tests.support.schemas import account_case, CHAT, TABLE
from tests.support.samples import QUOTE


def test_batches_merge_shards_with_numeric_ids_and_repeatable_identity(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path, changes={
        "message/message_0.db": f"INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(10,9007199254740993,1,1,1700000000,'Ten',0),(2,0,1,2,1700000000,'Two',0)",
        "message/message_1.db": f"INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(2,9007199254740994,{(57<<32)|49},1,1700000001,x'{QUOTE.hex()}',0)"})
    adapter = open_adapter(cfg, build="270102")
    conversation = next(c for c in adapter.conversations() if c.source_username == CHAT)
    batches = list(adapter.message_batches(conversation.conversation_id, batch_size=2))
    assert [len(b) for b in batches] == [2,1]
    messages = [m for b in batches for m in b]
    assert [m.source.local_id for m in messages] == ["2","10","2"]
    assert len({m.message_id for m in messages}) == 3
    assert messages[0].sender_id != messages[1].sender_id
    assert messages[2].reply.target_message_id == messages[1].message_id
    again = open_adapter(cfg, build="270102")
    assert [m.message_id for b in again.message_batches(conversation.conversation_id) for m in b] == [m.message_id for m in messages]
    assert len(adapter.observations) >= 2
    with pytest.raises(BridgeError, match="invalid_limit"):
        list(adapter.message_batches(conversation.conversation_id,batch_size=201))


def test_empty_sender_mapping_is_unknown_without_aborting_batch(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg = account_case(tmp_path, changes={"message/message_0.db": f"INSERT INTO Name2Id(rowid,user_name,is_session) VALUES(4,'',0); INSERT INTO {TABLE}(local_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(1,1,4,1700000000,'Unknown sender',0)"})
    adapter = open_adapter(cfg,build="270102")
    chat = next(c for c in adapter.conversations() if c.source_username == CHAT)
    message = next(adapter.message_batches(chat.conversation_id))[0]
    assert message.text == "Unknown sender"
    assert message.sender_id is None
    assert "sender_missing" in message.diagnostic_codes


def test_sender_mapping_removed_after_prior_read_is_unknown(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    from tests.support.encrypted import Engine
    cfg = account_case(tmp_path, changes={"message/message_0.db": f"INSERT INTO {TABLE}(local_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(1,1,1,1700000000,'Text',0)"})
    adapter = open_adapter(cfg,build="270102")
    chat = next(c for c in adapter.conversations() if c.source_username == CHAT)
    assert next(adapter.message_batches(chat.conversation_id))[0].sender_id is not None
    with Engine(cfg.account_root / "db_storage/message/message_0.db") as writer:
        writer.run("DELETE FROM Name2Id WHERE rowid=1")
    assert next(adapter.message_batches(chat.conversation_id))[0].sender_id is None


def test_null_and_malformed_timestamps_never_hide_later_rows(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg=account_case(tmp_path,changes={
      'message/message_0.db':f"INSERT INTO {TABLE}(local_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(1,1,1,NULL,'Null one',0),(2,1,1,NULL,'Null two',0),(3,1,1,'invalid','Bad text',0),(4,1,1,9223372036854775807,'Overflow',0),(5,1,1,1700000000,'Valid',0)",
      'message/message_1.db':f"INSERT INTO {TABLE}(local_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(7,1,1,1.5,'Bad real',0),(8,1,1,1700000001,'Later',0)"})
    adapter=open_adapter(cfg,build='270102');chat=next(c for c in adapter.conversations() if c.source_username==CHAT)
    messages=[m for b in adapter.message_batches(chat.conversation_id,batch_size=1) for m in b]
    assert len(messages)==7
    assert {m.source.local_id for m in messages}=={'1','2','3','4','5','7','8'}
    assert all(m.utc_timestamp is None and 'invalid_timestamp' in m.diagnostic_codes for m in messages[:5])
    assert [m.source.local_id for m in messages[5:]]==['5','8']
    assert [m.source.local_id for m in messages[:5] if m.source.database_id==adapter._dbs['message/message_0.db'].database_id]==['1','2','3','4']
