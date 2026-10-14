"""Authenticated cursors reveal no query text and bind its complete contract."""
import base64
import hashlib
import hmac
import json

from ..core.errors import BridgeError


def binding(method, filters, limit):
    """Digest of the query contract; a cursor only works with the exact method, filters and limit that issued it."""
    raw = json.dumps([method, filters, limit], ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def encode_cursor(key, generation, bound, last):
    """Return a URL-safe token: JSON payload plus HMAC-SHA256, pinned to a generation."""
    raw = json.dumps({'v': 1, 'g': generation, 'b': bound, 'last': last}, separators=(',', ':'), allow_nan=False).encode()
    signature = hmac.digest(key, b'wechat-cursor-v1\0' + raw, 'sha256')
    return base64.urlsafe_b64encode(raw + signature).decode().rstrip('=')


def decode_cursor(token, key, bound):
    """Verify the signature before parsing; return (generation, last).

    Token length is at most 4096 and last holds 1 to 4 items. Every failure raises invalid_cursor.
    """
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 4096:
            raise ValueError
        data = base64.b64decode(token.encode('ascii') + b'=' * (-len(token) % 4), altchars=b'-_', validate=True)
        raw, signature = data[:-32], data[-32:]
        if not hmac.compare_digest(signature, hmac.digest(key, b'wechat-cursor-v1\0' + raw, 'sha256')):
            raise ValueError
        payload = json.loads(raw)
        if (payload['v'] != 1 or type(payload['g']) is not int or payload['g'] < 1
                or payload['b'] != bound or not isinstance(payload['last'], list) or not 1 <= len(payload['last']) <= 4):
            raise ValueError
        return payload['g'], payload['last']
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise BridgeError('invalid_cursor') from None
