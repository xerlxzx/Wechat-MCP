"""Bounded decoding; malformed/unsupported records retain their source identity."""
import compression.zstd as zstd
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import xml.etree.ElementTree as ET

from ..core.errors import BridgeError
from .models import Message
from .wire import parse_fields

MAX_PAYLOAD = 1048576


def decode_payload(content: bytes | str | None, flag: int) -> bytes:
    """Return the raw payload, zstd-decompressed when flag is 4; output is capped at 1 MiB.

    Raises invalid_payload, payload_too_large, unsupported_compression or malformed_compression.
    """
    data = content.encode("utf-8") if isinstance(content, str) else content
    if data is None:
        data = b""
    if not isinstance(data, bytes):
        raise BridgeError("invalid_payload")
    if len(data) > MAX_PAYLOAD:
        raise BridgeError("payload_too_large")
    if flag == 0:
        return data
    if flag != 4:
        raise BridgeError("unsupported_compression")
    try:
        decoder = zstd.ZstdDecompressor(options={zstd.DecompressionParameter.window_log_max: 23})
        result = decoder.decompress(data, MAX_PAYLOAD + 1)
        if len(result) > MAX_PAYLOAD:
            raise BridgeError("payload_too_large")
        if not decoder.eof or decoder.unused_data:
            raise BridgeError("malformed_compression")
        return result
    except zstd.ZstdError:
        raise BridgeError("malformed_compression") from None


def payload_text(data: bytes) -> str:
    """Decode strict UTF-8 without NUL; raises invalid_text_encoding or invalid_text_content."""
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise BridgeError("invalid_text_encoding") from None
    if "\0" in text:
        raise BridgeError("invalid_text_content")
    return text


def parse_xml(data: bytes):
    """Parse untrusted XML with DOCTYPE and ENTITY refused, depth at most 32 and 4096 nodes.

    Raises payload_too_large, xml_declaration_denied, xml_structure_limit or malformed_xml.
    """
    if len(data) > MAX_PAYLOAD:
        raise BridgeError("payload_too_large")
    text = payload_text(data)
    if "<!doctype" in text.lower() or "<!entity" in text.lower():
        raise BridgeError("xml_declaration_denied")
    parser = ET.XMLPullParser(events=("start", "end"))
    depth = nodes = 0; root = None
    try:
        for offset in range(0, len(text), 4096):
            parser.feed(text[offset:offset + 4096])
            for event, element in parser.read_events():
                if event == "start":
                    depth += 1; nodes += 1
                    if root is None:
                        root = element
                    if depth > 32 or nodes > 4096:
                        raise BridgeError("xml_structure_limit")
                else:
                    depth -= 1
        parser.close()
        if root is None or depth:
            raise BridgeError("malformed_xml")
        return root
    except ET.ParseError:
        raise BridgeError("malformed_xml") from None


def decode_message(row, source, identities) -> Message:
    """Decode one source row into a Message; never raises for bad content.

    Unsupported or malformed rows keep their identity and carry a status and diagnostic code with text=None.
    """
    conversation_id, username, kind = identities.conversation(source.database_id, source.table)
    sender = identities.sender(source.database_id, row.get("real_sender_id"))
    codes = [] if sender.status == "ok" else ["sender_" + sender.status]
    value = row.get("create_time"); unit = row.get("timestamp_unit")
    utc = None
    try:
        if type(value) is not int or unit not in ("seconds", "milliseconds"):
            raise BridgeError("invalid_timestamp")
        utc = (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value * (1000000 if unit == "seconds" else 1000))).isoformat()
    except (OverflowError, ValueError, BridgeError):
        codes.append("invalid_timestamp")
    raw_type = row.get("local_type")
    message = Message(identities.message_id(source), conversation_id, sender.contact_id, str(value), str(unit), utc,
                      str(raw_type), "unsupported", None, source, "partial" if codes else "ok", tuple(codes))
    try:
        data = decode_payload(row.get("message_content"), row.get("WCDB_CT_message_content"))
        text = payload_text(data)
        if kind == "group" and ":\n" in text:
            prefix, rest = text.split(":\n", 1)
            if sender.status == "ok" and prefix == sender.source_username:
                text = rest; data = rest.encode()
            else:
                codes.append("sender_prefix_conflict")
        packed = row.get("packed_info_data")
        if packed:
            try:
                parse_fields(packed)
            except BridgeError as error:
                codes.append(error.code)
        base_type = raw_type & 0xffffffff if type(raw_type) is int else None
        if raw_type == 1:
            message = replace(message, type="text", text=text)
        elif raw_type == 10000 and not text.lstrip().startswith("<"):
            message = replace(message, type="system", text=text)
        elif base_type == 3:
            root = parse_xml(data)
            if root.tag != "msg" or root.find("img") is None:
                raise BridgeError("unsupported_image_message")
            message = replace(message, type="image")
        elif base_type == 49:
            root = parse_xml(data)
            if root.tag != "msg" or root.find("appmsg") is None:
                raise BridgeError("unsupported_app_message")
            app = root.find("appmsg"); subtype = app.findtext("type")
            source_subtype = raw_type >> 32
            if source_subtype and subtype != str(source_subtype):
                raise BridgeError("appmsg_type_mismatch")
            if subtype == "57":
                from .replies import parse_reply
                message = replace(message, type="reply", text=app.findtext("title") or None)
                reply = parse_reply(message, data)
                message = replace(message, reply=reply)
                if reply is None or reply.resolution_status == "unsupported":
                    codes.append("reply_variant_unsupported")
            elif subtype == "6":
                message = replace(message, type="file", text=app.findtext("title") or None)
            else:
                raise BridgeError("unsupported_app_message")
        else:
            raise BridgeError("unsupported_message_type")
        return replace(message, parse_status="partial" if codes else "ok", diagnostic_codes=tuple(codes))
    except BridgeError as error:
        codes.append(error.code)
        status = "unsupported" if error.code.startswith("unsupported_") else "malformed"
        return replace(message, text=None, parse_status=status, diagnostic_codes=tuple(codes))
