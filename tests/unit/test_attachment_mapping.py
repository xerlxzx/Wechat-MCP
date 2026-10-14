from tests.support.schemas import account_case, CHAT, TABLE
from tests.support.encrypted import Engine
import pytest


@pytest.mark.parametrize('extra_type', [None, 1, 2])
def test_exact_file_mapping_uses_only_supported_hardlinks(tmp_path, extra_type):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    from wechat_bridge.attachments.resolver import resolve_attachment
    import hashlib
    title=b'original.txt';local=b'local-copy.txt'
    nested=b'\x0a'+bytes([len(title)])+title+b'\x12'+bytes([len(local)])+local
    packed=b'\x0a'+bytes([len(nested)])+nested
    xml='<msg><appmsg><title>original.txt</title><type>6</type><appattach><totallen>4</totallen><md5>'+hashlib.md5(b'test').hexdigest()+'</md5></appattach></appmsg></msg>'
    cfg=account_case(tmp_path,changes={
      'message/message_0.db':f"INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(1,123,{(6<<32)|49},1,1700000000,'{xml}',0)",
      'message/message_resource.db':f"INSERT INTO ChatName2Id(rowid,user_name) VALUES(1,'{CHAT}'); INSERT INTO MessageResourceInfo(message_id,chat_id,message_local_id,message_svr_id,message_local_type,message_create_time) VALUES(5,1,1,123,{(6<<32)|49},1700000000); INSERT INTO MessageResourceDetail(resource_id,message_id,type,size,status,data_index,packed_info) VALUES(7,5,3342339,4,1,'index',x'{packed.hex()}')",
      'hardlink/hardlink.db':f"INSERT INTO dir2id(rowid,username) VALUES(1,'2026-01'); INSERT INTO file_hardlink_info_v4(md5,type,file_name,file_size,dir1,dir2) VALUES('{hashlib.md5(b'test').hexdigest()}',1,'local-copy.txt',4,1,0)"})
    path=cfg.account_root/'msg/file/2026-01/local-copy.txt';path.parent.mkdir();path.write_bytes(b'test')
    other=cfg.account_root/'msg/file/2026-02/local-copy.txt';other.parent.mkdir();other.write_bytes(b'wrong')
    if extra_type is not None:
        with Engine(cfg.account_root/'db_storage/hardlink/hardlink.db') as writer:
            writer.run(f"INSERT INTO dir2id(rowid,username) VALUES(2,'2026-02'); INSERT INTO file_hardlink_info_v4(md5,type,file_name,file_size,dir1,dir2) VALUES('{hashlib.md5(b'test').hexdigest()}',{extra_type},'local-copy.txt',4,2,0)")
    a=open_adapter(cfg,build='270102');chat=next(c for c in a.conversations() if c.source_username==CHAT);message=next(a.message_batches(chat.conversation_id))[0]
    refs=a.attachment_refs(message)
    assert len(refs)==1
    with resolve_attachment(refs[0],cfg) as result:
        if extra_type == 1:
            assert result.availability=='identity_mismatch'
            assert result.handle is None
        else:
            assert refs[0].original_filename=='original.txt'
            assert result.availability=='original_available'
            assert result.handle.read()==b'test'


def test_conflicting_resource_mapping_cannot_return_original(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    from wechat_bridge.attachments.models import AttachmentRef
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg=account_case(tmp_path)
    ref=AttachmentRef('attachment_conflict','message_test','file','msg/file/x',mapping_provenance='conflicting')
    with resolve_attachment(ref,cfg) as result:
        assert result.availability=='identity_mismatch'


def test_duplicate_resource_records_report_conflict_in_adapter(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    cfg=account_case(tmp_path,changes={
      'message/message_0.db':f"INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(1,123,{(6<<32)|49},1,1700000000,'<msg><appmsg><type>6</type><title>File</title></appmsg></msg>',0)",
      'message/message_resource.db':f"INSERT INTO ChatName2Id(rowid,user_name) VALUES(1,'{CHAT}'); INSERT INTO MessageResourceInfo(message_id,chat_id,message_local_id,message_svr_id,message_local_type,message_create_time) VALUES(5,1,1,123,{(6<<32)|49},1700000000),(6,1,1,123,{(6<<32)|49},1700000000); INSERT INTO MessageResourceDetail(resource_id,message_id,type,size,status) VALUES(1,5,3342339,4,1),(2,6,3342339,4,1)"})
    a=open_adapter(cfg,build='270102');chat=next(c for c in a.conversations() if c.source_username==CHAT);message=next(a.message_batches(chat.conversation_id))[0]
    refs=a.attachment_refs(message)
    assert len(refs)==1
    assert refs[0].mapping_provenance=='conflicting'
