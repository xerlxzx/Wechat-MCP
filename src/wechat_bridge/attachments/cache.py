"""Owner-only atomic caches keyed by source content and decoder version."""
import hashlib
import os
import re
import tempfile
from ..core.config import ensure_private_dir, reject_symlinks
from ..core.errors import BridgeError


def cache_decoded(result, source_sha256, config):
    if not isinstance(source_sha256, str) or not re.fullmatch("[0-9a-f]{64}", source_sha256):
        raise BridgeError("invalid_content_identity")
    root = config.state_dir / "decoded"
    ensure_private_dir(root)
    key = hashlib.sha256((source_sha256 + ":" + result.decoder_version).encode()).hexdigest()
    path = root / (key + "." + result.format.lower())
    reject_symlinks(path, "unsafe_cache_path")
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=root)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(result.data); out.flush(); os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return path
