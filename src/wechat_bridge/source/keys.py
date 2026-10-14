"""Exact per-database keys; verified coverage never implies full-page integrity."""
from dataclasses import dataclass, field, replace
import hmac
from pathlib import Path, PurePosixPath
import re

from ..core.clock import now_utc
from ..core.config import Config, read_private_json, write_private_json, state_lock
from ..core.errors import BridgeError
from .discovery import Inventory
from .reader import open_source

@dataclass(frozen=True)
class KeyEntry:
    """One database key bound to account, relative path and salt; validated on construction.

    Raises invalid_key_manifest. The key bytes never appear in repr.
    """
    account_id: str
    relative_path: str = field(repr=False)
    database_id: str
    salt_hex: str = field(repr=False)
    raw_key: bytes = field(repr=False)
    profile: str = "sqlcipher4"
    verified_at: str | None = None

    def __post_init__(self):
        if not all(isinstance(v, str) and v for v in (self.account_id, self.relative_path, self.database_id, self.salt_hex)):
            raise BridgeError("invalid_key_manifest")
        path = PurePosixPath(self.relative_path)
        if (path.is_absolute() or ".." in path.parts or "\\" in self.relative_path
                or str(path) != self.relative_path or not self.relative_path.endswith(".db")
                or not re.fullmatch("[0-9a-f]{32}", self.salt_hex)
                or not isinstance(self.raw_key, bytes) or len(self.raw_key) != 32
                or self.profile != "sqlcipher4"
                or self.verified_at is not None and not isinstance(self.verified_at, str)):
            raise BridgeError("invalid_key_manifest")

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.account_id, self.relative_path, self.salt_hex

    def private_dict(self):
        """Key-bearing form for keys.json only; never log or return it."""
        return {"account_id": self.account_id, "relative_path": self.relative_path,
                "database_id": self.database_id, "salt_hex": self.salt_hex,
                "raw_key_hex": self.raw_key.hex(), "profile": self.profile,
                "verified_at": self.verified_at}

@dataclass(frozen=True)
class KeyManifest:
    """Key entries with unique (account, path, salt) identities; raises invalid_key_manifest."""
    entries: tuple[KeyEntry, ...] = field(repr=False)
    version: int = 1

    def __post_init__(self):
        if self.version != 1 or len({e.identity for e in self.entries}) != len(self.entries):
            raise BridgeError("invalid_key_manifest")

    def private_dict(self):
        """Key-bearing form for keys.json only; never log or return it."""
        return {"version": self.version, "entries": [entry.private_dict() for entry in self.entries]}

@dataclass(frozen=True)
class DatabaseCoverage:
    """Verification status of one discovered database."""
    database_id: str
    required: bool
    status: str

@dataclass(frozen=True)
class Coverage:
    """Per-database key status. Verified means the schema reads, not full-page integrity."""
    databases: tuple[DatabaseCoverage, ...]
    issues: tuple[str, ...]
    verified_entries: tuple[KeyEntry, ...] = field(default=(), repr=False)

    def statuses(self) -> tuple[str, ...]:
        """Status strings in discovery order."""
        return tuple(item.status for item in self.databases)

    @property
    def ready(self):
        """True when at least one database is required and every required one is verified."""
        required = [item for item in self.databases if item.required]
        return bool(required) and "required_source_missing" not in self.issues and all(i.status == "verified" for i in required)

    def public_dict(self):
        """Redacted aggregate: counts, database IDs and statuses; no keys or paths."""
        return {"ready": self.ready, "discovered_count": len(self.databases),
                "required_count": sum(i.required for i in self.databases),
                "verified_count": sum(i.status == "verified" for i in self.databases),
                "verification_scope": "schema_read", "full_page_integrity_checked": False,
                "databases": [{"database_id": i.database_id, "required": i.required, "status": i.status}
                              for i in self.databases], "issues": list(self.issues)}


