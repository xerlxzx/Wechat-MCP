"""Cross-process serialization of setup and recovery in the private state."""
from contextlib import contextmanager
import fcntl
import os
from ..core.config import ensure_private_dir, read_private_json
from ..core.errors import BridgeError


@contextmanager
def setup_lock(config):
    ensure_private_dir(config.state_dir)
    fd = os.open(config.state_dir / ".setup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BridgeError("setup_busy") from None
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def check_active_plan(plan, *, allow_same=False):
    path = plan.config.state_dir / "setup/active.json"
    if path.exists():
        active = read_private_json(path)
        if not allow_same or active.get("plan_id") != plan.plan_id:
            raise BridgeError("setup_recovery_required")


def clear_active_plan(plan):
    path = plan.config.state_dir / "setup/active.json"
    if path.exists() and read_private_json(path).get("plan_id") == plan.plan_id:
        path.unlink()
