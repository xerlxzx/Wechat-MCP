import pytest
from wechat_bridge.core.errors import BridgeError


def test_preserves_documented_wire_values_without_guessing_nested_meanings():
    from wechat_bridge.adapters.wire import parse_fields
    fields = parse_fields(b"\x08\xac\x02\x12\x03abc\x1d\x01\x02\x03\x04\x21" + b"\0" * 8)
    assert [(f.number, f.wire_type, f.value) for f in fields] == [
        (1, 0, 300), (2, 2, b"abc"), (3, 5, b"\x01\x02\x03\x04"), (4, 1, b"\0" * 8)]


@pytest.mark.parametrize("payload", [b"\0", b"\x08\x80", b"\x08" + b"\xff" * 10,
    b"\x12\x7fabc", b"\x0b", b"\x0d\0", b"\x08\x81\0"])
def test_malformed_wire_is_bounded(payload):
    from wechat_bridge.adapters.wire import parse_fields
    with pytest.raises(BridgeError, match="malformed_protobuf"):
        parse_fields(payload)


def test_wire_byte_and_field_budgets():
    from wechat_bridge.adapters.wire import parse_fields
    with pytest.raises(BridgeError, match="payload_too_large"):
        parse_fields(b"x" * 11, max_bytes=10)
    with pytest.raises(BridgeError, match="protobuf_field_limit"):
        parse_fields(b"\x08\0" * 4097)
