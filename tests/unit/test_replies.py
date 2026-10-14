from tests.support.samples import source, row, identities, QUOTE


def test_cross_shard_resolution_uses_account_conversation_and_exact_server_id():
    from wechat_bridge.adapters.messages import decode_message
    from wechat_bridge.adapters.replies import resolve_replies
    message = decode_message(row(QUOTE, local_type=(57 << 32) | 49), source(), identities())
    reply = message.reply
    key = ("account", message.conversation_id, "9007199254740993")
    resolved = resolve_replies((reply,), {key: ("message_target_shard2",)})[0]
    assert resolved.target_server_id == "9007199254740993"
    assert resolved.target_message_id == "message_target_shard2"
    assert resolved.resolution_status == "resolved"
    assert resolve_replies((reply,), {})[0].resolution_status == "unresolved"
    assert resolve_replies((reply,), {("other", key[1], key[2]): ("wrong",)})[0].resolution_status == "unresolved"
    ambiguous = resolve_replies((reply,), {key: ("message_1", "message_2")})[0]
    assert ambiguous.resolution_status == "ambiguous"
    assert ambiguous.target_message_id is None
    assert reply.quoted_sender == "Duplicate name"


def test_unknown_reply_variant_is_explicitly_unsupported():
    from wechat_bridge.adapters.messages import decode_message
    message = decode_message(row(b'<msg><appmsg><type>57</type><title>Reply</title></appmsg></msg>', local_type=(57 << 32) | 49), source(), identities())
    assert message.reply.resolution_status == "unsupported"
