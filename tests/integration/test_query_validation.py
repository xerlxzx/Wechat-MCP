"""QueryRepository input validation: every external argument is strictly typed and bounded."""
import pytest

from tests.integration.test_search import repo_case
from wechat_bridge.core.errors import BridgeError

ZERO = "0" * 64


@pytest.fixture
def repo(tmp_path):
    return repo_case(tmp_path)[1]


@pytest.mark.parametrize("limit", [0, -1, 201, True, 1.5, "10", None])
def test_limit_is_a_strict_bounded_int(repo, limit):
    for call in (lambda: repo.list_contacts(limit=limit), lambda: repo.search_messages("Known", limit=limit)):
        with pytest.raises(BridgeError, match="invalid_limit"):
            call()


@pytest.mark.parametrize("kwargs,code", [
    ({"start": "2026-01-01T00:00:00"}, "invalid_time_range"),               # naive datetime
    ({"start": "not a date"}, "invalid_time_range"),
    ({"start": 5}, "invalid_time_range"),
    ({"start": "2026-01-01T00:00:00+00:00" + " " * 80}, "invalid_time_range"),
    ({"start": "2026-02-01T00:00:00+00:00", "end": "2026-01-01T00:00:00+00:00"}, "invalid_time_range"),
    ({"type": "sticker"}, "invalid_filter"),
    ({"conversation_id": "conversation_x"}, "invalid_id"),
    ({"sender_id": "message_" + ZERO}, "invalid_id"),
])
def test_search_filters_are_validated_before_querying(repo, kwargs, code):
    with pytest.raises(BridgeError) as info:
        repo.search_messages("Known", **kwargs)
    assert info.value.code == code


@pytest.mark.parametrize("query", ["", "   ", "x" * 257])
def test_search_query_must_be_non_blank_and_bounded(repo, query):
    with pytest.raises(BridgeError):
        repo.search_messages(query)


def test_equal_start_and_end_with_offsets_is_accepted_and_normalized_to_utc(repo):
    page = repo.search_messages("Known", start="2026-01-01T08:00:00+08:00", end="2026-01-01T00:00:00+00:00")
    assert page["items"] is not None


@pytest.mark.parametrize("call,code", [
    (lambda r: r.list_conversations(kind="channel"), "invalid_filter"),
    (lambda r: r.list_attachments(kind="video"), "invalid_filter"),
    (lambda r: r.list_attachments(representation="preview"), "invalid_filter"),
    (lambda r: r.list_attachments(availability="original_available"), "invalid_filter"),
    (lambda r: r.get_context("message_" + ZERO, before=101), "invalid_context_limit"),
    (lambda r: r.get_context("message_" + ZERO, before=60, after=41), "invalid_context_limit"),
    (lambda r: r.get_context("message_" + ZERO, before=-1), "invalid_context_limit"),
    (lambda r: r.get_context("message_" + ZERO, before=True), "invalid_context_limit"),
    (lambda r: r.get_context("message_" + ZERO, generation=0), "invalid_generation"),
    (lambda r: r.get_context("message_" + ZERO, generation=1 << 63), "invalid_generation"),
    (lambda r: r.get_context("message_" + ZERO, generation=True), "invalid_generation"),
    (lambda r: r.get_context("message_" + ZERO, generation=99999), "cursor_expired"),
    (lambda r: r.get_context("message_" + ZERO), "message_not_found"),
    (lambda r: r.get_group("conversation_" + ZERO), "conversation_not_found"),
    (lambda r: r.get_context("contact_" + ZERO), "invalid_id"),
    (lambda r: r.attachment_ref("attachment_" + ZERO), "attachment_not_found"),
])
def test_context_attachment_and_filter_arguments_fail_with_stable_codes(repo, call, code):
    with pytest.raises(BridgeError) as info:
        call(repo)
    assert info.value.code == code


def test_context_limit_boundary_is_inclusive(repo):
    # Ids are keyed per run, so pick whichever conversation actually has messages.
    hit = next(m["message_id"] for c in repo.list_conversations()["items"]
               for m in repo.get_messages(c["conversation_id"])["items"])
    assert repo.get_context(hit, before=50, after=50)["message"]["message_id"] == hit
    assert repo.get_context(hit, before=0, after=0)["message"]["message_id"] == hit


def test_direct_conversation_is_not_a_group(repo):
    direct = next(c for c in repo.list_conversations()["items"] if c["kind"] == "direct")
    with pytest.raises(BridgeError, match="group_not_found"):
        repo.get_group(direct["conversation_id"])
