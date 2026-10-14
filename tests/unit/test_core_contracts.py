"""Public limits, strict validation and BridgeError shape in core/."""
import pytest


def test_public_limits_match_the_documented_contract():
    from wechat_bridge.core import limits
    assert (limits.DEFAULT_RECORDS, limits.MAX_RECORDS, limits.MAX_CONTEXT_NEIGHBORS,
            limits.MAX_QUERY_CHARACTERS) == (50, 200, 100, 256)
    assert limits.DEFAULT_RECORDS <= limits.MAX_RECORDS


@pytest.mark.parametrize("value", [0, 201, -1, True, False, 1.0, "5", None, 10 ** 30])
def test_require_int_range_rejects_out_of_range_and_non_int(value):
    from wechat_bridge.core.errors import BridgeError
    from wechat_bridge.core.validate import require_int_range
    with pytest.raises(BridgeError) as info:
        require_int_range(value, 1, 200, "invalid_limit")
    assert info.value.code == "invalid_limit"


@pytest.mark.parametrize("value", [1, 200, 50])
def test_require_int_range_accepts_inclusive_bounds(value):
    from wechat_bridge.core.validate import require_int_range
    assert require_int_range(value, 1, 200, "x") == value


def test_status_limits_are_the_core_limits_and_bridge_error_is_code_only(tmp_path):
    from tests.integration.test_mcp_service import service_case
    from wechat_bridge.core import limits
    _, service = service_case(tmp_path)
    reported = service.status()["limits"]
    assert reported["default_records"] == limits.DEFAULT_RECORDS
    assert reported["maximum_records"] == limits.MAX_RECORDS
    assert reported["context_neighbors"] == limits.MAX_CONTEXT_NEIGHBORS
    assert reported["query_characters"] == limits.MAX_QUERY_CHARACTERS
    assert reported["maximum_content_bytes"] == 8 * 1024 * 1024


def test_compact_json_is_compact_ordered_unicode_and_rejects_nan():
    from wechat_bridge.core.serialize import compact_json
    assert compact_json({"b": "发票", "a": [1, None]}) == '{"b":"发票","a":[1,null]}'
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            compact_json({"x": bad})


def test_bridge_error_public_dict_is_code_only():
    from wechat_bridge.core.errors import BridgeError
    error = BridgeError("source_unreadable", native_code=5)
    assert error.public_dict() == {"error": "source_unreadable"}
    assert "5" not in str(error)


def test_now_utc_is_timezone_aware_iso8601():
    from datetime import datetime, timedelta
    from wechat_bridge.core.clock import now_utc
    parsed = datetime.fromisoformat(now_utc())
    assert parsed.utcoffset() == timedelta(0)
