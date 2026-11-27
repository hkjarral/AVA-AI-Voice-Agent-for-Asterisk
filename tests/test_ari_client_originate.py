from unittest.mock import Mock

import pytest

from src.ari_client import ARIClient


class _Response:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return {"id": "channel-1"}


@pytest.mark.asyncio
async def test_originate_channel_sends_variables_in_json_body_without_mutating_input():
    client = ARIClient.__new__(ARIClient)
    client.http_url = "http://asterisk.invalid/ari"
    client.http_session = Mock()
    client.http_session.request.return_value = _Response()
    channel_vars = {
        "AAVA_CUSTOM_VARS_JSON": '{"task":"confirm appointment"}',
        "AI_AGENT": "sales",
    }
    original = dict(channel_vars)

    result = await client.originate_channel(
        endpoint="Local/15551234567@from-internal",
        app="asterisk-ai-voice-agent",
        app_args="outbound,attempt-1,campaign-1,lead-1",
        timeout=60,
        caller_id="Asterisk AI <6789>",
        channel_vars=channel_vars,
    )

    assert result == {"id": "channel-1"}
    assert channel_vars == original
    request = client.http_session.request.call_args
    assert request.args == ("POST", "http://asterisk.invalid/ari/channels")
    assert request.kwargs["json"] == {"variables": original}
    assert "channelVars" not in request.kwargs["params"]
    assert request.kwargs["params"] == {
        "endpoint": "Local/15551234567@from-internal",
        "app": "asterisk-ai-voice-agent",
        "timeout": "60",
        "appArgs": "outbound,attempt-1,campaign-1,lead-1",
        "callerId": "Asterisk AI <6789>",
    }


@pytest.mark.asyncio
async def test_originate_channel_without_variables_omits_json_body():
    client = ARIClient.__new__(ARIClient)
    client.http_url = "http://asterisk.invalid/ari"
    client.http_session = Mock()
    client.http_session.request.return_value = _Response()

    await client.originate_channel(
        endpoint="PJSIP/6000",
        app="asterisk-ai-voice-agent",
    )

    request = client.http_session.request.call_args
    assert request.kwargs["json"] is None
    assert request.kwargs["params"] == {
        "endpoint": "PJSIP/6000",
        "app": "asterisk-ai-voice-agent",
        "timeout": "60",
    }


@pytest.mark.asyncio
async def test_invalid_http_url_is_reported_as_definitely_undispatched():
    import aiohttp
    client = ARIClient.__new__(ARIClient)
    client.http_url = 'invalid-local-ari-url'
    client.http_session = Mock()
    client.http_session.request.side_effect = aiohttp.InvalidURL('invalid-local-ari-url')
    result = await client.send_command('POST', 'channels', allow_redirects=False)
    assert result['request_started'] is False
    assert result['status'] == 503


@pytest.mark.asyncio
async def test_connector_failure_with_redirects_allowed_is_not_proven_unsent():
    import aiohttp
    client = ARIClient.__new__(ARIClient)
    client.http_url = 'http://asterisk.invalid/ari'
    client.http_session = Mock()
    client.http_session.request.side_effect = aiohttp.InvalidURL('invalid redirect destination')
    result = await client.send_command('POST', 'channels')
    assert result['status'] == 503
    assert result.get('request_started') is not False


@pytest.mark.asyncio
async def test_continue_does_not_follow_redirect_or_report_acceptance():
    import aiohttp
    from aiohttp import web
    seen = []
    async def redirect(request):
        seen.append('continue')
        raise web.HTTPTemporaryRedirect(location='/redirect-target')
    async def target(request):
        seen.append('redirect-target')
        return web.Response(status=204)
    app = web.Application()
    app.router.add_post('/ari/channels/caller/continue', redirect)
    app.router.add_post('/redirect-target', target)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        async with aiohttp.ClientSession() as session:
            client = ARIClient.__new__(ARIClient)
            client.http_url = f'http://127.0.0.1:{port}/ari'
            client.http_session = session
            assert await client.continue_in_dialplan('caller', context='survey') is None
        assert seen == ['continue']
    finally:
        await runner.cleanup()
