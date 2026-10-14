"""MCP wire contract: error codes, argument bounds and the no-echo rule, via the SDK client."""
import asyncio

import pytest
from mcp import Client

from tests.integration.test_mcp_service import service_case

ZERO = '0' * 64


def call(tmp_path, calls, monkeypatch=None):
    from wechat_bridge.mcp.server import create_server
    case, service = service_case(tmp_path)

    async def scenario():
        async with Client(create_server(case.config, watch=False)) as client:
            out = []
            for name, arguments in calls:
                result = await client.call_tool(name, arguments)
                text = ''.join(b.text for b in result.content if b.type == 'text')
                out.append((result.is_error, result.structured_content, text))
            return out
    return asyncio.run(scenario())


@pytest.mark.parametrize('name,arguments', [
    ('search_messages', {'query': ''}),
    ('search_messages', {'query': 'x' * 257}),
    ('search_messages', {'query': 'x', 'limit': 0}),
    ('search_messages', {'query': 'x', 'conversation_id': 'conversation_' + 'G' * 64}),
    ('search_messages', {'query': 'x', 'conversation_id': 'contact_' + ZERO}),
    ('list_contacts', {'query': 'x' * 257}),
    ('list_contacts', {'limit': 201}),
    ('get_context', {'message_id': 'message_' + ZERO, 'before': 101}),
    ('get_context', {'message_id': 'message_' + ZERO, 'before': -1}),
    ('get_context', {'message_id': 'message_' + ZERO, 'generation': 0}),
    ('get_attachment', {'attachment_id': 'attachment_' + ZERO, 'max_bytes': 8 * 1024 * 1024 + 1}),
    ('get_attachment', {'attachment_id': 'attachment_' + 'A' * 64}),
    ('status', {'unexpected': 1}),
])
def test_out_of_bound_or_ill_typed_arguments_are_invalid_arguments_without_echo(tmp_path, name, arguments):
    (is_error, structured, text), = call(tmp_path, [(name, arguments)])
    assert is_error and structured == {'error': 'invalid_arguments'}
    assert text == '{"error":"invalid_arguments"}'


def test_missing_records_and_bad_cursors_use_stable_error_codes(tmp_path):
    results = call(tmp_path, [
        ('get_context', {'message_id': 'message_' + ZERO}),
        ('get_attachment', {'attachment_id': 'attachment_' + ZERO}),
        ('get_group', {'conversation_id': 'conversation_' + ZERO}),
        ('list_contacts', {'cursor': 'not-a-cursor'}),
        ('get_attachment', {'attachment_id': 'attachment_' + ZERO, 'representation': 'bogus'}),
    ])
    codes = [s['error'] if e else None for e, s, _ in results]
    assert all(e for e, _, _ in results)
    assert codes == ['message_not_found', 'attachment_not_found', 'conversation_not_found',
                     'invalid_cursor', 'invalid_representation']
    for _, _, text in results:
        assert ZERO not in text and 'not-a-cursor' not in text and 'Traceback' not in text


def test_unexpected_exception_maps_to_internal_error_without_leaking_message(tmp_path, monkeypatch):
    from wechat_bridge.mcp import service

    def explode(self, *a, **k):
        raise RuntimeError('SECRET-PRIVATE-PATH /Users/someone/x')
    monkeypatch.setattr(service.QueryRepository, 'list_contacts', explode)
    (is_error, structured, text), = call(tmp_path, [('list_contacts', {})])
    assert is_error and structured == {'error': 'internal_error'}
    assert 'SECRET' not in text and '/Users' not in text


def test_decoded_representation_of_a_file_is_refused_explicitly(tmp_path):
    from wechat_bridge.mcp.server import create_server
    case, service = service_case(tmp_path)
    file_id = service.queries.list_attachments(kind='file')['items'][0]['attachment_id']

    async def scenario():
        async with Client(create_server(case.config, watch=False)) as client:
            return await client.call_tool('get_attachment', {'attachment_id': file_id, 'representation': 'decoded'})
    result = asyncio.run(scenario())
    assert result.is_error and result.structured_content == {'error': 'unsupported_representation'}
