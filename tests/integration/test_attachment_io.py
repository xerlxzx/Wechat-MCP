from dataclasses import replace
import hashlib
import io
import os
import pytest
from tests.support.schemas import account_case
from tests.support.images import png


def ref(path,**kwargs):
    from wechat_bridge.attachments.models import AttachmentRef
    return AttachmentRef('attachment_test','message_test','file',path,expected_size=4,
                         expected_digest=hashlib.md5(b'test').hexdigest(),mapping_provenance='resource_hardlink',**kwargs)


def test_file_bytes_and_streaming_hash_are_verified(tmp_path):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg=account_case(tmp_path); path=cfg.account_root/'msg/file/2026-01/test.bin';path.parent.mkdir();path.write_bytes(b'test')
    with resolve_attachment(ref('msg/file/2026-01/test.bin'),cfg) as result:
        assert result.availability=='original_available'
        assert result.handle.read()==b'test'
        assert result.source_sha256==hashlib.sha256(b'test').hexdigest()
        assert result.verification_basis=='size_and_digest'
    assert result.handle.closed


@pytest.mark.parametrize('path',['../escape','msg/file/../escape','msg/file/a\0b','/tmp/escape','msg/other/file'])
def test_paths_outside_approved_roots_are_rejected(tmp_path,path):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg=account_case(tmp_path)
    with resolve_attachment(ref(path),cfg) as result:
        assert result.availability=='identity_mismatch'
        assert result.handle is None


@pytest.mark.parametrize('ancestor',[False,True])
def test_symlink_components_and_final_file_are_rejected(tmp_path,ancestor):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg=account_case(tmp_path); outside=(tmp_path/'outside');outside.mkdir();(outside/'file').write_bytes(b'test')
    path=cfg.account_root/'msg/file/link'
    path.symlink_to(outside if ancestor else outside/'file')
    with resolve_attachment(ref('msg/file/link/file' if ancestor else 'msg/file/link'),cfg) as result:
        assert result.availability=='identity_mismatch'


def test_missing_digest_size_mismatch_and_missing_file(tmp_path):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg=account_case(tmp_path); path=cfg.account_root/'msg/file/test.bin';path.write_bytes(b'test')
    direct=replace(ref('msg/file/test.bin'),expected_digest=None)
    with resolve_attachment(direct,cfg) as result:
        assert result.availability=='original_available'
        assert result.verification_basis=='direct_mapping_and_size'
    for bad in (replace(direct,expected_size=5),replace(direct,expected_digest='0'*32)):
        with resolve_attachment(bad,cfg) as result: assert result.availability=='identity_mismatch'
    with resolve_attachment(ref('msg/file/absent'),cfg) as result: assert result.availability=='not_local'


def test_file_replacement_after_mapping_does_not_return_new_bytes(tmp_path):
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg=account_case(tmp_path);path=cfg.account_root/'msg/file/test.bin';path.write_bytes(b'test');s=path.stat()
    selected=replace(ref('msg/file/test.bin'),file_identity=(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns))
    path.unlink();path.write_bytes(b'test')
    with resolve_attachment(selected,cfg) as result: assert result.availability=='identity_mismatch'


def test_thumbnail_representation_never_claims_original(tmp_path):
    from wechat_bridge.attachments.models import AttachmentRef
    from wechat_bridge.attachments.resolver import resolve_attachment
    cfg=account_case(tmp_path);raw=png();encoded=bytes(v^99 for v in raw);path=cfg.account_root/'msg/attach/thumbnail.dat';path.write_bytes(encoded)
    thumbnail=AttachmentRef('attachment_thumb','message_test','image','msg/attach/thumbnail.dat',expected_size=len(encoded),representation='thumbnail',mapping_provenance='resource_layout')
    with resolve_attachment(thumbnail,cfg) as result:
        assert result.availability=='thumbnail_only'
        assert result.representation=='thumbnail'
        assert result.source_sha256!=result.decoded_sha256
        assert result.decoded.data==raw


def test_mutation_during_hash_is_read_failed(tmp_path,monkeypatch):
    from wechat_bridge.attachments import resolver
    cfg=account_case(tmp_path);path=cfg.account_root/'msg/file/test.bin';path.write_bytes(b'test')
    real_hash=resolver.hash_stream
    def mutate(stream,*args,**kwargs):
        out=real_hash(stream,*args,**kwargs)
        path.write_bytes(b'changed')
        return out
    monkeypatch.setattr(resolver,'hash_stream',mutate)
    with resolver.resolve_attachment(ref('msg/file/test.bin'),cfg) as result:
        assert result.availability=='read_failed'
        assert result.diagnostic_code=='source_changed'


def test_file_changed_after_verification_cannot_be_streamed(tmp_path):
    from wechat_bridge.attachments.resolver import resolve_attachment
    from wechat_bridge.core.errors import BridgeError
    cfg=account_case(tmp_path);path=cfg.account_root/'msg/file/test.bin';path.write_bytes(b'test')
    with resolve_attachment(ref('msg/file/test.bin'),cfg) as result:
        path.write_bytes(b'evil')
        with pytest.raises(BridgeError,match='source_changed'):
            result.handle.read()


def test_ordinary_file_overflow_is_explicit(tmp_path):
    from wechat_bridge.attachments.resolver import resolve_attachment
    from wechat_bridge.core.errors import BridgeError
    cfg=account_case(tmp_path);path=cfg.account_root/'msg/file/huge.bin'
    with path.open('wb') as stream: stream.truncate(209715201)
    with pytest.raises(BridgeError,match='content_too_large'):
        resolve_attachment(replace(ref('msg/file/huge.bin'),expected_size=None,expected_digest=None),cfg)
