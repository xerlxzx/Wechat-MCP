"""Bounded schema observation and identifier whitelisting."""
from dataclasses import dataclass, field
import hashlib
import json
import re
from ..core.clock import now_utc
from ..core.errors import BridgeError

MSG_TABLE = re.compile(r"Msg_[0-9a-f]{32}")


@dataclass(frozen=True)
class TableSchema:
    """Observed columns, primary key and rowid alias of one table."""
    name: str = field(repr=False)
    columns: tuple[tuple[str, str], ...]
    primary_key: tuple[str, ...] = ()
    rowid_alias: str | None = None
    has_rowid: bool = True

    def has(self, mapping: dict[str, str]) -> bool:
        """True when every named column exists with the given declared type."""
        return all(dict(self.columns).get(k) == v for k, v in mapping.items())


@dataclass(frozen=True)
class SchemaDescriptor:
    """Observed table shapes of one database plus a fingerprint that ignores message-table hashes."""
    database_id: str
    relative_path: str = field(repr=False)
    tables: tuple[TableSchema, ...] = field(repr=False)
    fingerprint: str
    observed_at: str

    def table(self, name: str) -> TableSchema | None:
        """Look up a table by exact name."""
        return next((t for t in self.tables if t.name == name), None)

    def quote_table(self, name: str) -> str:
        """Quote a table name only if the schema lists it; raises source_table_not_allowed."""
        if not self.table(name):
            raise BridgeError("source_table_not_allowed")
        return '"' + name.replace('"', '""') + '"'

    def private_dict(self):
        """Full shape for private state; contains source table names, never return it to callers."""
        return {"database_id": self.database_id, "relative_path": self.relative_path,
                "fingerprint": self.fingerprint, "observed_at": self.observed_at,
                "tables": [{"name": t.name, "columns": list(t.columns), "primary_key": t.primary_key,
                            "rowid_alias": t.rowid_alias, "has_rowid": t.has_rowid} for t in self.tables]}


def inspect_schema(reader) -> SchemaDescriptor:
    """Read table shapes through the reader in 100-table pages; raises schema_limit past 10000 tables."""
    tables = []
    with reader.transaction() as tx:
        after = ""
        while rows := tx.query("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name>? ORDER BY name LIMIT 100", (after,), max_rows=100):
            for row in rows:
                name = row["name"]
                cols = tx.query('PRAGMA table_info("' + name.replace('"', '""') + '")')
                indexes = tx.query('PRAGMA index_list("' + name.replace('"', '""') + '")')
                primary = tuple(c["name"] for c in sorted(cols, key=lambda c: c["pk"]) if c["pk"])
                has_rowid = not bool(re.search(r"\bWITHOUT\s+ROWID\b", row["sql"] or "", re.I))
                alias = (primary[0] if has_rowid and len(primary) == 1
                         and next(c["type"].upper() for c in cols if c["name"] == primary[0]) == "INTEGER"
                         and not any(i["origin"] == "pk" for i in indexes) else None)
                tables.append(TableSchema(name, tuple((c["name"], c["type"].upper()) for c in cols), primary, alias, has_rowid))
                if len(tables) > 10000:
                    raise BridgeError("schema_limit")
            after = rows[-1]["name"]
    shape = sorted({("Msg_<hash>" if MSG_TABLE.fullmatch(t.name) else t.name, t.columns,
                     t.primary_key, t.rowid_alias or "", t.has_rowid) for t in tables})
    fingerprint = hashlib.sha256(json.dumps(shape, separators=(",", ":")).encode()).hexdigest()
    return SchemaDescriptor(reader.db.database_id, reader.db.relative_path, tuple(tables), fingerprint, now_utc())
