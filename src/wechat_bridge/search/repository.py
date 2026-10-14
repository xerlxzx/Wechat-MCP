"""Small indexed queries; no source decryption, import or migration at query time."""
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import re
import sqlite3
import time

from ..adapters.identity import load_identity_key
from ..archive.importer import archive_status
from ..archive.store import Archive
from ..attachments.models import AttachmentRef
from ..core.config import Config
from ..core.errors import BridgeError
from ..core.limits import DEFAULT_RECORDS, MAX_RECORDS, MAX_CONTEXT_NEIGHBORS, MAX_QUERY_CHARACTERS
from ..core.validate import require_int_range
from .cursor import binding, encode_cursor, decode_cursor
from .tokenize import normalize, match_expression, source_offset

MAX_QUERY_BYTES = 8 * 1024 * 1024
AVAILABILITY_SQL = """CASE
WHEN json_extract(r.data,'$.mapping_provenance')='conflicting' THEN 'identity_mismatch'
WHEN json_extract(r.data,'$.mapping_provenance') NOT IN ('resource_hardlink','resource_layout','explicit_resource') THEN 'unsupported_format'
WHEN json_extract(r.data,'$.relative_path') IS NULL OR json_extract(r.data,'$.declared_local')=0 OR json_extract(r.data,'$.file_identity') IS NULL THEN 'not_local'
ELSE 'local_unverified' END"""


def valid_id(value, kind):
    """Return value if it is an opaque `<kind>_<64 hex>` ID; raises invalid_id."""
    if not isinstance(value, str) or not re.fullmatch(kind + r'_[0-9a-f]{64}', value):
        raise BridgeError('invalid_id')
    return value


def query_text(value, *, required=False):
    """Normalize a query of at most 256 characters without NUL; raises invalid_query."""
    if not isinstance(value, str) or len(value) > MAX_QUERY_CHARACTERS or '\0' in value:
        raise BridgeError('invalid_query')
    value = normalize(value)
    if required and not value:
        raise BridgeError('invalid_query')
    return value


def valid_limit(limit):
    """Require an int page size in [1, MAX_RECORDS]; raises invalid_limit."""
    require_int_range(limit, 1, MAX_RECORDS, 'invalid_limit')


def timestamp(value):
    """Parse an ISO-8601 time with an explicit offset to a UTC epoch; raises invalid_time_range."""
    if value is None:
        return None
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC).timestamp()
    except (ValueError, OverflowError):
        raise BridgeError('invalid_time_range') from None


def public_record(record):
    """Strip source usernames, paths, file identity, digests and table names before a record leaves the process."""
    result = {k: v for k, v in record.items() if k not in ('source_username', 'relative_path', 'file_identity', 'expected_digest', 'record_id')}
    for field in ('source', 'sources'):
        if field in result:
            values = result[field] if field == 'sources' else (result[field],)
            projected = [{k: v for k, v in source.items() if k != 'table'} for source in values]
            result[field] = projected if field == 'sources' else projected[0]
    return result


def attachment_availability(record):
    """Availability derived from the archived mapping alone; mirrors AVAILABILITY_SQL.

    Never reports a verified state: bytes are checked only at resolve time.
    """
    if record['mapping_provenance'] == 'conflicting':
        return 'identity_mismatch'
    if record['mapping_provenance'] not in ('resource_hardlink', 'resource_layout', 'explicit_resource'):
        return 'unsupported_format'
    if not record.get('relative_path') or record.get('declared_local') is False or record.get('file_identity') is None:
        return 'not_local'
    return 'local_unverified'


