"""Preserve validated static encoded bytes; never substitute a first frame."""
from dataclasses import dataclass, field
import hashlib
import io
import os
import re
import struct
import time
import warnings

from PIL import Image, UnidentifiedImageError
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from ..core.config import Config, read_private_json, reject_symlinks
from ..core.errors import BridgeError
from ..core.validate import require_int_range

DECODER_VERSION = "static-image-v1"
MAX_IMAGE_BYTES = 52428800
MAX_PIXELS = 40000000


@dataclass(frozen=True)
class ImageKeyMaterial:
    """At most 64 (AES key, XOR byte) candidates for legacy image files; kept out of repr."""
    candidates: tuple[tuple[bytes, int], ...] = field(repr=False)

    def __post_init__(self):
        if (len(self.candidates) > 64 or any(not isinstance(k, bytes) or len(k) != 16
                or type(x) is not int or not 0 <= x <= 255 for k, x in self.candidates)):
            raise BridgeError("invalid_image_material")


@dataclass(frozen=True)
class DecodedImage:
    """Validated static image bytes in their encoded format; never a re-encoded or first-frame copy."""
    data: bytes = field(repr=False)
    format: str
    width: int
    height: int
    decoder_version: str = DECODER_VERSION


def load_image_material(config: Config) -> ImageKeyMaterial | None:
    """Load saved image candidates, or derive them from known per-account container paths.

    Reads at most 4096 directory entries and 32 codes. Derived candidates are
    tried only against the decoder's own validation. Raises invalid_image_material,
    image_material_unreadable, image_material_limit or media_material_symlink.
    """
    path = config.state_dir / "image-material.json"
    if path.exists():
        data = read_private_json(path)
        try:
            if data["version"] != 1:
                raise ValueError
            return ImageKeyMaterial(tuple((bytes.fromhex(r["aes_hex"]), r["xor"]) for r in data["candidates"]))
        except (KeyError, TypeError, ValueError):
            raise BridgeError("invalid_image_material") from None
    # Only known paths in the selected account's container. No home-directory
    # search, SQLCipher key reuse or media-key publication.
    documents = config.account_root.parent.parent
    roots = [documents / "app_data/net/kvcomm", documents / "app_data/ilink/kvcomm"]
    if config.account_root.parent.name == "xwechat_files" and documents.name == "Documents":
        roots += [documents / "xwechat/net/kvcomm",
                  documents.parent / "Library/Application Support/com.tencent.xinWeChat/xwechat/net/kvcomm",
                  documents.parent / "Library/Application Support/com.tencent.xinWeChat/net/kvcomm"]
    codes = set()
    entries = 0
    for root in roots:
        reject_symlinks(root, "media_material_symlink")
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            continue
        except OSError:
            raise BridgeError("image_material_unreadable") from None
        try:
            with os.scandir(fd) as items:
                for item in items:
                    entries += 1
                    if entries > 4096:
                        raise BridgeError("image_material_limit")
                    match = re.fullmatch(r"key_([0-9]{1,10})_.*\.statistic", item.name)
                    if match and item.is_file(follow_symlinks=False) and int(match[1]) < 1 << 32:
                        codes.add(int(match[1]))
                    if len(codes) > 32:
                        raise BridgeError("image_material_limit")
        except OSError:
            raise BridgeError("image_material_unreadable") from None
        finally:
            os.close(fd)
    names = {config.account_root.name}
    # Tested account folders append a device suffix; this candidate is accepted
    # only when padding and the complete static-image decoder validate it.
    if "_" in config.account_root.name:
        names.add(config.account_root.name.rsplit("_", 1)[0])
    candidates = tuple((hashlib.md5((str(code) + name).encode()).hexdigest()[:16].encode(), code & 255)
                       for code in sorted(codes) for name in sorted(names))
    return ImageKeyMaterial(candidates) if candidates else None


