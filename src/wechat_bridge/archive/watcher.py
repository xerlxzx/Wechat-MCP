"""Metadata polling plus periodic full reconciliation; independent of queries."""
import fcntl
import math
import os
import time

from ..adapters.wechat_4_1 import open_adapter
from ..core.config import check_private_fd
from ..core.errors import BridgeError
from .importer import refresh_archive
from .reconcile import source_fingerprint
from .store import Archive

# Codes that only a keys.json rewrite (setup or verify-keys) normally clears.
# A transient source read can also leave coverage incomplete, so a blocked
# watcher still retries at this fixed ceiling.
KEY_STATE_ERRORS = frozenset({'setup_required', 'key_coverage_incomplete'})
# Persistent failures that neither a source write nor a key rewrite clears: the
# installed build or the archive's account only changes with a code, config or
# user action. They share the key-state backoff so a full discover/verify/schema
# read is not repeated every reconcile cycle.
PERSISTENT_ERRORS = frozenset({'unsupported_build', 'archive_account_mismatch'})
BLOCKED_ERRORS = KEY_STATE_ERRORS | PERSISTENT_ERRORS
BLOCKED_RETRY_SECONDS = 3600.0
LOCK_RETRY_SECONDS = 30.0
# Every refresh is a full import that rewrites the whole archive. The periodic
# reconcile is a safety net for a change the metadata fingerprint missed, so while
# the source stays untouched it backs off (doubling up to this ceiling) instead of
# rewriting an unchanged archive every reconcile_seconds. A fingerprint change
# restores the short interval.
RECONCILE_CEILING_SECONDS = 3600.0
# Whatever triggers a refresh (a source write, a periodic tick, a retry after a
# failure, including codes not in BLOCKED_ERRORS), the watcher then rests so it
# never works more than this fraction of the time. Without the cap a refresh
# that outlasts the debounce or the reconcile interval runs back to back.
MAX_DUTY_CYCLE = 0.2


def key_state(config):
    """Stat signature of keys.json (None if absent, 'unreadable' on error); reads no key content."""
    try:
        info = os.stat(config.state_dir / 'keys.json', follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        return 'unreadable'
    return info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _try_watcher_lock(config):
    """Non-blocking exclusive flock; returns the fd or None when another process holds it."""
    fd = os.open(config.state_dir / '.archive-watcher.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        check_private_fd(fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException as error:
        os.close(fd)
        if isinstance(error, BlockingIOError):
            return None
        raise
    return fd


class ArchiveWatcher:
    """Poll the source fingerprint and refresh the archive after a debounce or on a fixed interval.

    Needs an existing active generation (archive_not_ready) and intervals in (0, 3600] seconds.
    An idle reconcile doubles the next interval up to RECONCILE_CEILING_SECONDS, a source change
    restores reconcile_seconds, and each refresh is followed by rest that caps the duty cycle at
    MAX_DUTY_CYCLE.
    """
    def __init__(self, config, *, debounce_seconds=1.0, reconcile_seconds=60.0,
                 adapter_factory=open_adapter, clock=time.monotonic):
        for value in (debounce_seconds, reconcile_seconds):
            if type(value) not in (float, int) or not math.isfinite(value) or not 0 < value <= 3600:
                raise BridgeError('invalid_watch_interval')
        self.config, self.adapter_factory = config, adapter_factory
        self.debounce_seconds, self.reconcile_seconds = debounce_seconds, reconcile_seconds
        self._clock = clock
        status = Archive(config).status()
        if status['active_generation'] is None:
            raise BridgeError('archive_not_ready')
        self._seen = status['source_fingerprint']
        self._pending_since = None
        self._last_attempt = None
        self._not_before = -math.inf
        self._reconcile_interval = reconcile_seconds
        self._blocked = False
        self._blocked_on = None

    def poll(self, *, now=None, cancel_event=None):
        """Run at most one refresh if one is due; return its status, or None when idle."""
        now = self._clock() if now is None else now
        if self._last_attempt is None:
            self._last_attempt = now
        try:
            current = source_fingerprint(self.config)
        except BridgeError as error:
            current = 'error:' + error.code
        if current != self._seen:
            self._seen = current
            self._pending_since = now
            self._reconcile_interval = self.reconcile_seconds
        periodic = now - self._last_attempt >= self._reconcile_interval
        changed = self._pending_since is not None and now - self._pending_since >= self.debounce_seconds
        if not periodic and not changed:
            return None
        if now < self._not_before:
            return None
        if (self._blocked and key_state(self.config) == self._blocked_on
                and now - self._last_attempt < BLOCKED_RETRY_SECONDS):
            return None
        self._last_attempt, self._pending_since = now, None
        blocked_on = key_state(self.config)
        started = self._clock()
        try:
            result = refresh_archive(self.config, adapter_factory=self.adapter_factory, cancel_event=cancel_event)
        except BridgeError as error:
            return {'status': 'deferred', 'error': error.code}
        finally:
            self._not_before = now + (self._clock() - started) / MAX_DUTY_CYCLE
        self._blocked = result['status'] == 'failed' and result['error'] in BLOCKED_ERRORS
        self._blocked_on = blocked_on
        if result['status'] == 'complete':
            self._seen = Archive(self.config).status()['source_fingerprint']
            if not changed:  # An idle reconcile found nothing new: wait longer next time.
                self._reconcile_interval = min(2 * self._reconcile_interval, RECONCILE_CEILING_SECONDS)
        return result

    def run(self, stop_event):
        """Poll until stop_event is set; the same event cancels a refresh in flight."""
        while not stop_event.is_set():
            self.poll(cancel_event=stop_event)
            stop_event.wait(min(1.0, self.debounce_seconds))


def run_exclusive(config, stop_event, *, retry_seconds=LOCK_RETRY_SECONDS, **kwargs):
    """Run one watcher per state dir; other callers wait and take over if the holder exits.

    The flock dies with its process, so a crashed holder never leaves a stale lock.
    """
    while not stop_event.is_set():
        try:
            fd = _try_watcher_lock(config)
        except (BridgeError, OSError):
            fd = None  # Fail closed: serve queries without a watcher.
        if fd is not None:
            try:
                ArchiveWatcher(config, **kwargs).run(stop_event)
            except BridgeError:
                pass  # Archive not ready; the initial import is explicit.
            finally:
                os.close(fd)
            return
        stop_event.wait(retry_seconds)
