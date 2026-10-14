"""Idempotent recovery limited to this setup's owned resources."""
from dataclasses import dataclass
import os
import stat
from ..core.config import read_private_json, reject_symlinks
from ..core.errors import BridgeError
from .prepare import SetupPlan, update_journal
from .lock import setup_lock, check_active_plan, clear_active_plan

@dataclass(frozen=True)
class RecoveryResult:
    """Recovery stage reached and whether the raw-key copies were deleted."""
    status: str
    key_copies_removed: bool = False

KEY_COPIES = ("captured.json", "candidates.json")


def _remove_key_copies(plan: SetupPlan) -> bool:
    """Delete the raw-key copies of a verified, recovered plan; True only if both are gone.

    Failure is reported as False, never raised: setup has already succeeded and
    keys.json holds the verified keys.
    """
    directory_fd = None
    try:
        journal = read_private_json(plan.directory / "journal.json")
        coverage = journal.get("coverage")
        if (journal.get("capture_status") != "verified" or journal.get("recovered") is not True
                or not isinstance(coverage, dict) or coverage.get("ready") is not True):
            return False
        # Never delete the only copy: keys.json must already hold the verified keys.
        entries = read_private_json(plan.config.state_dir / "keys.json").get("entries")
        verified = coverage.get("verified_count")
        if not isinstance(entries, list) or not isinstance(verified, int) or not entries or len(entries) < verified:
            return False
        # Unlink by name relative to a pinned directory fd so a swapped path cannot redirect the delete.
        reject_symlinks(plan.directory, "unsafe_state_path")
        directory_fd = os.open(plan.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        removed = True
        for name in KEY_COPIES:
            try:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                removed = False
                continue
            try:
                os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                removed = False
        return removed
    except (BridgeError, OSError):
        return False
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def recover_setup(plan: SetupPlan, *, backend=None) -> RecoveryResult:
    """Stop owned processes, disable the copy and restore the original; safe to repeat.

    Runs under the setup lock. A running process that cannot be stopped yields manual_restore_required.
    """
    with setup_lock(plan.config):
        check_active_plan(plan, allow_same=True)
        return _recover_setup(plan, backend=backend)


def _recover_setup(plan: SetupPlan, *, backend=None) -> RecoveryResult:
    from .system import SystemBackend
    backend = backend or SystemBackend()
    journal = read_private_json(plan.directory / "journal.json")
    if journal.get("recovered"):
        clear_active_plan(plan)
        return RecoveryResult("recovered", _remove_key_copies(plan))
    if not backend.stop_owned(plan):
        update_journal(plan, stage="manual_restore_required")
        return RecoveryResult("manual_restore_required")
    backend.disable_copy(plan)
    if backend.signature(plan.app_path) != plan.original_signature:
        update_journal(plan, stage="original_signature_changed")
        return RecoveryResult("original_signature_changed")
    if plan.restore_original and not backend.is_running(plan.app_path):
        backend.launch_original(plan.app_path)
    update_journal(plan, stage="recovered", recovered=True)
    clear_active_plan(plan)
    return RecoveryResult("recovered", _remove_key_copies(plan))