class QueryRepository:
    """Bounded read-only queries over the active archive generation.

    Needs a search-ready archive (search_not_ready). Every page is pinned to one
    complete generation and every record, page and query has a byte or time bound.
    """
    def __init__(self, config: Config):
        self.config, self.archive = config, Archive(config)
        with self.archive.connection() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != 2:
                raise BridgeError('search_not_ready')
        self._key = load_identity_key(config)

    @contextmanager
    def _snapshot(self, *, token=None, bound=None, generation=None):
        # A cursor pins its generation; an evicted one raises cursor_expired instead of silently switching.
        last = None
        if token is not None:
            generation, last = decode_cursor(token, self._key, bound)
        if generation is not None and (type(generation) is not int or not 1 <= generation < 1 << 63):
            raise BridgeError('invalid_generation')
        try:
            with self.archive.connection() as db:
                if generation is None:
                    generation = db.execute('SELECT active_generation FROM archive_state').fetchone()[0]
                row = db.execute('SELECT * FROM generations WHERE generation=? AND state="complete"', (generation,)).fetchone()
                if not row:
                    raise BridgeError('cursor_expired' if token or generation is not None else 'archive_not_ready')
                # 2 s query budget enforced through the SQLite progress handler.
                deadline = time.monotonic() + 2
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                yield db, row, last
        except sqlite3.Error:
            raise BridgeError('archive_query_failed') from None

    @staticmethod
    def _record(db, generation, kind, record_id):
        row = db.execute(f'SELECT data FROM {kind} WHERE generation=? AND record_id=?', (generation, record_id)).fetchone()
        if row is None:
            raise BridgeError(kind.rstrip('s') + '_not_found')
        if len(row[0].encode()) > MAX_QUERY_BYTES:
            raise BridgeError('content_too_large')
        return json.loads(row[0])

    def _items(self, db, generation, kind, hits, *, query=None):
        items, total = [], 0
        for hit in hits:
            record = self._record(db, generation, kind, hit['record_id'])
            total += len(json.dumps(record, ensure_ascii=False).encode())
            if total > MAX_QUERY_BYTES:
                raise BridgeError('content_too_large')
            item = public_record(record)
            if query is not None:
                text = item.pop('text') or ''
                offset = normalize(text).find(query)
                begin = max(0, source_offset(text, offset) - 80)
                item['excerpt'] = text[begin:begin + 240]
                item['excerpt_is_partial'] = len(text) > 240
            if kind == 'attachments':
                item['availability'] = attachment_availability(record)
                item['verification_status'] = 'not_checked'
            items.append(item)
        return items

    def _result(self, row, items, *, next_cursor=None):
        metadata = json.loads(row['metadata'])
        status = archive_status(self.config)
        return {'generation': row['generation'], 'snapshot_published_at': row['published_at'],
                'coverage': {k: v for k, v in metadata.get('coverage', {}).items() if k != 'missing_conversation_ids'},
                'freshness': {'stale': status['stale'] or row['generation'] != status['active_generation'],
                              'is_current_generation': row['generation'] == status['active_generation'],
                              'last_refresh_error': status['last_refresh_error'], 'source_error': status['source_error']},
                'items': items, 'next_cursor': next_cursor}

    def _paginate(self, hits, limit, generation, bound, key_of):
        # Over-fetch by one, slice to the page, and mint a next-page cursor from
        # the last kept row. key_of builds the per-path cursor key from that row;
        # it must pass the row's values through unchanged (no type coercion), or
        # SQLite affinity comparisons in the `after` clause would silently skip.
        selected = hits[:limit]
        token = encode_cursor(self._key, generation, bound, key_of(selected[-1])) if len(hits) > limit else None
        return selected, token

    def _list(self, kind: str, *, query: str = '', limit: int = DEFAULT_RECORDS, cursor: str | None = None, filters: dict | None = None) -> dict:
        valid_limit(limit); query = query_text(query)
        filters = dict(filters or {}) | {'query': query}
        bound = binding(kind, filters, limit)
        with self._snapshot(token=cursor, bound=bound) as (db, row, last):
            generation = row['generation']
            where, params = ['r.generation=?'], [generation]
            if query:
                normalized, expression = match_expression(query, generation, kind)
                where += ['r.record_id IN (SELECT record_id FROM record_search WHERE tokens MATCH ? AND instr(normalized,?)>0)']
                params += [expression, normalized]
            if last:
                where += ['r.record_id>?']; params += [last[0]]
            for key, value in filters.items():
                if value is None or key == 'query':
                    continue
                if key == 'kind' and kind == 'conversations':
                    where += ["json_extract(r.data,'$.kind')=?"]; params += [value]
                elif key == 'availability':
                    where += [AVAILABILITY_SQL + '=?']; params += [value]
                elif key in ('conversation_id', 'message_id', 'kind', 'representation'):
                    where += [f'r.{key}=?']; params += [value]
            hits = db.execute(f'SELECT r.record_id FROM {kind} r WHERE ' + ' AND '.join(where) + ' ORDER BY r.record_id LIMIT ?', (*params, limit + 1)).fetchall()
            selected, next_token = self._paginate(hits, limit, generation, bound, lambda last: [last['record_id']])
            result = self._result(row, self._items(db, generation, kind, selected), next_cursor=next_token)
            return result

    def list_contacts(self, query: str = '', *, limit: int = DEFAULT_RECORDS, cursor: str | None = None) -> dict:
        """Contacts by name substring, paginated by record ID; raises invalid_query, invalid_limit or invalid_cursor."""
        return self._list('contacts', query=query, limit=limit, cursor=cursor)

    def list_conversations(self, query: str = '', *, kind: str | None = None, limit: int = DEFAULT_RECORDS, cursor: str | None = None) -> dict:
        """Conversations by name substring, optionally filtered by direct or group; raises invalid_filter."""
        if kind not in (None, 'direct', 'group'):
            raise BridgeError('invalid_filter')
        return self._list('conversations', query=query, limit=limit, cursor=cursor, filters={'kind': kind})

    def get_group(self, conversation_id: str, *, limit: int = DEFAULT_RECORDS, cursor: str | None = None) -> dict:
        """Group record plus locally observed membership page; raises group_not_found or conversation_not_found."""
        valid_id(conversation_id, 'conversation'); valid_limit(limit)
        bound = binding('group', {'conversation_id': conversation_id}, limit)
        with self._snapshot(token=cursor, bound=bound) as (db, row, last):
            generation = row['generation']
            group = self._record(db, generation, 'conversations', conversation_id)
            if group['kind'] != 'group':
                raise BridgeError('group_not_found')
            hits = db.execute('SELECT record_id FROM membership WHERE generation=? AND conversation_id=? AND record_id>? ORDER BY record_id LIMIT ?',
                              (generation, conversation_id, last[0] if last else '', limit + 1)).fetchall()
            selected, token = self._paginate(hits, limit, generation, bound, lambda last: [last['record_id']])
            return self._result(row, self._items(db, generation, 'membership', selected), next_cursor=token) | {'group': public_record(group)}

    def _messages(self, query: str | None, *, conversation_id: str | None = None, sender_id: str | None = None, start: str | None = None, end: str | None = None, type: str | None = None, limit: int = DEFAULT_RECORDS, cursor: str | None = None) -> dict:
        valid_limit(limit)
        normalized = query_text(query, required=True) if query is not None else None
        if conversation_id is not None:
            valid_id(conversation_id, 'conversation')
        if sender_id is not None:
            valid_id(sender_id, 'contact')
        if type is not None and type not in ('text', 'image', 'file', 'reply', 'unsupported', 'voice', 'video'):
            raise BridgeError('invalid_filter')
        lower, upper = timestamp(start), timestamp(end)
        if lower is not None and upper is not None and lower > upper:
            raise BridgeError('invalid_time_range')
        filters = {'query': normalized, 'conversation_id': conversation_id, 'sender_id': sender_id, 'start': lower, 'end': upper, 'type': type}
        bound = binding('search' if normalized is not None else 'timeline', filters, limit)
        with self._snapshot(token=cursor, bound=bound) as (db, row, last):
            generation = row['generation']
            if conversation_id is not None:
                self._record(db, generation, 'conversations', conversation_id)
            where, params = ['m.generation=?'], [generation]
            for field in ('conversation_id', 'sender_id', 'type'):
                if filters[field] is not None:
                    where += [f'm.{field}=?']; params += [filters[field]]
            for value, operator in ((lower, '>='), (upper, '<=')):
                if value is not None:
                    where += [f'm.timestamp IS NOT NULL AND m.time_sort{operator}?']; params += [value]
            if normalized is not None:
                _, expression = match_expression(normalized, generation, 'messages')
                query_sql = ('SELECT m.record_id AS record_id,m.time_sort AS time_sort,instr(f.normalized,?) AS position,length(f.normalized) AS length '
                             'FROM record_search f JOIN messages m ON m.generation=f.generation AND m.record_id=f.record_id WHERE tokens MATCH ? AND instr(f.normalized,?)>0 AND ' + ' AND '.join(where))
                args = [normalized, expression, normalized, *params]
                order = 'position ASC,length ASC,time_sort DESC,record_id ASC'
                after = '((position,length)>(?,?) OR ((position,length)=(?,?) AND (time_sort<? OR (time_sort=? AND record_id>?))))'
            else:
                query_sql = 'SELECT m.record_id,m.time_sort FROM messages m WHERE ' + ' AND '.join(where)
                args, order, after = params, 'time_sort ASC,record_id ASC', '(time_sort,record_id)>(?,?)'
            if last:
                query_sql = 'SELECT * FROM (' + query_sql + ') WHERE ' + after
                args += [last[0], last[1], last[0], last[1], last[2], last[2], last[3]] if normalized is not None else last
            hits = db.execute(query_sql + ' ORDER BY ' + order + ' LIMIT ?', (*args, limit + 1)).fetchall()
            selected, token = self._paginate(hits, limit, generation, bound,
                lambda last: ([last['position'], last['length'], last['time_sort'], last['record_id']]
                              if normalized is not None else [last['time_sort'], last['record_id']]))
            result = self._result(row, self._items(db, generation, 'messages', selected, query=normalized), next_cursor=token)
            missing = json.loads(row['metadata']).get('coverage', {}).get('missing_conversation_ids', [])
            result['local_history_status'] = 'no_local_message_table' if conversation_id in missing else 'available_local_scope'
            return result

    def search_messages(self, query: str, **filters) -> dict:
        """Literal substring search with optional conversation, sender, time and type filters; ranked, cursor-paginated."""
        return self._messages(query, **filters)

    def get_messages(self, conversation_id: str, **filters) -> dict:
        """Chronological timeline of one conversation with the same filters as search; unsupported messages stay in."""
        return self._messages(None, conversation_id=conversation_id, **filters)

    def get_context(self, message_id: str, *, before: int = 10, after: int = 10, generation: int | None = None) -> dict:
        """One message plus at most MAX_CONTEXT_NEIGHBORS neighbors in its conversation; raises invalid_context_limit."""
        valid_id(message_id, 'message')
        if any(type(n) is not int or n < 0 for n in (before, after)) or before + after > MAX_CONTEXT_NEIGHBORS:
            raise BridgeError('invalid_context_limit')
        with self._snapshot(generation=generation) as (db, row, _):
            generation = row['generation']
            anchor = self._record(db, generation, 'messages', message_id)
            sort = int(anchor['source_timestamp']) if anchor['utc_timestamp'] else -(1 << 63)
            base = 'SELECT record_id FROM messages WHERE generation=? AND conversation_id=? AND (time_sort,record_id)'
            previous = db.execute(base + '<(?,?) ORDER BY time_sort DESC,record_id DESC LIMIT ?', (generation, anchor['conversation_id'], sort, message_id, before)).fetchall()[::-1]
            following = db.execute(base + '>(?,?) ORDER BY time_sort,record_id LIMIT ?', (generation, anchor['conversation_id'], sort, message_id, after)).fetchall()
            hits = previous + [{'record_id': message_id}] + following
            return self._result(row, self._items(db, generation, 'messages', hits)) | {'message': public_record(anchor)}

    def list_attachments(self, message_id: str | None = None, *, kind: str | None = None, representation: str | None = None, availability: str | None = None, limit: int = DEFAULT_RECORDS, cursor: str | None = None) -> dict:
        """Attachment metadata with an unverified availability label; raises invalid_filter."""
        if message_id is not None:
            valid_id(message_id, 'message')
        if kind not in (None, 'image', 'file') or representation not in (None, 'original', 'thumbnail'):
            raise BridgeError('invalid_filter')
        if availability not in (None, 'local_unverified', 'not_local', 'unsupported_format', 'identity_mismatch'):
            raise BridgeError('invalid_filter')
        return self._list('attachments', limit=limit, cursor=cursor,
                          filters={'message_id': message_id, 'kind': kind, 'representation': representation, 'availability': availability})

    def attachment_ref(self, attachment_id: str, *, generation: int | None = None) -> tuple:
        """Return (AttachmentRef, generation) for one archived attachment; raises attachment_not_found."""
        valid_id(attachment_id, 'attachment')
        with self._snapshot(generation=generation) as (db, row, _):
            record = self._record(db, row['generation'], 'attachments', attachment_id)
            if record.get('file_identity') is not None:
                record['file_identity'] = tuple(record['file_identity'])
            return AttachmentRef(**record), row['generation']
