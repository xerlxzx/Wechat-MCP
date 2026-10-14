import asyncio
import base64
import json
import sys

import pytest

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.integration.test_mcp_service import service_case
from tests.support.images import png

TOOLS = {'status', 'list_conversations', 'list_contacts', 'get_group', 'search_messages',
         'get_messages', 'get_context', 'list_attachments', 'get_attachment'}


async def exercise(client):
    tools = await client.list_tools()
    assert {t.name for t in tools.tools} == TOOLS
    assert all(t.annotations.read_only_hint for t in tools.tools)
    status = await client.call_tool('status', {})
    assert status.structured_content['read_only']
    assert status.structured_content['ready_for_local_queries']
    contacts = await client.call_tool('list_contacts', {'query': 'Same name'})
    assert len(contacts.structured_content['items']) == 2
    conversations = await client.call_tool('list_conversations', {})
    group = next(c for c in conversations.structured_content['items'] if c['kind'] == 'group')
    members = await client.call_tool('get_group', {'conversation_id': group['conversation_id']})
    assert len(members.structured_content['items']) == 2
    search = await client.call_tool('search_messages', {'query': 'Known message'})
    hit = search.structured_content['items'][0]
    context = await client.call_tool('get_context', {'message_id': hit['message_id']})
    assert context.structured_content['message']['text'] == 'Known message'
    timeline = await client.call_tool('get_messages', {'conversation_id': group['conversation_id']})
    assert len(timeline.structured_content['items']) == 4
    assert any(m['source']['server_id'] == '9007199254740993' for m in timeline.structured_content['items'])
    attachments = await client.call_tool('list_attachments', {})
    for item in attachments.structured_content['items']:
        result = await client.call_tool('get_attachment', {'attachment_id': item['attachment_id']})
        assert not result.is_error
        metadata = result.structured_content
        assert metadata['representation'] == 'original'
        blocks = [b for b in result.content if b.type in ('image', 'resource')]
        assert len(blocks) == 1
        if item['kind'] == 'file':
            assert base64.b64decode(blocks[0].resource.blob) == b'test'
        else:
            assert base64.b64decode(blocks[0].data) == png()
        resource = await client.read_resource(metadata['resource_uri'])
        assert base64.b64decode(resource.contents[0].blob) == (b'test' if item['kind'] == 'file' else png())
    for name, arguments in [('search_messages', {'query': 'Known', 'limit': 201}),
                            ('search_messages', {'query': 'Known', 'limit': True}),
                            ('get_context', {'message_id': hit['message_id'], 'before': 80, 'after': 80}),
                            ('list_contacts', {'shell': 'arbitrary command'})]:
        result = await client.call_tool(name, arguments)
        assert result.is_error
        assert 'Traceback' not in ''.join(b.text for b in result.content if b.type == 'text')


def test_official_client_in_memory_exercises_all_tools_and_binary_resources(tmp_path):
    from wechat_bridge.mcp.server import create_server
    case, _ = service_case(tmp_path)
    async def scenario():
        async with Client(create_server(case.config, watch=False)) as client:
            await exercise(client)
    asyncio.run(scenario())


def test_invalid_argument_values_are_normalized_without_echoing_input(tmp_path):
    from wechat_bridge.mcp.server import create_server
    case, _ = service_case(tmp_path)
    async def scenario():
        async with Client(create_server(case.config, watch=False)) as client:
            for arguments in ({'query': 'x', 'limit': 201}, {'query': 'x', 'limit': True},
                              {'query': 'x', 'before': 1}, {'max_bytes': 0}):
                tool = 'get_attachment' if 'max_bytes' in arguments else 'search_messages'
                result = await client.call_tool(tool, arguments | ({'attachment_id': 'attachment_' + '0' * 64} if tool == 'get_attachment' else {}))
                assert result.is_error
                assert result.structured_content == {'error': 'invalid_arguments'}
                text = ''.join(b.text for b in result.content if b.type == 'text')
                assert text == '{"error":"invalid_arguments"}'
                assert 'pydantic' not in text and 'input_value' not in text
    asyncio.run(scenario())