def _validate(raw: bytes, *, max_bytes=MAX_IMAGE_BYTES) -> DecodedImage:
    # A wrong key decrypts to noise; full decode is what accepts a candidate key.
    if len(raw) > max_bytes:
        raise BridgeError("content_too_large")
    if raw.startswith((b"wxgf", b"GIF8")):
        raise BridgeError("unsupported_image_format")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ("JPEG", "PNG", "WEBP") or getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                    raise BridgeError("unsupported_image_format")
                if image.width * image.height > MAX_PIXELS:
                    raise BridgeError("image_dimensions_limit")
                # Pillow's multi-channel 8-bit rasters use four-byte pixel
                # storage even for RGB/LA. Budget actual allocation, not only
                # the compact encoded payload or tobytes() channel count.
                storage = {"1": 1, "L": 1, "P": 1, "LA": 4, "PA": 4,
                           "RGB": 4, "RGBA": 4, "RGBX": 4, "CMYK": 4,
                           "YCbCr": 4, "HSV": 4, "I": 4, "F": 4,
                           "I;16": 2, "I;16L": 2, "I;16B": 2}.get(image.mode)
                if storage is None:
                    raise BridgeError("unsupported_image_format")
                if image.width * image.height * storage > max_bytes:
                    raise BridgeError("decoded_image_too_large")
                dimensions, kind = (image.width, image.height), image.format
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
        return DecodedImage(raw, kind, *dimensions)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise BridgeError("malformed_image") from None


def _dat_parts(data):
    if len(data) < 15 or data[14] not in (0, 1):
        raise BridgeError("malformed_image_header")
    size, xor_size = struct.unpack("<II", data[6:14])
    encrypted = size + (16 - size % 16) if size else 0
    if encrypted > len(data) - 15 or xor_size > len(data) - 15 - encrypted or size > MAX_IMAGE_BYTES:
        raise BridgeError("malformed_image_header")
    return size, encrypted, xor_size


def _decrypt_dat(data, key, xor, parts):
    size, encrypted, xor_size = parts
    if encrypted:
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        padded = decryptor.update(data[15:15 + encrypted]) + decryptor.finalize()
        unpad = PKCS7(128).unpadder()
        head = unpad.update(padded) + unpad.finalize()
        if len(head) != size:
            raise ValueError("length")
    else:
        head = b""
    tail = data[15 + encrypted:]
    return head + (tail[:-xor_size] + bytes(v ^ xor for v in tail[-xor_size:]) if xor_size else tail)


def decode_image(source, *, material: ImageKeyMaterial | None, max_bytes: int = MAX_IMAGE_BYTES) -> DecodedImage:
    """Return a validated JPEG, PNG or WEBP from plain, XOR or V1/V2 .dat bytes.

    Animated and WXGF inputs raise unsupported_image_format. Key search stops after 5 s
    (image_decode_timeout). Also raises content_too_large, image_key_unavailable or image_key_mismatch.
    """
    require_int_range(max_bytes, 1, MAX_IMAGE_BYTES, "invalid_content_limit")
    data = source.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise BridgeError("content_too_large")
    if data.startswith((b"\xff\xd8", b"\x89PNG", b"RIFF", b"GIF8", b"wxgf")):
        return _validate(data, max_bytes=max_bytes)
    if data.startswith((b"\x07\x08V1\x08\x07", b"\x07\x08V2\x08\x07")):
        parts = _dat_parts(data)
        candidates = material.candidates if material else ()
        if data[2:4] == b"V1":
            candidates = tuple((b"cfcd208495d565ef", x) for _, x in candidates)
            if not candidates:
                # PNG/JPEG terminal signatures bound V1 XOR recovery to two
                # candidates; complete decode validates the chosen candidate.
                candidates = tuple((b"cfcd208495d565ef", data[-1] ^ end) for end in (0x82, 0xd9))
        if not candidates:
            raise BridgeError("image_key_unavailable")
        deadline = time.monotonic() + 5
        for key, xor in candidates:
            if time.monotonic() > deadline:
                raise BridgeError("image_decode_timeout")
            try:
                raw = _decrypt_dat(data, key, xor, parts)
                return _validate(raw, max_bytes=max_bytes)
            except ValueError:
                continue
            except BridgeError as error:
                if error.code in ("unsupported_image_format", "content_too_large", "image_dimensions_limit", "decoded_image_too_large"):
                    raise
        raise BridgeError("image_key_mismatch")
    if data:
        for signature in (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF"):
            xor = data[0] ^ signature[0]
            if bytes(v ^ xor for v in data[:len(signature)]) == signature:
                return _validate(bytes(v ^ xor for v in data), max_bytes=max_bytes)
    raise BridgeError("unsupported_image_format")
