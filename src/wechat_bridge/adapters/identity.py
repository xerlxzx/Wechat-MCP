"""Account-scoped opaque identities; names are never identity evidence."""
from dataclasses import dataclass, field
import hashlib
import hmac
import os
import re

from ..core.config import Config, read_private_json, write_private_json, state_lock
from ..core.errors import BridgeError


def stable_id(kind: str, components: tuple[str, ...], identity_key: bytes) -> str:
    """Keyed HMAC-SHA256 ID over length-prefixed components, so no two inputs share an encoding.

    Changing this derivation changes every stable ID. Raises invalid_identity_key,
    invalid_identity_kind or invalid_identity_components.
    """
    if not isinstance(identity_key, bytes) or len(identity_key) != 32:
        raise BridgeError("invalid_identity_key")
    if not isinstance(kind, str) or not re.fullmatch(r"[a-z_]{1,32}", kind):
        raise BridgeError("invalid_identity_kind")
    if not isinstance(components, tuple) or not all(isinstance(c, str) for c in components):
        raise BridgeError("invalid_identity_components")
    values = (kind, *components)
    encoded = b"wechat-bridge-id-v1\0" + len(values).to_bytes(4, "big")
    for value in values:
        data = value.encode("utf-8")
        encoded += len(data).to_bytes(4, "big") + data
    return kind + "_" + hmac.new(identity_key, encoded, hashlib.sha256).hexdigest()


def load_identity_key(config: Config) -> bytes:
    """Return the 32-byte account secret, creating it once under the state lock; raises invalid_identity_key."""
    with state_lock(config):
        path = config.state_dir / "identity.json"
        if not path.exists():
            write_private_json(path, {"version": 1, "secret_hex": os.urandom(32).hex()})
        data = read_private_json(path)
        if data.get("version") != 1 or not isinstance(data.get("secret_hex"), str) or not re.fullmatch("[0-9a-f]{64}", data["secret_hex"]):
            raise BridgeError("invalid_identity_key")
        return bytes.fromhex(data["secret_hex"])


@dataclass(frozen=True)
class IdentityResult:
    """Sender resolution: ok, missing or conflicting. The source username stays out of repr."""
    contact_id: str | None
    source_username: str | None = field(repr=False)
    status: str


class IdentityLookup:
    """Resolves source rows to opaque IDs for one account; a sender with several usernames is conflicting, never guessed."""
    def __init__(self, account_id: str, identity_key: bytes):
        self.account_id = account_id
        self._key = identity_key
        self._senders: dict[tuple[str, int], set[str]] = {}
        self._conversations: dict[str, tuple[str, str]] = {}

    def contact_id(self, username: str) -> str:
        return stable_id("contact", (self.account_id, username), self._key)

    def conversation_id(self, username: str) -> str:
        return stable_id("conversation", (self.account_id, username), self._key)

    def message_id(self, source) -> str:
        """Message ID from the database incarnation, table and local row."""
        return stable_id("message", (source.account_id, source.database_id, source.table, source.local_id), self._key)

    def add_sender(self, database_id: str, row_id: int, username: str) -> None:
        if type(row_id) is not int or not isinstance(username, str) or not username:
            raise BridgeError("source_identity_invalid")
        self._senders.setdefault((database_id, row_id), set()).add(username)

    def sender(self, database_id: str, row_id: int) -> IdentityResult:
        values = self._senders.get((database_id, row_id), set())
        if len(values) != 1:
            return IdentityResult(None, None, "missing" if not values else "conflicting")
        username = next(iter(values))
        return IdentityResult(self.contact_id(username), username, "ok")

    def clear_sender(self, database_id: str, row_id: int) -> None:
        self._senders.pop((database_id, row_id), None)

    def add_conversation(self, database_id: str, table: str, username: str) -> None:
        self._conversations[database_id + ":" + table] = (username, "group" if username.endswith("@chatroom") else "direct")

    def conversation(self, database_id: str, table: str) -> tuple[str, str, str]:
        """Return (conversation_id, source username, kind); raises conversation_mapping_missing."""
        value = self._conversations.get(database_id + ":" + table)
        if value is None:
            raise BridgeError("conversation_mapping_missing")
        return self.conversation_id(value[0]), value[0], value[1]
