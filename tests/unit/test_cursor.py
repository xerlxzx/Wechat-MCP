import base64
import hashlib
import hmac
import json

import pytest

KEY = b"k" * 32


def forge(payload, key=KEY):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    sig = hmac.digest(key, b"wechat-cursor-v1\0" + raw, "sha256")
    return base64.urlsafe_b64encode(raw + sig).decode().rstrip("=")


def test_cursor_roundtrips_generation_and_position():
    from wechat_bridge.search.cursor import binding, decode_cursor, encode_cursor
    bound = binding("search", {"q": "发票"}, 10)
    token = encode_cursor(KEY, 7, bound, [1.5, "id"])
    assert decode_cursor(token, KEY, bound) == (7, [1.5, "id"])
    assert "发票" not in base64.urlsafe_b64decode(token + "==").decode("latin-1")


def test_binding_changes_with_method_filters_and_limit():
    from wechat_bridge.search.cursor import binding
    base = binding("m", {"a": 1}, 5)
    assert len({base, binding("n", {"a": 1}, 5), binding("m", {"a": 2}, 5), binding("m", {"a": 1}, 6)}) == 4
    assert binding("m", {"a": 1, "b": 2}, 5) == binding("m", {"b": 2, "a": 1}, 5)


@pytest.mark.parametrize("mutate", [
    lambda t, b: t[:-2] + ("AA" if t[-2:] != "AA" else "BB"),   # signature bit-flip
    lambda t, b: t[: len(t) // 2],                              # truncated
    lambda t, b: "",
    lambda t, b: "!!!not-base64!!!",
    lambda t, b: "A" * 5000,                                    # over the 4096 bound
    lambda t, b: None,
    lambda t, b: 12,
    lambda t, b: "发" + t,                                  # non-ascii
])
def test_tampered_or_malformed_tokens_are_invalid_cursor(mutate):
    from wechat_bridge.core.errors import BridgeError
    from wechat_bridge.search.cursor import decode_cursor, encode_cursor
    token = encode_cursor(KEY, 1, "bound", [1])
    with pytest.raises(BridgeError) as info:
        decode_cursor(mutate(token, "bound"), KEY, "bound")
    assert info.value.code == "invalid_cursor" and info.value.__cause__ is None


def test_wrong_key_and_wrong_binding_are_rejected():
    from wechat_bridge.core.errors import BridgeError
    from wechat_bridge.search.cursor import decode_cursor, encode_cursor
    token = encode_cursor(KEY, 1, "bound", [1])
    with pytest.raises(BridgeError, match="invalid_cursor"):
        decode_cursor(token, b"z" * 32, "bound")
    with pytest.raises(BridgeError, match="invalid_cursor"):
        decode_cursor(token, KEY, "other")


@pytest.mark.parametrize("payload", [
    {"v": 2, "g": 1, "b": "bound", "last": [1]},
    {"v": 1, "g": 0, "b": "bound", "last": [1]},
    {"v": 1, "g": True, "b": "bound", "last": [1]},
    {"v": 1, "g": "1", "b": "bound", "last": [1]},
    {"v": 1, "g": 1, "b": "bound", "last": []},
    {"v": 1, "g": 1, "b": "bound", "last": [1, 2, 3, 4, 5]},
    {"v": 1, "g": 1, "b": "bound", "last": "x"},
    {"v": 1, "g": 1, "b": "bound"},
])
def test_validly_signed_but_ill_shaped_payloads_are_rejected(payload):
    from wechat_bridge.core.errors import BridgeError
    from wechat_bridge.search.cursor import decode_cursor
    with pytest.raises(BridgeError, match="invalid_cursor"):
        decode_cursor(forge(payload), KEY, "bound")


def test_forge_helper_accepts_a_valid_payload():
    from wechat_bridge.search.cursor import decode_cursor
    assert decode_cursor(forge({"v": 1, "g": 3, "b": "bound", "last": [1, 2, 3, 4]}), KEY, "bound") == (3, [1, 2, 3, 4])
