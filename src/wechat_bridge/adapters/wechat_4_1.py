"""Explicit column mappings observed on 4.1.15 builds 270102 and 270135.

Source usernames and membership come from structured records. Unknown formats
retain diagnostics rather than inferred relationships.
"""
from pathlib import Path
import plistlib
import hashlib
import heapq
from dataclasses import replace

from ..core.clock import now_utc
from ..core.config import Config, write_private_json
from ..core.errors import BridgeError
from ..core.validate import require_int_range
from ..source.discovery import discover, MESSAGE_SHARD
from ..source.keys import load_manifest, verify_keys
from ..source.reader import open_source
from .identity import IdentityLookup, load_identity_key
from .models import Contact, Conversation, Membership, SourceRef
from .schema import SchemaDescriptor, MSG_TABLE, inspect_schema
from .wire import parse_fields

ADAPTER_VERSION = "wechat-4.1.15-270102-270135-v2"
# 270135 (auto-update, 14 Oct 2026) was accepted on evidence that every required
# table and column is identical to 270102; see docs/compatibility.md.
SUPPORTED_BUILDS = frozenset({"270102", "270135"})
INFO_PLIST = Path("/Applications/WeChat.app/Contents/Info.plist")
CONTACT = {"id": "INTEGER", "username": "TEXT", "nick_name": "TEXT", "remark": "TEXT"}
ROOM = {"id": "INTEGER", "username": "TEXT", "owner": "TEXT", "ext_buffer": "BLOB"}
MEMBER = {"room_id": "INTEGER", "member_id": "INTEGER"}
MESSAGE = {"local_id": "INTEGER", "server_id": "INTEGER", "local_type": "INTEGER",
           "real_sender_id": "INTEGER", "create_time": "INTEGER", "message_content": "TEXT",
           "WCDB_CT_message_content": "INTEGER"}
RESOURCE_INFO = {"message_id": "INTEGER", "chat_id": "INTEGER", "message_local_id": "INTEGER",
                 "message_svr_id": "INTEGER", "message_local_type": "INTEGER", "message_create_time": "INTEGER", "packed_info": "BLOB"}
RESOURCE_DETAIL = {"resource_id": "INTEGER", "message_id": "INTEGER", "type": "INTEGER",
                   "size": "INTEGER", "data_index": "TEXT", "packed_info": "BLOB", "status": "INTEGER"}
HARDLINK = {"md5": "TEXT", "file_name": "TEXT", "file_size": "INTEGER", "dir1": "INTEGER", "dir2": "INTEGER", "type": "INTEGER"}
KNOWN_CONTACT_COLUMNS = frozenset("id username local_type alias encrypt_username flag delete_flag verify_flag remark remark_quan_pin remark_pin_yin_initial nick_name pin_yin_initial quan_pin big_head_url small_head_url head_img_md5 chat_room_notify is_in_chat_room description extra_buffer chat_room_type".split())
KNOWN_MESSAGE_COLUMNS = frozenset("local_id server_id local_type sort_seq real_sender_id create_time status upload_status download_status server_seq origin_source source message_content compress_content packed_info_data WCDB_CT_message_content WCDB_CT_source".split())
KNOWN_SESSION_COLUMNS = frozenset("username type unread_count unread_first_msg_srv_id unread_first_pat_msg_local_id unread_first_pat_msg_sort_seq is_hidden summary draft status last_timestamp sort_timestamp last_clear_unread_timestamp last_msg_locald_id last_msg_type last_msg_sub_type last_msg_sender last_sender_display_name last_msg_ext_type".split())
KNOWN_HARDLINK_COLUMNS = frozenset("md5_hash md5 type file_name file_size modify_time dir1 dir2 _rowid_ extra_buffer".split())
MIN_TIME = -62135596800
MAX_TIME = 253402300799
TIME_VALID = f"(typeof(create_time)='integer' AND create_time BETWEEN {MIN_TIME} AND {MAX_TIME})"
TIME_SORT = f"(CASE WHEN {TIME_VALID} THEN create_time ELSE 0 END)"


