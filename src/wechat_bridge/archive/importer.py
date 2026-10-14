"""Full reconciliation; publish only a completed, stable-source generation."""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import os

from ..adapters.wechat_4_1 import open_adapter
from ..adapters.schema import MSG_TABLE
from ..core.config import check_private_fd, read_private_json
from ..core.errors import BridgeError
from ..source.discovery import discover
from ..source.discovery import MESSAGE_SHARD
from .reconcile import source_fingerprint
from .store import Archive


@contextmanager
def import_lock(config):
    """Hold a non-blocking exclusive refresh lock; raises archive_refresh_in_progress if taken."""
    fd = os.open(config.state_dir / '.archive-import.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        check_private_fd(fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BridgeError('archive_refresh_in_progress') from None
        yield
    finally:
        os.close(fd)


def _checkpoint(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise BridgeError('operation_cancelled')


def _write(archive, generation, kind, records, cancel_event=None):
    batch = []
    for record in records:
        _checkpoint(cancel_event)
        batch.append(record)
        if len(batch) == 200:
            archive.write_records(generation, kind, batch); batch = []
    if batch:
        archive.write_records(generation, kind, batch)


def refresh_archive(config, *, adapter_factory=open_adapter, cancel_event=None):
    """Import the source into a new generation and publish it only if every check passes.

    Publication needs full table coverage, all established shards present and an
    unchanged source fingerprint. Failures mark the generation failed and return a
    redacted status with the error code; KeyboardInterrupt marks it cancelled and re-raises.
    """
    archive = Archive(config)
    with import_lock(config):
        registry_path = config.state_dir / 'inventory.json'
        registry = read_private_json(registry_path) if registry_path.exists() else None
        account_id = registry['account_id'] if registry else discover(config).account_id
        known_shards = {path for path in (registry or {}).get('databases', {})
                        if MESSAGE_SHARD.fullmatch(path)}
        generation = archive.begin_generation(account_id)
        try:
            _checkpoint(cancel_event)
            from ..search.index import ensure_index
            ensure_index(archive, building_generation=generation)
            before = source_fingerprint(config)
            adapter = adapter_factory(config)
            if adapter.inventory.account_id != account_id:
                raise BridgeError('archive_account_mismatch')
            current_paths = {db.relative_path for db in adapter.inventory.databases}
            # Discovery retains established paths across absences. A missing
            # shard cannot establish deletion of every row it used to contain.
            if not known_shards.issubset(current_paths):
                raise BridgeError('source_shard_missing')
            _write(archive, generation, 'contacts', (asdict(r) for r in adapter.contacts()), cancel_event)
            conversations = tuple(adapter.conversations())
            _write(archive, generation, 'conversations', (asdict(r) for r in conversations), cancel_event)
            missing = []
            covered_tables = set()
            for conversation in conversations:
                _checkpoint(cancel_event)
                covered_tables.update((schema.database_id, table)
                                      for schema, table in adapter._message_tables(conversation.source_username))
                if conversation.kind == 'group' and conversation.membership_status == 'recorded':
                    _write(archive, generation, 'membership',
                           (asdict(r) | {'record_id': conversation.conversation_id + ':' + str(i)}
                            for i, r in enumerate(adapter.memberships(conversation.conversation_id))), cancel_event)
                try:
                    for batch in adapter.message_batches(conversation.conversation_id, batch_size=200):
                        _checkpoint(cancel_event)
                        archive.write_records(generation, 'messages', [asdict(m) for m in batch])
                        _write(archive, generation, 'replies', (asdict(m.reply) for m in batch if m.reply), cancel_event)
                        _write(archive, generation, 'attachments',
                               (asdict(ref) for m in batch for ref in adapter.attachment_refs(m)), cancel_event)
                except BridgeError as error:
                    if error.code != 'conversation_has_no_local_message_table':
                        raise
                    missing.append(conversation.conversation_id)
            observed_tables = {(schema.database_id, table.name)
                               for schema in adapter.schemas if MESSAGE_SHARD.fullmatch(schema.relative_path)
                               for table in schema.tables if MSG_TABLE.fullmatch(table.name)}
            # Every message table in the source must map to a conversation; none may be dropped silently.
            if observed_tables != covered_tables:
                raise BridgeError('message_table_mapping_incomplete')
            _write(archive, generation, 'observations',
                   ({'database_id': key, 'observed_at': value} for key, value in sorted(adapter.observations.items())), cancel_event)
            # Re-fingerprint after reading: a source that moved mid-import is not a snapshot.
            after = source_fingerprint(config)
            if before != after:
                raise BridgeError('source_changed')
            _checkpoint(cancel_event)
            metadata = {'source_fingerprint': after, 'adapter_status': adapter.parse_status,
                        'source_observations': dict(adapter.observations),
                        'coverage': {'missing_message_tables': len(missing), 'missing_conversation_ids': missing,
                                     'mapped_message_tables': len(covered_tables),
                                     'database_count': len(adapter.inventory.databases), 'account_wide_atomic_snapshot': False,
                                     'auxiliary_storage_unhandled': 'auxiliary_storage_unhandled' in adapter.inventory.issues,
                                     'complete_phone_history': False}}
            archive.publish(generation, metadata)
        except KeyboardInterrupt:
            archive.fail(generation, 'operation_cancelled')
            raise
        except Exception as error:
            code = error.code if isinstance(error, BridgeError) else 'archive_refresh_failed'
            archive.fail(generation, code)
            return _public_status(archive.status()) | {'status': 'failed', 'error': code}
        return _public_status(archive.status()) | {'status': 'complete'}


def _public_status(status):
    # Drops the fingerprint and per-conversation IDs from caller-visible status.
    result = {key: value for key, value in status.items() if key != 'source_fingerprint'}
    result['coverage'] = {key: value for key, value in status.get('coverage', {}).items()
                          if key != 'missing_conversation_ids'}
    return result


def archive_status(config):
    """Redacted archive status plus staleness against the current source fingerprint."""
    archive = Archive(config)
    status = archive.status()
    try:
        current = source_fingerprint(config)
        changed = status['source_fingerprint'] is not None and current != status['source_fingerprint']
        error = None
    except BridgeError as issue:
        changed, error = True, issue.code
    stale = status['active_generation'] is None or changed or bool(status['last_refresh_error']) or error is not None
    return _public_status(status) | {'source_change_detected': changed, 'source_error': error,
                                    'stale': stale, 'ready_for_local_queries': status['active_generation'] is not None}
