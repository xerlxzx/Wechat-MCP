"""Exact resource joins and tested hardlink/layout mappings; no filename search."""
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import os
import re

from ..adapters.identity import stable_id
from ..adapters.wire import parse_fields
from ..core.clock import now_utc
from ..core.errors import BridgeError
from .models import AttachmentRef
from .resolver import open_media, file_identity


def _field(data, number):
    fields = [f for f in parse_fields(data) if f.number == number and f.wire_type == 2]
    if len(fields) != 1:
        raise BridgeError("attachment_metadata_variant_unsupported")
    return fields[0].value


def attachment_refs(adapter, message):
    """Build references for an image or file message from exact resource-table joins.

    Matches on conversation, local ID, type and timestamp; never searches by filename.
    Conflicting or unmapped rows become explicit refs with a diagnostic code.
    Raises invalid_source_reference if the message does not belong to the adapter's account.
    """
    if message.type not in ("image", "file"):
        return ()
    source = message.source
    if source.account_id != adapter.inventory.account_id or adapter.identities.message_id(source) != message.message_id:
        raise BridgeError("invalid_source_reference")
    username = adapter.conversation(message.conversation_id).source_username
    if not any(s.database_id == source.database_id and t == source.table for s, t in adapter._message_tables(username)):
        raise BridgeError("invalid_source_reference")
    schema = adapter._schemas["message/message_resource.db"]
    key = adapter.identities._key

    # The attachment ID derives from the message row and resource ID, not from the path.
    def make(resource_id, **kwargs):
        aid = stable_id("attachment", (source.account_id, source.database_id, source.table, source.local_id, str(resource_id)), key)
        return AttachmentRef(aid, message.message_id, message.type, **kwargs)

    with adapter._open(schema.relative_path) as reader, reader.transaction() as tx:
        predicate = "i.message_svr_id=?" if source.server_id else "i.message_local_id=?"
        identity = int(source.server_id or source.local_id)
        rows = tx.query("SELECT i.message_id,i.packed_info AS info,d.resource_id,d.type,d.size,d.status,d.packed_info AS detail "
                        "FROM MessageResourceInfo i JOIN ChatName2Id c ON c.rowid=i.chat_id "
                        "LEFT JOIN MessageResourceDetail d ON d.message_id=i.message_id "
                        "WHERE c.user_name=? AND " + predicate + " AND i.message_local_id=? AND i.message_local_type=? AND i.message_create_time=? LIMIT 200",
                        (username, identity, int(source.local_id), int(message.source_type), int(message.source_timestamp)))
        adapter.observations[reader.db.database_id] = now_utc()
    if not rows:
        return (make("missing", relative_path=None, mapping_provenance="unsupported", diagnostic_code="attachment_resource_missing"),)
    if len({r["message_id"] for r in rows}) != 1 or len({r["resource_id"] for r in rows}) != len(rows):
        return (make("conflict", relative_path=None, mapping_provenance="conflicting"),)
    refs = []
    for row in rows:
        try:
            if type(row["size"]) is not int or row["size"] < 0:
                raise BridgeError("attachment_metadata_variant_unsupported")
            if message.type == "file":
                if row["type"] != 3342339:
                    raise BridgeError("attachment_metadata_variant_unsupported")
                nested = _field(row["detail"], 1)
                original = _field(nested, 1).decode("utf-8")
                local = _field(nested, 2).decode("utf-8")
                if not local or "/" in local or "\\" in local or "\0" in local or local in (".", ".."):
                    raise BridgeError("attachment_path_invalid")
                hardlinks = list(adapter._rows("hardlink/hardlink.db", "file_hardlink_info_v4", where="AND file_name=? AND file_size=?", params=(local, row["size"])))
                # Type 1 is the supported ordinary-file layout. Other classes
                # may reuse the same filename/size but are not candidates for it.
                supported = [h for h in hardlinks if h["type"] == 1]
                if supported:
                    hardlinks = supported
                if len(hardlinks) != 1:
                    refs.append(make(row["resource_id"], relative_path=None, mapping_provenance="conflicting" if hardlinks else "unsupported", diagnostic_code="attachment_hardlink_missing" if not hardlinks else None)); continue
                hardlink = hardlinks[0]
                dirs = list(adapter._rows("hardlink/hardlink.db", "dir2id", where="AND rowid=?", params=(hardlink["dir1"],)))
                digest = hardlink["md5"]
                if (len(dirs) != 1 or not re.fullmatch(r"[0-9]{4}-[0-9]{2}", dirs[0]["username"] or "")
                        or hardlink["type"] != 1 or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", digest)):
                    raise BridgeError("attachment_metadata_variant_unsupported")
                relative = "msg/file/" + dirs[0]["username"] + "/" + local
                ref = make(row["resource_id"], relative_path=relative, original_filename=original, expected_size=row["size"],
                           expected_digest=digest, mapping_provenance="resource_hardlink", declared_local=row["status"] == 1)
            else:
                resource = _field(_field(row["info"], 2), 1).decode("ascii")
                if not re.fullmatch(r"[0-9a-f]{32}", resource) or row["type"] not in (131073, 262145):
                    raise BridgeError("attachment_metadata_variant_unsupported")
                month = datetime.fromtimestamp(int(message.source_timestamp), UTC).astimezone().strftime("%Y-%m")
                thumbnail = row["type"] == 262145
                relative = "msg/attach/" + hashlib.md5(username.encode()).hexdigest() + "/" + month + "/Img/" + resource + ("_t.dat" if thumbnail else ".dat")
                ref = make(row["resource_id"], relative_path=relative, expected_size=row["size"], representation="thumbnail" if thumbnail else "original",
                           mapping_provenance="resource_layout", declared_local=row["status"] == 1)
            try:
                with open_media(adapter.config, ref.relative_path) as stream:
                    ref = replace(ref, file_identity=file_identity(os.fstat(stream.fileno())))
            except (OSError, BridgeError):
                pass  # Resolver classifies missing/unsafe paths without returning bytes.
            refs.append(ref)
        except (BridgeError, UnicodeError, TypeError) as error:
            code = error.code if isinstance(error, BridgeError) else "attachment_metadata_malformed"
            refs.append(make(row["resource_id"], relative_path=None, mapping_provenance="unsupported", diagnostic_code=code))
    return tuple(refs)