def select_adapter(build: str, schemas: tuple[SchemaDescriptor, ...]):
    """Bind an adapter to a supported build after checking every required table, column and rowid alias.

    Unknown extra columns set parse_status to partial and are never dropped silently.
    Raises unsupported_build or unsupported_schema.
    """
    if build not in SUPPORTED_BUILDS:
        raise BridgeError("unsupported_build")
    by_path = {s.relative_path: s for s in schemas}
    expected = {"contact/contact.db": {"contact": CONTACT, "chat_room": ROOM},
                "session/session.db": {"SessionTable": {"username": "TEXT", "type": "INTEGER"}},
                "message/message_resource.db": {"ChatName2Id": {"user_name": "TEXT"}, "MessageResourceInfo": RESOURCE_INFO, "MessageResourceDetail": RESOURCE_DETAIL},
                "hardlink/hardlink.db": {"dir2id": {"username": "TEXT"}, "file_hardlink_info_v4": HARDLINK, "image_hardlink_info_v4": HARDLINK}}
    for path, mapping in expected.items():
        schema = by_path.get(path)
        if schema is None or any(not schema.table(t) or not schema.table(t).has(cols) or not schema.table(t).has_rowid for t, cols in mapping.items()):
            raise BridgeError("unsupported_schema")
    contact_schema = by_path["contact/contact.db"]
    if contact_schema.table("contact").rowid_alias != "id" or contact_schema.table("chat_room").rowid_alias != "id":
        raise BridgeError("unsupported_schema")
    shards = [s for s in schemas if MESSAGE_SHARD.fullmatch(s.relative_path)]
    if not shards:
        raise BridgeError("unsupported_schema")
    for schema in shards:
        if not schema.table("Name2Id") or not schema.table("Name2Id").has({"user_name": "TEXT"}) or not schema.table("Name2Id").has_rowid:
            raise BridgeError("unsupported_schema")
        if any(not t.has(MESSAGE) or t.rowid_alias != "local_id" for t in schema.tables if MSG_TABLE.fullmatch(t.name)):
            raise BridgeError("unsupported_schema")
        if any(t.name.startswith("Msg_") and not MSG_TABLE.fullmatch(t.name) for t in schema.tables):
            raise BridgeError("unsupported_schema")
    adapter = Wechat41Adapter(build, schemas)
    contact = by_path["contact/contact.db"]
    if (not contact.table("chatroom_member") or not contact.table("chatroom_member").has(MEMBER)
            or any(n not in KNOWN_CONTACT_COLUMNS for n, _ in contact.table("contact").columns)):
        adapter.parse_status = "partial"
    known = {"contact/contact.db": {"contact": KNOWN_CONTACT_COLUMNS, "chat_room": frozenset(ROOM), "chatroom_member": frozenset(MEMBER)},
             "session/session.db": {"SessionTable": KNOWN_SESSION_COLUMNS},
             "message/message_resource.db": {"ChatName2Id": frozenset(("user_name", "update_time")),
                 "MessageResourceInfo": frozenset("message_id chat_id sender_id message_local_type message_create_time message_local_id message_svr_id message_origin_source packed_info".split()),
                 "MessageResourceDetail": frozenset("resource_id message_id type size create_time access_time status data_index packed_info".split())},
             "hardlink/hardlink.db": {"dir2id": frozenset(("username",)), "file_hardlink_info_v4": KNOWN_HARDLINK_COLUMNS, "image_hardlink_info_v4": KNOWN_HARDLINK_COLUMNS}}
    for schema in schemas:
        for table in schema.tables:
            allowed = (KNOWN_MESSAGE_COLUMNS if MSG_TABLE.fullmatch(table.name) else frozenset(("user_name", "is_session"))) if MESSAGE_SHARD.fullmatch(schema.relative_path) and (MSG_TABLE.fullmatch(table.name) or table.name == "Name2Id") else known.get(schema.relative_path, {}).get(table.name)
            if allowed is not None and (not table.has_rowid or any(n not in allowed for n, _ in table.columns)):
                adapter.parse_status = "partial"
            if MSG_TABLE.fullmatch(table.name) and "packed_info_data" in dict(table.columns) and dict(table.columns)["packed_info_data"] != "BLOB":
                adapter.parse_status = "partial"
    return adapter


