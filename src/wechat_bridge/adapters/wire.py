"""Strict bounded protobuf wire parsing, without inferred field semantics."""
from dataclasses import dataclass, field
from ..core.errors import BridgeError


@dataclass(frozen=True)
class WireField:
    """One protobuf field: number, wire type and raw value, without interpretation."""
    number: int
    wire_type: int
    value: int | bytes = field(repr=False)


def parse_fields(data: bytes, *, max_bytes: int = 1048576) -> tuple[WireField, ...]:
    """Parse top-level fields: at most 1 MiB input and 4096 fields, no groups, no overlong varints.

    Raises invalid_wire_input, payload_too_large, protobuf_field_limit or malformed_protobuf.
    """
    if not isinstance(data, bytes) or type(max_bytes) is not int or not 1 <= max_bytes <= 1048576:
        raise BridgeError("invalid_wire_input")
    if len(data) > max_bytes:
        raise BridgeError("payload_too_large")
    offset = 0

    def varint():
        nonlocal offset
        value = 0
        for n in range(10):
            if offset >= len(data):
                raise BridgeError("malformed_protobuf")
            byte = data[offset]; offset += 1
            if n == 9 and byte > 1:
                raise BridgeError("malformed_protobuf")
            value |= (byte & 127) << (n * 7)
            if not byte & 128:
                if n and byte == 0:
                    raise BridgeError("malformed_protobuf")
                return value
        raise BridgeError("malformed_protobuf")

    fields = []
    while offset < len(data):
        if len(fields) >= 4096:
            raise BridgeError("protobuf_field_limit")
        tag = varint(); number, kind = tag >> 3, tag & 7
        if not 1 <= number < (1 << 29) or kind not in (0, 1, 2, 5):
            raise BridgeError("malformed_protobuf")
        if kind == 0:
            value = varint()
        else:
            size = varint() if kind == 2 else (8 if kind == 1 else 4)
            if size > len(data) - offset:
                raise BridgeError("malformed_protobuf")
            value = data[offset:offset + size]; offset += size
        fields.append(WireField(number, kind, value))
    return tuple(fields)
