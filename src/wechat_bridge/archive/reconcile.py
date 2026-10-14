"""Detect DB/WAL incarnation and metadata changes without reading content."""
import hashlib
import os
import stat

from ..core.config import reject_symlinks
from ..core.errors import BridgeError
from .store import encode


def source_fingerprint(config):
    """Hash path, inode, size and timestamps of every .db and .db-wal file; reads no content.

    Walks with O_NOFOLLOW at most 4096 entries. Raises source_symlink,
    source_inventory_limit or source_unreadable.
    """
    root = config.account_root / 'db_storage'
    reject_symlinks(root, 'source_symlink')
    count = 0
    files = []

    def walk(fd, prefix):
        nonlocal count
        with os.scandir(fd) as entries:
            for entry in entries:
                count += 1
                if count > 4096:
                    raise BridgeError('source_inventory_limit')
                if entry.is_symlink():
                    raise BridgeError('source_symlink')
                info = entry.stat(follow_symlinks=False)
                relative = prefix + entry.name
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        walk(child, relative + '/')
                    finally:
                        os.close(child)
                elif entry.name.endswith(('.db', '.db-wal')):
                    if not stat.S_ISREG(info.st_mode):
                        raise BridgeError('source_unreadable')
                    files.append((relative, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))

    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            walk(fd, '')
        finally:
            os.close(fd)
    except OSError:
        raise BridgeError('source_unreadable') from None
    if not files:
        raise BridgeError('source_unreadable')
    return hashlib.sha256(encode(sorted(files)).encode()).hexdigest()
