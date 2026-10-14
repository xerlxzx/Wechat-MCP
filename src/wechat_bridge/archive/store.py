"""Unpublished batches and one atomic pointer to a completed generation."""
from contextlib import contextmanager
import json
import os
import sqlite3

from ..core.clock import now_utc
from ..core.config import Config, check_private_fd, ensure_private_dir, reject_symlinks
from ..core.errors import BridgeError
from ..core.serialize import compact_json

KINDS = {'contacts': 'contact_id', 'conversations': 'conversation_id',
         'membership': 'record_id', 'messages': 'message_id',
         'replies': 'source_message_id', 'attachments': 'attachment_id',
         'observations': 'database_id'}


def encode(value):
    """Compact JSON used for stored record data and fingerprints."""
    return compact_json(value)


class Archive:
    """SQLite archive of generations; readers see only the generation archive_state points to.

    The file is owner-only and opened with O_NOFOLLOW. Schema versions 0 to 2 are accepted.
    """
    def __init__(self, config: Config):
        self.config = config
        ensure_private_dir(config.state_dir)
        self.path = config.state_dir / 'archive.sqlite3'
        reject_symlinks(self.path, 'unsafe_archive_path')
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            empty = check_private_fd(fd).st_size == 0
        finally:
            os.close(fd)
        with self.connection(write=True) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            existing_schema = db.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone()
            if version not in (0, 1, 2) or version == 0 and not empty and existing_schema:
                raise BridgeError('unsupported_archive_schema')
            if version == 0:
                self._create(db)

    @contextmanager
    def connection(self, *, write=False):
        """Yield one transaction: BEGIN IMMEDIATE read-write, or read-only with query_only.

        Commits on success, rolls back on error; write-side sqlite errors become archive_write_failed.
        """
        reject_symlinks(self.path, 'unsafe_archive_path')
        db = sqlite3.connect(self.path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                             uri=True, timeout=2, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            if write:
                db.execute('PRAGMA journal_mode=WAL')
            else:
                db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.execute('COMMIT')
        except BaseException as error:
            if db.in_transaction:
                db.execute('ROLLBACK')
            if write and isinstance(error, sqlite3.Error):
                raise BridgeError('archive_write_failed') from None
            raise
        finally:
            db.close()

    def _create(self, db):
        db.execute('CREATE TABLE generations(generation INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN (\'building\',\'complete\',\'failed\')), started_at TEXT NOT NULL, published_at TEXT, metadata TEXT, error TEXT)')
        db.execute('CREATE TABLE archive_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1), active_generation INTEGER REFERENCES generations(generation))')
        db.execute('INSERT INTO archive_state VALUES(1,NULL)')
        for kind in KINDS:
            extra = (', conversation_id TEXT, sender_id TEXT, type TEXT, timestamp TEXT, time_sort INTEGER NOT NULL, text TEXT' if kind == 'messages'
                     else ', message_id TEXT, kind TEXT, representation TEXT' if kind == 'attachments'
                     else ', conversation_id TEXT' if kind in ('membership', 'replies') else '')
            db.execute(f'CREATE TABLE {kind}(generation INTEGER NOT NULL REFERENCES generations(generation) ON DELETE CASCADE, record_id TEXT NOT NULL, data TEXT NOT NULL{extra}, PRIMARY KEY(generation,record_id))')
        db.execute('CREATE INDEX message_timeline ON messages(generation,conversation_id,time_sort,record_id)')
        db.execute('CREATE INDEX message_sender ON messages(generation,sender_id,time_sort,record_id)')
        db.execute('CREATE INDEX message_type ON messages(generation,type,time_sort,record_id)')
        db.execute('CREATE INDEX attachments_message ON attachments(generation,message_id,record_id)')
        db.execute('CREATE INDEX membership_group ON membership(generation,conversation_id,record_id)')
        db.execute('PRAGMA user_version=1')

    def begin_generation(self, account_id: str) -> int:
        """Start a building generation and fail any interrupted one. Raises archive_account_mismatch."""
        with self.connection(write=True) as db:
            prior = db.execute('SELECT account_id FROM generations WHERE state="complete" LIMIT 1').fetchone()
            if prior and prior['account_id'] != account_id:
                raise BridgeError('archive_account_mismatch')
            abandoned = [r[0] for r in db.execute('SELECT generation FROM generations WHERE state="building"')]
            db.execute('UPDATE generations SET state="failed",error="interrupted_refresh" WHERE state="building"')
            # Reclaim a crashed stage's partial rows now instead of waiting for the
            # next publish prune; keep the failed generation row for diagnostics,
            # as Archive.fail does.
            for stale in abandoned:
                for kind in KINDS:
                    db.execute(f'DELETE FROM {kind} WHERE generation=?', (stale,))
            # Failed rows hold no records; keep only the latest for status, so
            # repeated failures cannot grow the table without bound.
            db.execute('DELETE FROM generations WHERE state="failed" AND generation<'
                       '(SELECT max(generation) FROM generations WHERE state="failed")')
            return db.execute('INSERT INTO generations(account_id,state,started_at) VALUES(?,"building",?)',
                              (account_id, now_utc())).lastrowid

    @staticmethod
    def _require_building(db, generation):
        row = db.execute('SELECT state FROM generations WHERE generation=?', (generation,)).fetchone()
        if not row or row['state'] != 'building':
            raise BridgeError('invalid_generation')

    def write_records(self, generation: int, kind: str, records) -> None:
        """Insert at most 200 records into a building generation.

        Raises invalid_record_kind, invalid_batch or invalid_generation.
        """
        if kind not in KINDS:
            raise BridgeError('invalid_record_kind')
        if not isinstance(records, (list, tuple)) or len(records) > 200:
            raise BridgeError('invalid_batch')
        with self.connection(write=True) as db:
            self._require_building(db, generation)
            for record in records:
                values = [generation, record[KINDS[kind]], encode(record)]
                if kind == 'messages':
                    values += [record['conversation_id'], record['sender_id'], record['type'],
                               record['utc_timestamp'], int(record['source_timestamp']) if record['utc_timestamp'] else -(1 << 63), record['text']]
                elif kind == 'attachments':
                    values += [record['message_id'], record['kind'], record['representation']]
                elif kind in ('membership', 'replies'):
                    values += [record['conversation_id']]
                db.execute(f'INSERT INTO {kind} VALUES(' + ','.join('?' for _ in values) + ')', values)
            # Version 2 archives keep the search index in step with each batch.
            if db.execute('PRAGMA user_version').fetchone()[0] == 2:
                from ..search.index import index_records
                index_records(db, generation, kind, records)

    def publish(self, generation: int, metadata) -> None:
        """Complete a generation and repoint archive_state in one transaction.

        Readers move to the new generation or stay on the old one. Keeps the newest
        rows plus the previous complete generation; raises invalid_generation.
        """
        with self.connection(write=True) as db:
            self._require_building(db, generation)
            counts = {kind: db.execute(f'SELECT count(*) FROM {kind} WHERE generation=?', (generation,)).fetchone()[0] for kind in KINDS}
            db.execute('UPDATE generations SET state="complete",published_at=?,metadata=? WHERE generation=?',
                       (now_utc(), encode(dict(metadata) | {'counts': counts}), generation))
            db.execute('UPDATE archive_state SET active_generation=? WHERE singleton=1', (generation,))
            keep = [r[0] for r in db.execute('SELECT generation FROM generations ORDER BY generation DESC LIMIT 2')]
            previous = db.execute('SELECT generation FROM generations WHERE state="complete" AND generation<>? ORDER BY generation DESC LIMIT 1', (generation,)).fetchone()
            if previous:
                keep.append(previous[0])
            db.execute('DELETE FROM generations WHERE generation NOT IN (' + ','.join('?' for _ in keep) + ')', keep)

    def fail(self, generation: int, code: str) -> None:
        """Mark a building generation failed with `code` and drop its rows; the active generation is untouched."""
        with self.connection(write=True) as db:
            self._require_building(db, generation)
            db.execute('UPDATE generations SET state="failed",error=? WHERE generation=?', (code, generation))
            # Failed stages retain diagnostics, not a second partial archive.
            for kind in KINDS:
                db.execute(f'DELETE FROM {kind} WHERE generation=?', (generation,))

    def status(self) -> dict:
        """Redacted aggregate of the active generation and the latest refresh outcome."""
        with self.connection() as db:
            active = db.execute('SELECT active_generation FROM archive_state').fetchone()[0]
            latest = db.execute('SELECT * FROM generations ORDER BY generation DESC LIMIT 1').fetchone()
            row = db.execute('SELECT * FROM generations WHERE generation=?', (active,)).fetchone()
            metadata = json.loads(row['metadata']) if row else {}
            return {'active_generation': active, 'account_id': row['account_id'] if row else None,
                    'published_at': row['published_at'] if row else None,
                    'last_refresh_error': latest['error'] if latest and latest['state'] == 'failed' else None,
                    'refresh_in_progress': bool(latest and latest['state'] == 'building'),
                    'counts': metadata.get('counts', {}), 'coverage': metadata.get('coverage', {}),
                    'source_observations': metadata.get('source_observations', {}),
                    'adapter_status': metadata.get('adapter_status'), 'source_fingerprint': metadata.get('source_fingerprint')}
