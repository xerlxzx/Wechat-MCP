"""Complete synthetic acceptance, with independent literal expectations."""
from dataclasses import dataclass
import hashlib
from datetime import UTC,datetime
from tests.support.schemas import account_case,CHAT,TABLE
from tests.support.samples import QUOTE
from tests.support.images import png


@dataclass
class ProofCase:
    config: object
    selection: object
    image_path: object


def proof_case(tmp_path):
    from wechat_bridge.adapters.wechat_4_1 import open_adapter
    from wechat_bridge.proof import ProofSelection
    raw=png();resource='ab'*16;digest=hashlib.md5(b'test').hexdigest()
    image_info=b'\x12\x22\x0a\x20'+resource.encode()
    file_inner=b'\x0a\x08test.bin\x12\x08test.bin';file_detail=b'\x0a'+bytes([len(file_inner)])+file_inner
    cfg=account_case(tmp_path,changes={
      'message/message_0.db':f"INSERT INTO {TABLE}(local_id,server_id,local_type,real_sender_id,create_time,message_content,WCDB_CT_message_content) VALUES(1,9007199254740993,1,1,1700000000,'Known message',0),(2,9007199254740994,{(57<<32)|49},2,1700000001,x'{QUOTE.hex()}',0),(3,333,3,1,1700000002,'<msg><img /></msg>',0),(4,444,{(6<<32)|49},1,1700000003,'<msg><appmsg><type>6</type><title>test.bin</title></appmsg></msg>',0)",
      'message/message_resource.db':f"INSERT INTO ChatName2Id(rowid,user_name) VALUES(1,'{CHAT}'); INSERT INTO MessageResourceInfo(message_id,chat_id,message_local_id,message_svr_id,message_local_type,message_create_time,packed_info) VALUES(3,1,3,333,3,1700000002,x'{image_info.hex()}'),(4,1,4,444,{(6<<32)|49},1700000003,NULL); INSERT INTO MessageResourceDetail(resource_id,message_id,type,size,status,packed_info) VALUES(3,3,131073,{len(raw)},1,NULL),(4,4,3342339,4,1,x'{file_detail.hex()}')",
      'hardlink/hardlink.db':f"INSERT INTO dir2id(rowid,username) VALUES(1,'2023-11'); INSERT INTO file_hardlink_info_v4(md5,type,file_name,file_size,dir1,dir2) VALUES('{digest}',1,'test.bin',4,1,0)"})
    month=datetime.fromtimestamp(1700000002,UTC).astimezone().strftime('%Y-%m')
    image=cfg.account_root/f'msg/attach/{hashlib.md5(CHAT.encode()).hexdigest()}/{month}/Img/{resource}.dat'
    image.parent.mkdir(parents=True);image.write_bytes(raw)
    file=cfg.account_root/'msg/file/2023-11/test.bin';file.parent.mkdir();file.write_bytes(b'test')
    a=open_adapter(cfg,build='270102');db=a._dbs['message/message_0.db'];conv=a.identities.conversation_id(CHAT)
    def selected(n,expected):
        return {'source':{'database_id':db.database_id,'table':TABLE,'local_id':str(n)},'conversation_id':conv,'expected':expected}
    samples={
      'message':selected(1,{'text':'Known message','sender_username':'fictional-alice','utc_timestamp':'2023-11-14T22:13:20+00:00'}),
      'contact':{'contact_id':a.identities.contact_id('fictional-alice'),'expected':{'source_username':'fictional-alice','display_name':'Same name'}},
      'group':{'conversation_id':conv,'expected':{'source_username':CHAT,'member_usernames':['fictional-alice','fictional-bob']}},
      'reply':selected(2,{'target_server_id':'9007199254740993','resolution_status':'resolved','quoted_text':'Quoted text'}),
      'original_image':selected(3,{'decoded_sha256':hashlib.sha256(raw).hexdigest(),'width':3,'height':2}),
      'ordinary_file':selected(4,{'source_sha256':hashlib.sha256(b'test').hexdigest(),'size':4})}
    return ProofCase(cfg,ProofSelection(samples,expectation_basis='synthetic_fixture',build='270102'),image)


def installed_build(monkeypatch,tmp_path,build='270102'):
    """Pin the 'installed' WeChat build so live-scope proofs don't read this host's app."""
    import plistlib
    from wechat_bridge.adapters import wechat_4_1
    path=tmp_path/'Info.plist';path.write_bytes(plistlib.dumps({'CFBundleShortVersionString':'4.1.15','CFBundleVersion':build}))
    monkeypatch.setattr(wechat_4_1,'INFO_PLIST',path)
