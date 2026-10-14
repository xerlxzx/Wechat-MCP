"""Integration characterization tests pinning public limits, schema bounds and the
import-lock permission gate before the behavior-preserving refactor. Added in Commit 0.
"""
import asyncio
import os

import pytest

from mcp import Client

from tests.integration.test_importer import import_case, factory
from tests.integration.test_mcp_service import service_case
from wechat_bridge.core.errors import BridgeError


def test_status_limits_are_pinned(tmp_path):
    _, service = service_case(tmp_path)
    assert service.status()['limits'] == {
        'default_records': 50, 'maximum_records': 200, 'context_neighbors': 100,
        'query_characters': 256, 'default_content_bytes': 1048576, 'maximum_content_bytes': 8388608}


def test_tool_input_schema_bounds_are_pinned(tmp_path):
    from wechat_bridge.mcp.server import create_server
    case, _ = service_case(tmp_path)

    async def scenario():
        async with Client(create_server(case.config, watch=False)) as client:
            return {t.name: t.input_schema for t in (await client.list_tools()).tools}

    schemas = asyncio.run(scenario())
    contacts = schemas['list_contacts']['properties']
    assert contacts['limit']['maximum'] == 200
    assert contacts['limit']['minimum'] == 1
    assert contacts['limit']['default'] == 50
    context = schemas['get_context']['properties']
    assert (context['before']['maximum'], context['before']['minimum'], context['before']['default']) == (100, 0, 10)
    assert context['after']['maximum'] == 100
    attachment = schemas['get_attachment']['properties']
    assert attachment['max_bytes']['maximum'] == 8388608
    assert attachment['max_bytes']['default'] == 1048576
    search = schemas['search_messages']['properties']
    assert search['query']['maxLength'] == 256
    assert search['limit']['maximum'] == 200


def test_import_lock_rejects_group_readable_lock_file(tmp_path):
    from wechat_bridge.archive.importer import refresh_archive
    cfg = import_case(tmp_path)
    assert refresh_archive(cfg, adapter_factory=factory)['status'] == 'complete'
    os.chmod(cfg.state_dir / '.archive-import.lock', 0o644)
    with pytest.raises(BridgeError, match='private_permissions'):
        refresh_archive(cfg, adapter_factory=factory)


@pytest.mark.parametrize('bad', [True, 0, 201, 1.0])
def test_adapter_message_batches_rejects_nonint_batch_size(tmp_path, bad):
    cfg = import_case(tmp_path)
    adapter = factory(cfg)
    group = next(c for c in adapter.conversations() if c.kind == 'group')
    with pytest.raises(BridgeError, match='invalid_limit'):
        list(adapter.message_batches(group.conversation_id, batch_size=bad))
