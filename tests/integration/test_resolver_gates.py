"""Resolver gates that keep unverified or substituted media from being delivered."""
import hashlib
import os

import pytest

from tests.support.schemas import account_case
from wechat_bridge.core.errors import BridgeError


def make(path, provenance="resource_hardlink", **kwargs):
    from wechat_bridge.attachments.models import AttachmentRef
    return AttachmentRef("attachment_test", "message_test", "file", path, expected_size=4,
                         expected_digest=hashlib.md5(b"test").hexdigest(),
                         mapping_provenance=provenance, **kwargs)


def media(tmp_path, data=b"test"):
    cfg = account_case(tmp_path)
    path = cfg.account_root / "msg/file/2026-01/a.bin"
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    return cfg, "msg/file/2026-01/a.bin"


@pytest.mark.parametrize("provenance", ["unsupported", "", "guess", "filename_match"])
def test_unknown_mapping_provenance_never_reads_the_file(tmp_path, provenance, monkeypatch):
    from wechat_bridge.attachments import resolver
    cfg, rel = media(tmp_path)
    monkeypatch.setattr(resolver, "open_media", lambda *a: pytest.fail("opened an unmapped file"))
    with resolver.resolve_attachment(make(rel, provenance), cfg) as result:
        assert result.availability == "unsupported_format" and result.handle is None
        assert result.source_sha256 is None


def test_conflicting_mapping_is_identity_mismatch_without_content(tmp_path, monkeypatch):
    from wechat_bridge.attachments import resolver
    cfg, rel = media(tmp_path)
    monkeypatch.setattr(resolver, "open_media", lambda *a: pytest.fail("opened a conflicting file"))
    with resolver.resolve_attachment(make(rel, "conflicting"), cfg) as result:
        assert (result.availability, result.diagnostic_code) == ("identity_mismatch", "attachment_mapping_conflicting")
        assert result.handle is None


@pytest.mark.parametrize("kwargs", [{"declared_local": False}, {}])
def test_not_local_reference_stays_explicit(tmp_path, kwargs):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg, rel = media(tmp_path)
    ref = make(None if not kwargs else rel, **kwargs)
    with resolve_attachment(ref, cfg) as result:
        assert result.availability == "not_local" and result.handle is None


def test_directory_and_fifo_final_components_are_not_regular_files(tmp_path):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg, rel = media(tmp_path)
    base = cfg.account_root / "msg/file/2026-01"
    (base / "adir").mkdir()
    os.mkfifo(base / "pipe")
    for name in ("adir", "pipe"):
        with resolve_attachment(make(f"msg/file/2026-01/{name}"), cfg) as result:
            assert result.availability in ("identity_mismatch", "read_failed")
            assert result.handle is None and result.source_sha256 is None


def test_digest_comparison_is_case_insensitive_but_wrong_digest_is_a_mismatch(tmp_path):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg, rel = media(tmp_path)
    upper = make(rel)
    upper = type(upper)(**{**upper.__dict__, "expected_digest": upper.expected_digest.upper()})
    with resolve_attachment(upper, cfg) as result:
        assert result.availability == "original_available"
    wrong = type(upper)(**{**upper.__dict__, "expected_digest": hashlib.md5(b"other").hexdigest()})
    with resolve_attachment(wrong, cfg) as result:
        assert (result.availability, result.diagnostic_code) == ("identity_mismatch", "attachment_identity_mismatch")
        assert result.handle is None


def test_pinned_file_identity_must_match_the_opened_file(tmp_path):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg, rel = media(tmp_path)
    with resolve_attachment(make(rel, file_identity=(1, 2, 4, 5, 6)), cfg) as result:
        assert result.availability == "identity_mismatch" and result.handle is None


@pytest.mark.parametrize("limit", [0, -1, True, "1", 209715201])
def test_source_byte_limit_is_validated_before_any_io(tmp_path, limit):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg, rel = media(tmp_path)
    with pytest.raises(BridgeError, match="invalid_content_limit"):
        resolve_attachment(make(rel), cfg, max_source_bytes=limit)


def test_source_limit_smaller_than_file_raises_content_too_large(tmp_path):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg, rel = media(tmp_path)
    with pytest.raises(BridgeError, match="content_too_large"):
        resolve_attachment(make(rel), cfg, max_source_bytes=3)
    with resolve_attachment(make(rel), cfg, max_source_bytes=4) as result:
        assert result.handle.read() == b"test"
