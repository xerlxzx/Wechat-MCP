"""Persist a concrete, expiring setup plan without changing the application."""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time
import uuid

from ..core.config import Config, ensure_private_dir, read_private_json, write_private_json
from ..core.errors import BridgeError
from ..source.discovery import Inventory

@dataclass(frozen=True)
class SetupPlan:
    """Expiring, concrete setup plan bound to the app signature and the source fingerprint."""
    plan_id: str
    config: Config = field(repr=False)
    directory: Path = field(repr=False)
    app_path: Path = field(repr=False)
    original_signature: str = field(repr=False)
    source_fingerprint: str = field(repr=False)
    expires_at: float
    restore_original: bool

    @property
    def copy_path(self): return self.directory / "WeChat-trial.app"

    def public_dict(self):
        """User-facing plan summary; no paths."""
        return {"plan_id": self.plan_id, "status": "prepared", "expires_at": self.expires_at,
                "requires_normal_client_exit": True, "may_require_phone_confirmation": True,
                "modifies_installed_application": False, "capture_timeout_seconds": 150,
                "restore_original": self.restore_original}


def fingerprint(inventory: Inventory) -> str:
    """Digest of database IDs, salts and file identities; a plan is stale if this changes."""
    rows = [(d.relative_path, d.database_id, d.salt_hex, d.file_identity) for d in inventory.databases]
    return hashlib.sha256(json.dumps([inventory.account_id, rows], sort_keys=True).encode()).hexdigest()


def prepare_setup(config: Config, inventory: Inventory, *, backend=None) -> SetupPlan:
    """Write a one-hour plan and journal without touching the application.

    Raises setup_prerequisites_missing.
    """
    from .system import SystemBackend
    backend = backend or SystemBackend()
    ready = backend.preflight()
    if not ready.get("platform_ready") or not ready.get("lldb_ready"):
        raise BridgeError("setup_prerequisites_missing")
    signature = backend.signature(backend.app)
    plan_id = str(uuid.uuid4())
    parent = config.state_dir / "setup"; ensure_private_dir(parent)
    directory = parent / plan_id; ensure_private_dir(directory)
    plan = SetupPlan(plan_id, config, directory, backend.app, signature, fingerprint(inventory),
                     time.time() + 3600, backend.is_running(backend.app))
    write_private_json(directory / "plan.json", {
        "version": 1, "plan_id": plan_id, "app_path": str(plan.app_path),
        "original_signature": signature, "source_fingerprint": plan.source_fingerprint,
        "expires_at": plan.expires_at, "restore_original": plan.restore_original,
        "actions": ["normal_client_exit_by_user", "copy_installed_bundle", "sign_copy_only",
                    "capture_selected_database_keys", "verify_and_publish_candidates",
                    "close_owned_process", "disable_copy", "restore_original_if_requested"]})
    write_private_json(directory / "journal.json", {"stage": "prepared", "recovered": False})
    return plan


def load_setup(config: Config, plan_id: str) -> SetupPlan:
    """Load a saved plan by UUID; the app path must be the standard install. Raises invalid_setup_plan."""
    try:
        if str(uuid.UUID(plan_id)) != plan_id: raise ValueError
        directory = config.state_dir / "setup" / plan_id
        data = read_private_json(directory / "plan.json")
        if data["version"] != 1 or data["plan_id"] != plan_id: raise ValueError
        if data["app_path"] != "/Applications/WeChat.app": raise ValueError
        return SetupPlan(plan_id, config, directory, Path(data["app_path"]), data["original_signature"],
                         data["source_fingerprint"], float(data["expires_at"]), bool(data["restore_original"]))
    except (KeyError, ValueError, TypeError):
        raise BridgeError("invalid_setup_plan") from None


def update_journal(plan: SetupPlan, **changes):
    """Merge fields into the plan's private journal atomically."""
    path = plan.directory / "journal.json"
    data = read_private_json(path)
    data.update(changes)
    write_private_json(path, data)