def open_adapter(config: Config, *, build: str | None = None):
    """Discover, verify the saved keys and return a bound adapter; never runs setup or touches WeChat.

    Raises setup_required, key_coverage_incomplete, unsupported_build or source_version_unavailable.
    """
    if build is None:
        try:
            with INFO_PLIST.open("rb") as stream:
                info = plistlib.load(stream)
            if info.get("CFBundleShortVersionString") != "4.1.15":
                raise BridgeError("unsupported_build")
            build = info.get("CFBundleVersion")
        except (OSError, plistlib.InvalidFileException):
            raise BridgeError("source_version_unavailable") from None
    inventory = discover(config)
    path = config.state_dir / "keys.json"
    if not path.exists():
        raise BridgeError("setup_required")
    coverage = verify_keys(inventory, load_manifest(path), config)
    if not coverage.ready:
        raise BridgeError("key_coverage_incomplete")
    owners = {d.database_id: d.relative_path for d in inventory.databases}
    entries = {e.database_id: e for e in coverage.verified_entries if owners.get(e.database_id) == e.relative_path}
    schemas = []
    for db in inventory.databases:
        if db.required:
            key = entries[db.database_id]
            with open_source(db, key.raw_key, config=config, profile=key.profile) as reader:
                schemas.append(inspect_schema(reader))
    adapter = select_adapter(build, tuple(schemas))
    adapter.config, adapter.inventory, adapter.coverage = config, inventory, coverage
    adapter._keys = entries
    adapter._dbs = {d.relative_path: d for d in inventory.databases if d.required}
    adapter.identities = IdentityLookup(inventory.account_id, load_identity_key(config))
    write_private_json(config.state_dir / "adapter-schemas.json", {"version": 1, "adapter_version": ADAPTER_VERSION,
                       "schemas": [s.private_dict() for s in schemas]})
    return adapter


