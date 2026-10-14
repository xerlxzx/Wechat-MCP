import compression.zstd as zstd
import pytest
from tests.support.samples import source, row, identities, QUOTE


def test_plain_and_zstandard_chinese_emoji_and_large_server_id():
    from wechat_bridge.adapters.messages import decode_message
    ids = identities()
    plain = decode_message(row(), source(), ids)
    compressed = decode_message(row(zstd.compress("发票 🐈".encode()), WCDB_CT_message_content=4), source(), ids)
    assert plain.text == compressed.text == "发票 🐈"
    assert plain.source.server_id == "9007199254740994"
    assert plain.utc_timestamp == "2023-11-14T22:13:20+00:00"
    assert plain.parse_status == compressed.parse_status == "ok"


def test_timestamp_unit_is_explicit_not_guessed_by_magnitude():
    from wechat_bridge.adapters.messages import decode_message
    value = decode_message(row(create_time=1700000000000, timestamp_unit="milliseconds"), source(), identities())
    assert value.utc_timestamp == "2023-11-14T22:13:20+00:00"
    assert value.source_timestamp == "1700000000000"
    assert value.timestamp_unit == "milliseconds"


@pytest.mark.parametrize("payload,flag,code", [(b"\xff",0,"invalid_text_encoding"), (b"a\0b",0,"invalid_text_content"),
    (b"bad-zstd",4,"malformed_compression"),(zstd.compress(b"x" * 1048577),4,"payload_too_large"),
    (zstd.compress(b"x")[:-1],4,"malformed_compression"),(b"x",99,"unsupported_compression")])
def test_rejected_payload_preserves_source_identity(payload,flag,code):
    from wechat_bridge.adapters.messages import decode_message
    message = decode_message(row(payload, WCDB_CT_message_content=flag), source(), identities())
    assert message.text is None
    assert message.source.local_id == "1"
    assert code in message.diagnostic_codes


def test_sender_prefix_requires_group_and_matching_structured_identity():
    from wechat_bridge.adapters.messages import decode_message
    assert decode_message(row(b"alice:\nHello"), source(), identities()).text == "Hello"
    conflict = decode_message(row(b"bob:\nHello"), source(), identities())
    assert conflict.text == "bob:\nHello"
    assert "sender_prefix_conflict" in conflict.diagnostic_codes
    assert decode_message(row(b"alice:\nHello"), source(), identities(False)).text == "alice:\nHello"
    missing = decode_message(row(real_sender_id=99), source(), identities())
    assert missing.sender_id is None
    assert missing.parse_status == "partial"


def test_unknown_type_is_retained_with_diagnostic():
    from wechat_bridge.adapters.messages import decode_message
    message = decode_message(row(b"opaque", local_type=99999), source(), identities())
    assert message.type == "unsupported"
    assert message.source_type == "99999"
    assert message.parse_status == "unsupported"
    assert message.text is None


def test_normalized_reply_does_not_expose_xml_or_download_credentials():
    from wechat_bridge.adapters.messages import decode_message
    from dataclasses import asdict
    message = decode_message(row(QUOTE, local_type=(57 << 32) | 49), source(), identities())
    assert message.text == "A reply"
    assert message.reply.quoted_text == "Quoted text"
    assert "private-download-token" not in str(asdict(message))
    assert "<msg>" not in str(asdict(message))


@pytest.mark.parametrize("payload", [b'<msg>', b'<!DOCTYPE msg [<!ENTITY a "secret">]><msg>&a;</msg>',
    b'<msg>' + b'<a>'*33 + b'</a>'*33 + b'</msg>'])
def test_bad_or_overdeep_xml_retains_malformed_message(payload):
    from wechat_bridge.adapters.messages import decode_message
    message = decode_message(row(payload, local_type=(57 << 32) | 49), source(), identities())
    assert message.parse_status == "malformed"
    assert message.text is None


def test_malformed_packed_info_keeps_message_with_diagnostic():
    from wechat_bridge.adapters.messages import decode_message
    message = decode_message(row(packed_info_data=b'\x12\x7f'), source(), identities())
    assert message.text == "发票 🐈"
    assert message.parse_status == "partial"
    assert "malformed_protobuf" in message.diagnostic_codes
