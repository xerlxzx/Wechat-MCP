"""Explicit setup orchestration; keys are authenticated before publication."""
from dataclasses import dataclass, field
from pathlib import Path
import time

from ..core.config import read_private_json, write_private_json
from ..core.errors import BridgeError
from ..source.discovery import discover
from ..source.keys import KeyEntry, KeyManifest, verify_keys, publish_verified_manifest
from .prepare import SetupPlan, fingerprint, update_journal
from .recovery import _recover_setup
from .lock import setup_lock, check_active_plan

@dataclass(frozen=True)
class CaptureResult:
    """Capture outcome code, candidate-key file path and what recovery did afterwards."""
    status: str
    candidate_manifest: Path | None = field(default=None, repr=False)
    recovery_status: str = "not_needed"
    key_copies_removed: bool = False


def run_capture(plan: SetupPlan, *, backend=None) -> CaptureResult:
    """Capture and verify keys from a signed copy, then always run recovery, under the setup lock.

    Raises setup_busy, setup_recovery_required, setup_plan_stale or client_exit_required.
    Other failures return as a status code.
    """
    with setup_lock(plan.config):
        check_active_plan(plan)
        return _run_capture(plan, backend=backend)


def _run_capture(plan: SetupPlan, *, backend=None) -> CaptureResult:
    from .system import SystemBackend
    backend = backend or SystemBackend()
    inventory = discover(plan.config)
    journal = read_private_json(plan.directory / "journal.json")
    if (time.time() > plan.expires_at or backend.signature(plan.app_path) != plan.original_signature
            or fingerprint(inventory) != plan.source_fingerprint or journal["stage"] != "prepared"):
        raise BridgeError("setup_plan_stale")
    if backend.is_running(plan.app_path):
        raise BridgeError("client_exit_required")
    # The active marker blocks other plans until recovery clears it.
    write_private_json(plan.config.state_dir / "setup/active.json", {"plan_id": plan.plan_id})
    status, candidate_path = "capture_failed", None
    try:
        update_journal(plan, stage="copy_started")
        backend.copy_bundle(plan.app_path, plan.copy_path)
        update_journal(plan, stage="sign_started")
        backend.sign_copy(plan.copy_path)
        update_journal(plan, stage="capture_started")
        if backend.is_running(plan.app_path):
            raise BridgeError("client_exit_required")
        backend.capture(plan)
        raw = read_private_json(plan.directory / "captured.json")
        current = discover(plan.config)
        if fingerprint(current) != plan.source_fingerprint:
            raise BridgeError("source_changed")
        keys = raw.get("keys")
        if not isinstance(keys, dict): raise BridgeError("invalid_capture_output")
        entries = []
        for db in current.databases:
            row = keys.get(db.relative_path)
            if row is not None:
                try:
                    key = bytes.fromhex(row["enc_key"])
                    entries.append(KeyEntry(db.account_id, db.relative_path, db.database_id, db.salt_hex, key))
                except (TypeError, KeyError, ValueError):
                    raise BridgeError("invalid_capture_output") from None
        manifest = KeyManifest(tuple(entries))
        candidate_path = plan.directory / "candidates.json"
        write_private_json(candidate_path, manifest.private_dict())
        # Captured keys are candidates; only keys that open their database are published.
        coverage = verify_keys(current, manifest, plan.config)
        publish_verified_manifest(manifest, coverage, plan.config)
        status = "verified" if coverage.ready else "partial_coverage"
        update_journal(plan, stage="keys_verified", coverage=coverage.public_dict())
    except KeyboardInterrupt:
        status = "cancelled"
    except BridgeError as error:
        status = error.code
    except OSError:
        status = "setup_io_failed"
    finally:
        # Recovery runs on every exit path so the copy never outlives the capture.
        try:
            update_journal(plan, capture_status=status)
        except (BridgeError, OSError):
            status = "journal_write_failed"
        finally:
            recovered = _recover_setup(plan, backend=backend)
    return CaptureResult(status, candidate_path, recovered.status, recovered.key_copies_removed)
