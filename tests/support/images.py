"""Pure image-byte fixture generators shared across unit and integration tests.

Support modules must not import from tests/unit; these generators live here so
tests/support/proof.py and the integration suites can reuse them without a
backwards dependency. Byte output is identical to the previous definitions.
"""
import io
import struct


def png():
    from PIL import Image
    out = io.BytesIO(); Image.new("RGB", (3, 2), (10, 20, 30)).save(out, "PNG")
    return out.getvalue()


def dat(raw, key=b'0123456789abcdef', xor=77, variant=b'V2'):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7
    plain_len = min(32, len(raw)); tail_len = min(8, len(raw) - plain_len)
    pad = PKCS7(128).padder(); padded = pad.update(raw[:plain_len]) + pad.finalize()
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    tail = raw[plain_len:]; middle = tail[:-tail_len] if tail_len else tail
    return b'\x07\x08' + variant + b'\x08\x07' + struct.pack('<II', plain_len, tail_len) + b'\0' + enc.update(padded) + enc.finalize() + middle + bytes(b ^ xor for b in tail[-tail_len:])


def corrupt_png():
    raw = bytearray(png()); offset = 8
    while offset < len(raw):
        size = int.from_bytes(raw[offset:offset + 4], 'big')
        if raw[offset + 4:offset + 8] == b'IDAT':
            raw[offset + 8 + size] ^= 1; return bytes(raw)
        offset += 12 + size
    raise AssertionError('fixture has no IDAT')
