from wechat_bridge.adapters.models import SourceRef

# HMAC identity-lookup key for fixtures; distinct in role from the SQLCipher
# database key in tests.support.encrypted.KEY (do not conflate the two).
IDENTITY_KEY = bytes(range(32))

QUOTE = b'''<msg><appmsg><title>A reply</title><type>57</type><refermsg><type>1</type><svrid>9007199254740993</svrid><displayname>Duplicate name</displayname><content>Quoted text</content></refermsg><cdnurl>private-download-token</cdnurl></appmsg></msg>'''


def source(db="db1", local_id="1", server_id="9007199254740994"):
    return SourceRef("account", db, "Msg_synthetic", local_id, server_id, "2026-01-01T00:00:00+00:00")


def row(content="发票 🐈".encode(), **values):
    base = {"local_id": 1, "server_id": 9007199254740994, "local_type": 1, "real_sender_id": 1,
            "create_time": 1700000000, "timestamp_unit": "seconds", "message_content": content,
            "WCDB_CT_message_content": 0, "packed_info_data": None}
    return base | values


def identities(group=True):
    from wechat_bridge.adapters.identity import IdentityLookup
    ids = IdentityLookup("account", IDENTITY_KEY)
    ids.add_sender("db1", 1, "alice")
    ids.add_conversation("db1", "Msg_synthetic", "room@chatroom" if group else "alice")
    return ids
