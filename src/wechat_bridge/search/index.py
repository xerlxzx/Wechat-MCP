"""Explicit atomic index setup and transaction-local unpublished batch indexing."""
import json

from ..core.errors import BridgeError
from ..archive.store import KINDS
from .tokenize import normalize, terms

INDEXED_KINDS = ('messages', 'contacts', 'conversations')


def index_records(db, generation, kind, records):
    """Insert search rows for one batch inside the caller's transaction; other kinds are skipped."""
    if kind not in INDEXED_KINDS:
        return
    for record in records:
        text = (record.get('text') or '' if kind == 'messages' else
                ' '.join(record.get(field) or '' for field in ('display_name', 'nickname', 'remark')))
        normalized = normalize(text)
        tokens = ' '.join((f'z{generation}', 'k' + kind, *terms(normalized)))
        db.execute('INSERT INTO record_search(kind,generation,record_id,normalized,tokens) VALUES(?,?,?,?,?)',
                   (kind, generation, record[KINDS[kind]], normalized, tokens))


def ensure_index(archive, *, building_generation=None):
    """Build the FTS table and triggers once, backfilling complete generations; sets user_version 2.

    Raises archive_refresh_in_progress if another stage is building.
    """
    with archive.connection(write=True) as db:
        if db.execute('PRAGMA user_version').fetchone()[0] == 2:
            return
        stages = {r[0] for r in db.execute('SELECT generation FROM generations WHERE state="building"')}
        if stages - {building_generation}:
            raise BridgeError('archive_refresh_in_progress')
        db.execute('CREATE VIRTUAL TABLE record_search USING fts5(kind UNINDEXED,generation UNINDEXED,record_id UNINDEXED,normalized UNINDEXED,tokens, tokenize="ascii")')
        db.execute('CREATE TRIGGER delete_generation_search AFTER DELETE ON generations BEGIN DELETE FROM record_search WHERE generation=OLD.generation; END')
        db.execute('CREATE TRIGGER fail_generation_search AFTER UPDATE OF state ON generations WHEN NEW.state="failed" BEGIN DELETE FROM record_search WHERE generation=NEW.generation; END')
        for kind in INDEXED_KINDS:
            cursor = db.execute(f'SELECT generation,data FROM {kind} WHERE generation IN (SELECT generation FROM generations WHERE state="complete") ORDER BY generation,record_id')
            while rows := cursor.fetchmany(200):
                for row in rows:
                    index_records(db, row['generation'], kind, (json.loads(row['data']),))
        db.execute('PRAGMA user_version=2')