class Wechat41Adapter:
    """Maps WeChat 4.1.15 tables to archive records. Reads are bounded and read-only; callers use open_adapter."""
    version = ADAPTER_VERSION

    def __init__(self, build, schemas):
        self.build = build
        self.schemas = schemas
        self._schemas = {s.relative_path: s for s in schemas}
        self.parse_status = "ok"
        self.observations = {}
        self._conversations = None

    def _open(self, relative):
        if not hasattr(self, "config"):
            raise BridgeError("adapter_not_bound")
        db = self._dbs[relative]; entry = self._keys[db.database_id]
        return open_source(db, entry.raw_key, config=self.config, profile=entry.profile)

    def _rows(self, relative, table, *, where="", params=()):
        quoted = self._schemas[relative].quote_table(table)
        # Conditions are internal literals; source values are always bound.
        # Keyset paging on rowid in 100-row transactions keeps each read short and bounded.
        cursor = None
        with self._open(relative) as reader:
            while True:
                with reader.transaction() as tx:
                    condition = "rowid>?" if cursor is not None else "1"
                    values = (cursor, *params) if cursor is not None else params
                    rows = tx.query(f"SELECT rowid AS bridge_rowid,* FROM {quoted} WHERE {condition} " + where + " ORDER BY rowid LIMIT 100", values, max_rows=100)
                    self.observations[reader.db.database_id] = now_utc()
                if not rows:
                    break
                for row in rows:
                    yield row
                cursor = rows[-1]["bridge_rowid"]

    def _source(self, relative, table, row, server_id=None):
        db = self._dbs[relative]
        return SourceRef(db.account_id, db.database_id, table, str(row["bridge_rowid"]), server_id,
                         self.observations[db.database_id])

    @staticmethod
    def _username(value):
        if not isinstance(value, str) or not value or "\0" in value:
            raise BridgeError("source_identity_invalid")
        return value

    def contacts(self):
        """Yield contacts in rowid order; raises source_identity_invalid for a malformed username."""
        for row in self._rows("contact/contact.db", "contact"):
            username = self._username(row["username"])
            nickname = row["nick_name"] or None; remark = row["remark"] or None
            yield Contact(self.identities.contact_id(username), username, nickname, remark,
                          remark or nickname, self._source("contact/contact.db", "contact", row),
                          self.parse_status, ("schema_partial",) if self.parse_status == "partial" else ())

    def conversations(self):
        """Yield each session or group once; membership is recorded only when the source table supports it."""
        contacts = {c.source_username: c for c in self.contacts()}
        groups = {self._username(r["username"]): r for r in self._rows("contact/contact.db", "chat_room")}
        member = self._schemas["contact/contact.db"].table("chatroom_member")
        supported = bool(member and member.has(MEMBER) and member.has_rowid)
        seen = set()
        for relative, table in (("session/session.db", "SessionTable"), ("contact/contact.db", "chat_room")):
            for row in self._rows(relative, table):
                username = self._username(row["username"])
                if username in seen:
                    continue
                seen.add(username)
                group = username.endswith("@chatroom")
                contact = contacts.get(username)
                membership = "recorded" if group and username in groups and supported else "unsupported"
                yield Conversation(self.identities.conversation_id(username), "group" if group else "direct", username,
                                   contact.display_name if contact else None,
                                   (self._source(relative, table, row),), membership, self.parse_status)

    def conversation(self, conversation_id):
        """Return the single matching conversation; zero or several matches raise conversation_not_found."""
        # An adapter serves one refresh or proof run; per-message lookups must
        # not rebuild the list (and reopen contact.db) every time.
        if self._conversations is None:
            self._conversations = {}
            for c in self.conversations():
                self._conversations.setdefault(c.conversation_id, []).append(c)
        found = self._conversations.get(conversation_id, [])
        if len(found) != 1:
            raise BridgeError("conversation_not_found")
        return found[0]

    def memberships(self, conversation_id):
        """Yield recorded group members. Unparsed nickname data becomes diagnostic codes, never guessed names."""
        try:
            group = self.conversation(conversation_id)
        except BridgeError:
            raise BridgeError("group_not_found") from None
        if group.kind != "group":
            raise BridgeError("group_not_found")
        if group.membership_status != "recorded":
            raise BridgeError("membership_unsupported")
        rooms = list(self._rows("contact/contact.db", "chat_room", where="AND username=?", params=(group.source_username,)))
        if len(rooms) != 1:
            raise BridgeError("group_identity_conflicting")
        room = rooms[0]; diagnostics = ()
        contacts = {int(c.source.local_id): c for c in self.contacts()}
        member_rows = tuple(self._rows("contact/contact.db", "chatroom_member", where="AND room_id=?", params=(room["id"],)))
        recorded_names = {contacts[r["member_id"]].source_username for r in member_rows if r["member_id"] in contacts}
        nicknames = {}
        if room["ext_buffer"]:
            try:
                for field in parse_fields(room["ext_buffer"]):
                    if field.number in (3, 4) and field.wire_type == 0:
                        continue
                    if field.number != 1 or field.wire_type != 2:
                        diagnostics += ("group_nickname_variant_unsupported",)
                        continue
                    fields = parse_fields(field.value)
                    names = [f.value.decode("utf-8") for f in fields if f.number == 1 and f.wire_type == 2]
                    cards = [f.value.decode("utf-8") for f in fields if f.number == 2 and f.wire_type == 2]
                    if len(names) != 1 or names[0] not in recorded_names:
                        diagnostics += ("group_member_mapping_mismatch",)
                    elif len(cards) == 1 and len(cards[0].encode()) <= 256:
                        if names[0] in nicknames and nicknames[names[0]] != cards[0]:
                            nicknames[names[0]] = None
                            diagnostics += ("group_nickname_conflicting",)
                        else:
                            nicknames[names[0]] = cards[0] or None
                    elif cards:
                        diagnostics += ("group_nickname_variant_unsupported",)
                    if any((f.number, f.wire_type) not in ((1, 2), (2, 2), (3, 0), (4, 2)) for f in fields):
                        diagnostics += ("group_nickname_variant_unsupported",)
            except BridgeError as error:
                diagnostics = (error.code,)
                nicknames = {}
            except UnicodeError:
                diagnostics = ("invalid_text_encoding",)
                nicknames = {}
        diagnostics = tuple(sorted(set(diagnostics)))
        for row in member_rows:
            contact = contacts.get(row["member_id"])
            codes = diagnostics + (() if contact else ("member_contact_missing",))
            yield Membership(conversation_id, contact.contact_id if contact else None,
                             nicknames.get(contact.source_username) if contact else None,
                             self.observations[self._dbs["contact/contact.db"].database_id],
                             parse_status="partial" if codes else "ok", diagnostic_codes=codes)

    def public_dict(self):
        """Redacted adapter summary: version, build and counts."""
        return {"adapter_version": self.version, "build": self.build, "schema_count": len(self.schemas),
                "status": self.parse_status, "message_shard_count": sum(bool(MESSAGE_SHARD.fullmatch(s.relative_path)) for s in self.schemas)}

    def _message_tables(self, username):
        name = "Msg_" + hashlib.md5(username.encode()).hexdigest()
        for schema in self.schemas:
            if MESSAGE_SHARD.fullmatch(schema.relative_path) and schema.table(name):
                yield schema, name

    def _resolve_reply(self, reply, username):
        from .replies import resolve_replies
        if reply.target_server_id is None or reply.resolution_status == "unsupported":
            return reply
        # Candidates are only matched, never trusted: several matches stay ambiguous in resolve_replies.
        server = int(reply.target_server_id)
        if not -(1 << 63) <= server < (1 << 63):
            return reply
        candidates = []
        for schema, table in self._message_tables(username):
            with self._open(schema.relative_path) as reader, reader.transaction() as tx:
                rows = tx.query("SELECT local_id FROM " + schema.quote_table(table) + " WHERE server_id=? ORDER BY local_id LIMIT 2", (server,), max_rows=2)
                self.observations[reader.db.database_id] = now_utc()
                for row in rows:
                    source = SourceRef(reader.db.account_id, reader.db.database_id, table, str(row["local_id"]))
                    candidates.append(self.identities.message_id(source))
        key = (reply.account_id, reply.conversation_id, reply.target_server_id)
        return resolve_replies((reply,), {key: tuple(candidates)})[0]

    def _message_stream(self, schema, table, username, batch_size):
        from .messages import decode_message
        self.identities.add_conversation(schema.database_id, table, username)
        cursor = None
        columns = "local_id,server_id,local_type,real_sender_id,create_time,CAST(message_content AS BLOB) AS message_content,WCDB_CT_message_content," + TIME_VALID + " AS bridge_time_valid," + TIME_SORT + " AS bridge_time_sort"
        if "packed_info_data" in dict(schema.table(table).columns):
            columns += ",packed_info_data"
        with self._open(schema.relative_path) as reader:
            while True:
                where = "" if cursor is None else f" WHERE {TIME_VALID}>? OR ({TIME_VALID}=? AND {TIME_SORT}>?) OR ({TIME_VALID}=? AND {TIME_SORT}=? AND local_id>?)"
                params = (batch_size,) if cursor is None else (cursor[0], cursor[0], cursor[1], cursor[0], cursor[1], cursor[2], batch_size)
                with reader.transaction() as tx:
                    rows = tx.query("SELECT " + columns + " FROM " + schema.quote_table(table) + where + " ORDER BY bridge_time_valid,bridge_time_sort,local_id LIMIT ?", params, max_rows=batch_size)
                    observed = now_utc()
                    self.observations[schema.database_id] = observed
                    sender_ids = tuple(sorted({r["real_sender_id"] for r in rows if type(r["real_sender_id"]) is int}))
                    if sender_ids:
                        for sender_id in sender_ids:
                            self.identities.clear_sender(schema.database_id, sender_id)
                        senders = tx.query("SELECT rowid,user_name FROM Name2Id WHERE rowid IN (" + ",".join("?" for _ in sender_ids) + ")", sender_ids, max_rows=batch_size)
                        for sender in senders:
                            value = sender["user_name"]
                            if isinstance(value, str) and value and "\0" not in value:
                                self.identities.add_sender(schema.database_id, sender["rowid"], value)
                if not rows:
                    break
                for row in rows:
                    server = str(row["server_id"]) if row["server_id"] else None
                    source = SourceRef(reader.db.account_id, schema.database_id, table, str(row["local_id"]), server, observed)
                    message = decode_message(row | {"timestamp_unit": "seconds"}, source, self.identities)
                    if message.reply:
                        message = replace(message, reply=self._resolve_reply(message.reply, username))
                    yield message
                cursor = rows[-1]["bridge_time_valid"], rows[-1]["bridge_time_sort"], rows[-1]["local_id"]

    def message_batches(self, conversation_id, *, batch_size=50):
        """Yield tuples of at most batch_size messages merged across shards in a stable order.

        Raises invalid_limit or conversation_has_no_local_message_table.
        """
        require_int_range(batch_size, 1, 200, "invalid_limit")  # source read batch, not the API record limit
        username = self.conversation(conversation_id).source_username
        tables = tuple(self._message_tables(username))
        if not tables:
            raise BridgeError("conversation_has_no_local_message_table")
        streams = [self._message_stream(s, t, username, batch_size) for s, t in tables]
        batch = []
        try:
            ordered = heapq.merge(*streams, key=lambda m: (int(m.utc_timestamp is not None), int(m.source_timestamp) if m.utc_timestamp is not None else 0, m.source.database_id, m.source.table, int(m.source.local_id)))
            for message in ordered:
                batch.append(message)
                if len(batch) == batch_size:
                    yield tuple(batch); batch = []
            if batch:
                yield tuple(batch)
        finally:
            for stream in streams:
                stream.close()

    def attachment_refs(self, message):
        """Attachment references for one message; unmapped ones stay explicit."""
        from ..attachments.mapping import attachment_refs
        return attachment_refs(self, message)
