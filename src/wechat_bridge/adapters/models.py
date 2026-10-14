"""Normalized source records. Private content is omitted from repr."""
from dataclasses import dataclass, field
from typing import Literal

ParseStatus = Literal["ok", "partial", "unsupported", "malformed"]


@dataclass(frozen=True)
class SourceRef:
    account_id: str
    database_id: str
    table: str = field(repr=False)
    local_id: str = field(repr=False)
    server_id: str | None = field(default=None, repr=False)
    observed_at: str = ""


@dataclass(frozen=True)
class Contact:
    contact_id: str
    source_username: str = field(repr=False)
    nickname: str | None = field(repr=False)
    remark: str | None = field(repr=False)
    display_name: str | None = field(repr=False)
    source: SourceRef
    parse_status: ParseStatus = "ok"
    diagnostic_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Conversation:
    conversation_id: str
    kind: str
    source_username: str = field(repr=False)
    display_name: str | None = field(repr=False)
    sources: tuple[SourceRef, ...]
    membership_status: str = "unsupported"
    parse_status: ParseStatus = "ok"
    diagnostic_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Membership:
    conversation_id: str
    contact_id: str | None
    group_nickname: str | None = field(repr=False)
    observed_at: str
    role: str | None = None
    parse_status: ParseStatus = "ok"
    diagnostic_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reply:
    source_message_id: str
    account_id: str
    conversation_id: str
    target_server_id: str | None = field(repr=False)
    target_message_id: str | None = None
    quoted_text: str | None = field(default=None, repr=False)
    quoted_sender: str | None = field(default=None, repr=False)
    resolution_status: str = "unresolved"
    diagnostic_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Message:
    message_id: str
    conversation_id: str
    sender_id: str | None
    source_timestamp: str = field(repr=False)
    timestamp_unit: str
    utc_timestamp: str | None = field(repr=False)
    source_type: str
    type: str
    text: str | None = field(repr=False)
    source: SourceRef
    parse_status: ParseStatus = "ok"
    diagnostic_codes: tuple[str, ...] = ()
    reply: Reply | None = None