def test_real_stdio_subprocess_handshake_delivery_and_reconnect(tmp_path):
    case, _ = service_case(tmp_path)
    parameters = StdioServerParameters(command=sys.executable,
        args=['-m', 'wechat_bridge.cli', '--state-dir', str(case.config.state_dir), 'serve', '--no-watch'])
    async def scenario():
        async with asyncio.timeout(20):
            async with Client(stdio_client(parameters)) as client:
                await exercise(client)
            async with Client(stdio_client(parameters)) as client:
                assert len((await client.list_tools()).tools) == 9
                assert (await client.call_tool('status', {})).structured_content['active_generation'] is not None
    asyncio.run(scenario())


def test_metadata_response_budget_and_unprepared_archive_are_safe(tmp_path):
    from wechat_bridge.mcp.server import create_server
    from wechat_bridge.archive.store import Archive
    from tests.integration.test_importer import import_case
    case, _ = service_case(tmp_path)
    archive = Archive(case.config); generation = archive.begin_generation(archive.status()['account_id'])
    archive.write_records(generation, 'contacts', [{'contact_id': 'contact_' + '0' * 64, 'display_name': 'x' * 600000}])
    archive.publish(generation, {})
    empty = import_case(tmp_path / 'empty')
    async def scenario():
        async with Client(create_server(case.config, watch=False)) as client:
            response = await client.call_tool('list_contacts', {'limit': 1})
            assert response.is_error and response.structured_content['error'] == 'content_too_large'
        async with Client(create_server(empty, watch=True)) as client:
            status = await client.call_tool('status', {})
            assert status.structured_content['ready_for_local_queries'] is False
            response = await client.call_tool('search_messages', {'query': 'anything'})
            assert response.is_error
    asyncio.run(scenario())


def test_default_stdio_watcher_closes_cleanly_on_client_disconnect(tmp_path):
    case, _ = service_case(tmp_path)
    parameters = StdioServerParameters(command=sys.executable,
        args=['-m', 'wechat_bridge.cli', '--state-dir', str(case.config.state_dir), 'serve'])
    async def scenario():
        async with asyncio.timeout(12):
            async with Client(stdio_client(parameters)) as client:
                assert (await client.call_tool('status', {})).structured_content['ready_for_local_queries']
    asyncio.run(scenario())


def test_resource_failures_keep_safe_codes_without_traceback_or_private_path(tmp_path, monkeypatch, caplog):
    import errno
    from mcp.shared.exceptions import MCPError
    from wechat_bridge.archive.importer import refresh_archive
    from wechat_bridge.mcp.server import create_server
    from tests.integration.test_importer import factory
    case, _ = service_case(tmp_path)
    async def scenario():
        async with Client(create_server(case.config, watch=False)) as client:
            listed = (await client.call_tool('list_attachments', {})).structured_content['items']
            file = next(a for a in listed if a['kind'] == 'file')
            image = next(a for a in listed if a['kind'] == 'image')
            file_uri = (await client.call_tool('get_attachment', {'attachment_id': file['attachment_id']})).structured_content['resource_uri']
            image_uri = (await client.call_tool('get_attachment', {'attachment_id': image['attachment_id']})).structured_content['resource_uri']
            private_path = str(case.config.state_dir / 'decoded' / 'private-cache')
            def failed_cache(*args, **kwargs):
                raise OSError(errno.ENOSPC, 'disk full', private_path)
            monkeypatch.setattr('wechat_bridge.mcp.service.cache_decoded', failed_cache)
            with pytest.raises(MCPError, match='internal_error') as issue:
                await client.read_resource(image_uri)
            assert private_path not in str(issue.value)
            (case.config.account_root / 'msg/file/2023-11/test.bin').unlink()
            with pytest.raises(MCPError, match='attachment_not_local'):
                await client.read_resource(file_uri)
            refresh_archive(case.config, adapter_factory=factory)
            refresh_archive(case.config, adapter_factory=factory)
            with pytest.raises(MCPError, match='cursor_expired'):
                await client.read_resource(file_uri)
        assert private_path not in caplog.text
        assert 'Traceback' not in caplog.text
    asyncio.run(scenario())
