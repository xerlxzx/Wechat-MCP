import hashlib
import threading

import pytest

from tests.support.proof import proof_case
from tests.integration.test_importer import factory
from tests.support.images import png
from wechat_bridge.core.errors import BridgeError


def service_case(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.mcp.service import ToolService
    case = proof_case(tmp_path)
    refresh_archive(case.config, adapter_factory=factory)
    return case, ToolService(case.config)


def attachment_id(service, kind):
    return service.queries.list_attachments(kind=kind)['items'][0]['attachment_id']


def test_service_delivers_verified_file_and_decoded_original_image(tmp_path):
    case, service = service_case(tmp_path)
    metadata, data = service.attachment(attachment_id(service, 'file'))
    assert data == b'test'
    assert metadata['availability'] == 'original_available'
    assert metadata['source_sha256'] == hashlib.sha256(b'test').hexdigest()
    assert metadata['content_representation'] == 'source'
    assert metadata['resource_uri'].startswith('wechat-attachment://content/')
    metadata, data = service.attachment(attachment_id(service, 'image'))
    assert data == png()
    assert metadata['mime_type'] == 'image/png'
    assert (metadata['width'], metadata['height']) == (3, 2)
    assert metadata['decoded_sha256'] == hashlib.sha256(png()).hexdigest()
    assert metadata['representation'] == 'original'
    assert metadata['content_representation'] == 'decoded'
    assert 'relative_path' not in metadata
    assert not (case.config.state_dir / 'decoded').exists()


def test_unsupported_decoded_format_can_return_separately_labeled_source(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    case = proof_case(tmp_path)
    encoded = b'wxgf' + b'not a supported decoder' * 3
    # Keep a mapped original but update the declared size and file identity.
    from tests.support.encrypted import Engine
    with Engine(case.config.account_root / 'db_storage/message/message_resource.db') as writer:
        writer.run(f'UPDATE MessageResourceDetail SET size={len(encoded)} WHERE resource_id=3')
    case.image_path.write_bytes(encoded)
    refresh_archive(case.config, adapter_factory=factory)
    from wechat_bridge.mcp.service import ToolService
    service = ToolService(case.config); aid = attachment_id(service, 'image')
    metadata, data = service.attachment(aid)
    assert data is None and metadata['availability'] == 'unsupported_format'
    metadata, data = service.attachment(aid, representation='source')
    assert data == encoded
    assert metadata['content_representation'] == 'source'
    assert metadata['decoded_available'] is False
    assert metadata['decoded_sha256'] is None


def test_service_missing_and_mutated_files_cannot_return_content(tmp_path):
    case, service = service_case(tmp_path)
    aid = attachment_id(service, 'file')
    path = case.config.account_root / 'msg/file/2023-11/test.bin'
    path.write_bytes(b'evil')
    metadata, data = service.attachment(aid)
    assert data is None and metadata['availability'] == 'identity_mismatch'
    path.unlink()
    metadata, data = service.attachment(aid)
    assert data is None and metadata['availability'] == 'not_local'


@pytest.mark.parametrize('arguments', [{'max_bytes': 0}, {'max_bytes': 8388609}, {'max_bytes': True},
                                     {'representation': 'first_frame'}, {'max_bytes': 3}])
def test_service_content_limits_and_representations_are_enforced(tmp_path, arguments):
    _, service = service_case(tmp_path)
    with pytest.raises(BridgeError):
        service.attachment(attachment_id(service, 'file'), **arguments)


def test_background_stop_before_publication_preserves_prior_archive(tmp_path, monkeypatch):
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.archive.store import Archive
    case, _ = service_case(tmp_path); cfg = case.config
    before = Archive(cfg).status()['active_generation']; stop = threading.Event()
    original = Archive.write_records
    def request_stop(self, generation, kind, records):
        original(self, generation, kind, records)
        if kind == 'messages':
            stop.set()
    monkeypatch.setattr(Archive, 'write_records', request_stop)
    result = refresh_archive(cfg, adapter_factory=factory, cancel_event=stop)
    assert result['error'] == 'operation_cancelled'
    assert Archive(cfg).status()['active_generation'] == before


def test_declared_nonlocal_content_is_reported_before_transport_size_limit(tmp_path):
    from tests.support.encrypted import Engine
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.mcp.service import ToolService
    case = proof_case(tmp_path)
    with Engine(case.config.account_root / 'db_storage/message/message_resource.db') as writer:
        writer.run('UPDATE MessageResourceDetail SET size=10000000,status=0 WHERE resource_id=4')
    with Engine(case.config.account_root / 'db_storage/hardlink/hardlink.db') as writer:
        writer.run('UPDATE file_hardlink_info_v4 SET file_size=10000000')
    refresh_archive(case.config, adapter_factory=factory)
    service = ToolService(case.config)
    metadata, data = service.attachment(attachment_id(service, 'file'))
    assert metadata['availability'] == 'not_local'
    assert data is None
