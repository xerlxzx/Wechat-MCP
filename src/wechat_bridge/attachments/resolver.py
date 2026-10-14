"""No-follow descriptor reads pinned beneath the selected account's media roots."""
import errno
import hashlib
import os
from pathlib import PurePosixPath
import stat

from ..core.config import reject_symlinks
from ..core.errors import BridgeError
from ..core.validate import require_int_range
from .models import AttachmentResult
from .images import decode_image, load_image_material, MAX_IMAGE_BYTES

MAX_FILE_BYTES = 209715200


def file_identity(s):
    """Tuple of device, inode, size, mtime and ctime used to detect a swapped or rewritten file."""
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def open_media(config, relative):
    """Open a regular file under msg/file or msg/attach of the account root, one O_NOFOLLOW step per component.

    Descriptor-relative opens pin the path against symlink swaps. Raises attachment_path_invalid.
    """
    if not isinstance(relative, str) or "\0" in relative or "\\" in relative or len(relative) > 4096:
        raise BridgeError("attachment_path_invalid")
    parts = PurePosixPath(relative)
    if (parts.is_absolute() or ".." in parts.parts or str(parts) != relative or len(parts.parts) < 3
            or parts.parts[:2] not in (("msg", "file"), ("msg", "attach"))):
        raise BridgeError("attachment_path_invalid")
    reject_symlinks(config.account_root, "attachment_path_invalid")
    directory = os.open(config.account_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory); directory = child
        fd = os.open(parts.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise BridgeError("attachment_path_invalid")
        return os.fdopen(fd, "rb")
    finally:
        os.close(directory)


def hash_stream(stream, max_bytes):
    """Return (size, sha256, md5) and rewind; raises content_too_large past max_bytes."""
    sha = hashlib.sha256(); md5 = hashlib.md5(); size = 0
    while chunk := stream.read(65536):
        size += len(chunk)
        if size > max_bytes:
            raise BridgeError("content_too_large")
        sha.update(chunk); md5.update(chunk)
    stream.seek(0)
    return size, sha.hexdigest(), md5.hexdigest()


class VerifiedStream:
    """Check the pinned descriptor around every consumer read."""
    def __init__(self, stream, identity):
        self._stream, self._identity = stream, identity

    def _check(self):
        if file_identity(os.fstat(self._stream.fileno())) != self._identity:
            raise BridgeError("source_changed")

    def read(self, size=-1):
        """Read after and before verifying the descriptor still matches; raises source_changed."""
        self._check()
        data = self._stream.read(size)
        self._check()
        return data

    def seek(self, *args):
        self._check()
        return self._stream.seek(*args)

    def tell(self):
        return self._stream.tell()

    @property
    def closed(self):
        return self._stream.closed

    def close(self):
        self._stream.close()


def resolve_attachment(ref, config, *, decode=True, max_source_bytes=None):
    """Resolve a reference to verified bytes or an explicit non-available result.

    Failures map to availability and diagnostic_code instead of raising, except
    content_too_large and invalid_content_limit. Size and digest are checked
    against the mapping, and the file identity is re-checked after hashing and decoding.
    """
    if max_source_bytes is not None:
        require_int_range(max_source_bytes, 1, MAX_FILE_BYTES, 'invalid_content_limit')
    result = AttachmentResult(ref.attachment_id, "not_local", ref.representation, diagnostic_code=ref.diagnostic_code)
    if ref.mapping_provenance == "conflicting":
        result.availability = "identity_mismatch"; result.diagnostic_code = "attachment_mapping_conflicting"
        return result
    if ref.mapping_provenance not in ("resource_hardlink", "resource_layout", "explicit_resource"):
        result.availability = "unsupported_format"; result.diagnostic_code = ref.diagnostic_code or "attachment_mapping_unsupported"
        return result
    if ref.relative_path is None or ref.declared_local is False:
        result.diagnostic_code = ref.diagnostic_code or "attachment_not_local"
        return result
    stream = None
    try:
        stream = open_media(config, ref.relative_path)
        # Pinned identity: any change between here and the final read becomes source_changed.
        before = file_identity(os.fstat(stream.fileno()))
        if ref.file_identity is not None and before != ref.file_identity:
            raise BridgeError("attachment_identity_mismatch")
        # Reject by stat size before hashing so an oversized file is never read.
        budget = MAX_IMAGE_BYTES if ref.kind == "image" else MAX_FILE_BYTES
        if max_source_bytes is not None:
            budget = min(budget, max_source_bytes)
        if before[2] > budget:
            raise BridgeError("content_too_large")
        size, sha, digest = hash_stream(stream, budget)
        if before != file_identity(os.fstat(stream.fileno())):
            raise BridgeError("source_changed")
        if (ref.expected_size is not None and size != ref.expected_size
                or ref.expected_digest is not None and digest != ref.expected_digest.lower()):
            raise BridgeError("attachment_identity_mismatch")
        result.source_sha256 = sha
        result.verification_basis = "size_and_digest" if ref.expected_digest else "direct_mapping_and_size" if ref.expected_size is not None else "direct_mapping"
        # Thumbnail suffixes stay thumbnails; they never satisfy an original request.
        representation = "thumbnail" if ref.relative_path.endswith(("_t.dat", "_h.dat")) else ref.representation
        result.representation = representation
        if ref.kind == "image" and decode:
            result.decoded = decode_image(stream, material=load_image_material(config))
            if before != file_identity(os.fstat(stream.fileno())):
                raise BridgeError("source_changed")
            result.decoded_sha256 = hashlib.sha256(result.decoded.data).hexdigest()
        elif ref.kind not in ("image", "file"):
            raise BridgeError("unsupported_attachment_kind")
        stream.seek(0)
        result.availability = "thumbnail_only" if representation == "thumbnail" else "original_available"
        result.handle = VerifiedStream(stream, before); stream = None
        return result
    except FileNotFoundError:
        result.diagnostic_code = "attachment_not_local"
    except OSError as error:
        result.availability = "identity_mismatch" if error.errno in (errno.ELOOP, errno.ENOTDIR) else "read_failed"
        result.diagnostic_code = "attachment_path_invalid" if result.availability == "identity_mismatch" else "attachment_read_failed"
    except BridgeError as error:
        if error.code == "content_too_large":
            raise
        result.availability = ("identity_mismatch" if error.code in ("attachment_path_invalid", "attachment_identity_mismatch")
                               else "read_failed" if error.code == "source_changed" else "unsupported_format")
        result.diagnostic_code = error.code
        result.decoded = None; result.decoded_sha256 = None
    finally:
        if stream is not None:
            stream.close()
    return result
