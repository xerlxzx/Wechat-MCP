"""Owner-only configuration and atomic private state."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

from .errors import BridgeError

DEFAULT_STATE = Path.home() / "Library/Application Support/WechatBridge"
DEFAULT_ACCOUNTS = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
DEFAULT_LIBRARY = Path("/opt/homebrew/opt/sqlcipher/lib/libsqlcipher.dylib")
if not DEFAULT_LIBRARY.is_file():
    DEFAULT_LIBRARY = Path.home() / ".local/share/wechat-bridge/sqlcipher-4.19.0/lib/libsqlcipher.dylib"

@dataclass(frozen=True)
class Config:
    """Resolved state directory, account root and SQLCipher library. Paths stay out of repr."""
    state_dir: Path = field(repr=False)
    account_root: Path = field(repr=False)
    sqlcipher_library: Path = field(default=DEFAULT_LIBRARY, repr=False)


def reject_symlinks(path: Path, code: str) -> None:
    """Raise `code` if the path or any parent is a symlink."""
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise BridgeError(code)


def ensure_private_dir(path: Path) -> None:
    """Create an owner-only (0700) directory or verify an existing one.

    Raises unsafe_state_path, private_permissions or state_unreadable.
    """
    reject_symlinks(path, "unsafe_state_path")
    try:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
        s = path.stat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
            raise BridgeError("private_permissions")
    except OSError:
        raise BridgeError("state_unreadable") from None


def check_private_fd(fd):
    """Reject an open fd that is not a private (owner-only, non-group/world) regular file.

    Takes a descriptor, not a path, so it inspects exactly the opened object
    (the O_NOFOLLOW open already closed the symlink/TOCTOU gap). Raises
    private_permissions; os.fstat OSError is left to propagate to the caller.
    """
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BridgeError("private_permissions")
    return info


def read_private_json(path: Path) -> dict:
    """Read a private JSON object of at most 8 MiB through an O_NOFOLLOW descriptor.

    Raises unsafe_state_path, private_permissions, state_too_large or invalid_private_state.
    """
    reject_symlinks(path, "unsafe_state_path")
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        s = check_private_fd(fd)
        if s.st_size > 8 * 1024 * 1024:
            raise BridgeError("state_too_large")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = None
            value = json.load(stream)
        if not isinstance(value, dict):
            raise BridgeError("invalid_private_state")
        return value
    except (OSError, ValueError, UnicodeError):
        raise BridgeError("invalid_private_state") from None
    finally:
        if fd is not None:
            os.close(fd)


def write_private_json(path: Path, value: dict) -> None:
    """Replace a private JSON file atomically: temp file, fsync, rename, directory fsync.

    Readers see the old or the new file, never a partial one. Raises state_write_failed.
    """
    ensure_private_dir(path.parent)
    reject_symlinks(path, "unsafe_state_path")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        raise BridgeError("state_write_failed") from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


@contextmanager
def state_lock(config: Config):
    """Hold the exclusive state-directory flock for the block; blocks until acquired."""
    ensure_private_dir(config.state_dir)
    fd = os.open(config.state_dir / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def select_account(parent: Path = DEFAULT_ACCOUNTS, name: str | None = None) -> Path:
    """Return the one account root that has db_storage, optionally filtered by name.

    Raises source_unreadable, source_not_found, account_selection_required or source_symlink.
    """
    try:
        roots = sorted(p for p in parent.iterdir() if p.is_dir() and (p / "db_storage").is_dir())
    except OSError:
        raise BridgeError("source_unreadable") from None
    if name is not None:
        roots = [p for p in roots if p.name == name]
    if len(roots) != 1:
        raise BridgeError("account_selection_required" if roots else "source_not_found")
    reject_symlinks(roots[0], "source_symlink")
    return roots[0]


def create_config(state_dir: Path, account_root: Path | None = None, *,
                  sqlcipher_library: Path = DEFAULT_LIBRARY, repo_root: Path | None = None) -> Config:
    """Load the saved config or create it once; private state may not live inside the repository.

    Raises state_inside_repository, account_selection_conflict, source_not_found or source_symlink.
    """
    state_dir = state_dir.expanduser().absolute()
    repo_root = repo_root or Path(__file__).resolve().parents[3]
    if state_dir.resolve().is_relative_to(repo_root.resolve()):
        raise BridgeError("state_inside_repository")
    ensure_private_dir(state_dir)
    config_path = state_dir / "config.json"
    if config_path.exists():
        cfg = load_config(state_dir)
        if account_root is not None and cfg.account_root != account_root.resolve():
            raise BridgeError("account_selection_conflict")
        return cfg
    root = account_root or select_account()
    reject_symlinks(root, "source_symlink")
    if not (root / "db_storage").is_dir():
        raise BridgeError("source_not_found")
    cfg = Config(state_dir, root.resolve(), sqlcipher_library.absolute())
    write_private_json(config_path, {"version": 1, "account_root": str(cfg.account_root),
                                    "sqlcipher_library": str(cfg.sqlcipher_library)})
    return cfg


def load_config(state_dir: Path = DEFAULT_STATE) -> Config:
    """Read config.json with strict version and absolute-path checks; raises invalid_private_state."""
    ensure_private_dir(state_dir)
    data = read_private_json(state_dir / "config.json")
    try:
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError
        root, library = Path(data["account_root"]), Path(data["sqlcipher_library"])
        if not root.is_absolute() or not library.is_absolute():
            raise ValueError
        reject_symlinks(root, "source_symlink")
        return Config(state_dir, root, library)
    except (KeyError, TypeError, ValueError):
        raise BridgeError("invalid_private_state") from None