def load_manifest(path: Path) -> KeyManifest:
    """Load keys.json with strict field checks; raises invalid_key_manifest or private-state errors."""
    data = read_private_json(path)
    try:
        if data.get("version") != 1 or not isinstance(data.get("entries"), list):
            raise ValueError
        entries = []
        for row in data["entries"]:
            if not isinstance(row["raw_key_hex"], str) or not re.fullmatch("[0-9a-fA-F]{64}", row["raw_key_hex"]):
                raise ValueError
            entries.append(KeyEntry(row["account_id"], row["relative_path"], row["database_id"],
                row["salt_hex"], bytes.fromhex(row["raw_key_hex"]), row["profile"], row.get("verified_at")))
        return KeyManifest(tuple(entries))
    except (KeyError, TypeError, ValueError):
        raise BridgeError("invalid_key_manifest") from None


def verify_keys(inventory: Inventory, candidates: KeyManifest, config: Config) -> Coverage:
    """Open each discovered database read-only with its candidate key and record a status.

    A reader failure maps to a status; only BridgeError is caught, so other errors propagate.
    """
    entries = {entry.identity: entry for entry in candidates.entries}
    results, verified = [], []
    for db in inventory.databases:
        candidate = entries.get((db.account_id, db.relative_path, db.salt_hex))
        status = "replaced" if db.replaced else "missing_key"
        if candidate is not None:
            try:
                with open_source(db, candidate.raw_key, config=config, profile=candidate.profile):
                    pass
                # Same path and salt but another ID: the registry rotated this
                # incarnation. It stays unready until publication restores the key's ID.
                status = "verified" if candidate.database_id == db.database_id else "replaced"
                verified.append(replace(candidate, verified_at=now_utc()))
            except BridgeError as error:
                status = {"key_or_format_mismatch": "key_or_format_mismatch",
                          "source_changed": "replaced", "unsupported_cipher_version": "unsupported"}.get(error.code, "unreadable")
        results.append(DatabaseCoverage(db.database_id, db.required, status))
    return Coverage(tuple(results), inventory.issues, tuple(verified))


def publish_verified_manifest(manifest: KeyManifest, coverage: Coverage, config: Config) -> None:
    """Merge verified entries into keys.json under the state lock; unverified keys never persist.

    Raises coverage_manifest_mismatch if coverage names an entry the manifest lacks.
    """
    if not coverage.verified_entries:
        return
    supplied = {e.identity: e for e in manifest.entries}
    for verified in coverage.verified_entries:
        entry = supplied.get(verified.identity)
        if (entry is None or entry.database_id != verified.database_id or entry.profile != verified.profile
                or not hmac.compare_digest(entry.raw_key, verified.raw_key)):
            raise BridgeError("coverage_manifest_mismatch")
    with state_lock(config):
        registry_path = config.state_dir / "inventory.json"
        registry = read_private_json(registry_path) if registry_path.exists() else None
        accepted = []
        for entry in coverage.verified_entries:
            row = registry["databases"].get(entry.relative_path) if registry else None
            if registry is None:
                accepted.append(entry); continue
            if not row or row["salt_hex"] != entry.salt_hex:
                continue
            # Restore an ID only for a row flagged rotated, never onto an ID another
            # row owns. A rejected entry is not saved, so it cannot poison keys.json.
            taken = any(other is not row and other["database_id"] == entry.database_id
                        for other in registry["databases"].values())
            if row["database_id"] == entry.database_id or (row.get("replaced") and not taken):
                row["database_id"], row["replaced"] = entry.database_id, False
                accepted.append(entry)
        if not accepted:
            return
        path = config.state_dir / "keys.json"
        existing = load_manifest(path) if path.exists() else KeyManifest(())
        merged = {e.identity: e for e in existing.entries}
        for entry in accepted:
            merged[entry.identity] = entry
        write_private_json(path, KeyManifest(tuple(merged.values())).private_dict())
        if registry is not None:
            write_private_json(registry_path, registry)
