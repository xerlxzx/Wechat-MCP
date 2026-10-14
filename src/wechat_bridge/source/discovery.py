"""Discover database identities without reading messages."""
from dataclasses import dataclass, field
from pathlib import Path
import re
import itertools
import uuid

from ..core.config import Config, read_private_json, write_private_json, reject_symlinks, state_lock
from ..core.errors import BridgeError

REQUIRED = frozenset(("contact/contact.db", "session/session.db",
                      "message/message_resource.db", "hardlink/hardlink.db"))
MESSAGE_SHARD = re.compile(r"message/message_[0-9]+\.db")

@dataclass(frozen=True)
class SourceDb:
    """One source database incarnation, keyed by relative path plus header salt."""
    account_id: str
    relative_path: str = field(repr=False)
    database_id: str
    salt_hex: str = field(repr=False)
    file_identity: tuple[int, int] = field(repr=False)
    wal_present: bool
    required: bool
    replaced: bool = False

@dataclass(frozen=True)
class Inventory:
    """Discovered databases and issue codes for one account."""
    account_id: str
    databases: tuple[SourceDb, ...]
    issues: tuple[str, ...] = ()

    def public_dict(self) -> dict:
        """Redacted counts and issue codes; no paths or identifiers."""
        return {"database_count": len(self.databases),
                "required_count": sum(d.required for d in self.databases),
                "wal_count": sum(d.wal_present for d in self.databases),
                "replaced_count": sum(d.replaced for d in self.databases),
                "issues": list(self.issues)}


def source_path(db: SourceDb, config: Config) -> Path:
    """Absolute path of a database under db_storage; raises invalid_source_reference or source_symlink."""
    relative = Path(db.relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise BridgeError("invalid_source_reference")
    path = config.account_root / "db_storage" / relative
    reject_symlinks(path, "source_symlink")
    return path


def discover(config: Config) -> Inventory:
    """Scan db_storage (at most 4096 entries), read each 16-byte salt and update the ID registry.

    Reads no message content. Raises source_unreadable, source_symlink,
    source_inventory_limit or source_header_invalid.
    """
    try:
        with state_lock(config):
            registry_path = config.state_dir / "inventory.json"
            registry = read_private_json(registry_path) if registry_path.exists() else {
                "version": 1, "account_id": str(uuid.uuid4()), "databases": {}}
            root = config.account_root / "db_storage"
            reject_symlinks(root, "source_symlink")
            if not root.is_dir():
                raise BridgeError("source_unreadable")
            databases, issues, observed = [], [], set()
            entries = list(itertools.islice(root.rglob("*"), 4097))
            if len(entries) > 4096:
                raise BridgeError("source_inventory_limit")
            for path in sorted(entries):
                if path.is_symlink():
                    raise BridgeError("source_symlink")
                if ".kvdb" in path.name:
                    issues.append("auxiliary_storage_unhandled")
                if path.suffix != ".db" or not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                with path.open("rb") as stream:
                    salt = stream.read(16).hex()
                if len(salt) != 32:
                    raise BridgeError("source_header_invalid")
                s = path.stat(); identity = [s.st_dev, s.st_ino]
                old = registry["databases"].get(relative)
                # The incarnation is path + salt. A same-salt rewrite keeps its
                # ID (and every message ID) but awaits key reverification.
                if old and old["salt_hex"] == salt:
                    entry = old if old["file_identity"] == identity else old | {
                        "file_identity": identity, "replaced": True}
                else:
                    entry = {"database_id": str(uuid.uuid4()), "salt_hex": salt,
                             "file_identity": identity, "replaced": old is not None}
                registry["databases"][relative] = entry
                observed.add(relative)
                databases.append(SourceDb(registry["account_id"], relative, entry["database_id"], salt,
                    tuple(identity), path.with_name(path.name + "-wal").exists(),
                    relative in REQUIRED or MESSAGE_SHARD.fullmatch(relative) is not None,
                    entry.get("replaced", False)))
            if not REQUIRED.issubset(observed) or not any(MESSAGE_SHARD.fullmatch(p) for p in observed):
                issues.append("required_source_missing")
            write_private_json(registry_path, registry)
            return Inventory(registry["account_id"], tuple(databases), tuple(sorted(set(issues))))
    except OSError:
        raise BridgeError("source_unreadable") from None
