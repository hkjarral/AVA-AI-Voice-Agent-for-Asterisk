import json

import pytest

from src.tools.business.calendly_client import CalendlyClient, CalendlyError


class Response:
    def __init__(self, status=200, body=None, headers=None, raw=None, fragments=None):
        self.status, self.headers = status, headers or {}
        self.payload = raw if raw is not None else json.dumps(body if body is not None else {"resource": {}}).encode()
        self.content = self
        self.fragments = fragments
    async def iter_chunked(self, n):
        for chunk in self.fragments or [self.payload[i:i+n] for i in range(0, len(self.payload), n)]:
            yield chunk
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass


class Session:
    def __init__(self, response, calls): self.response, self.calls = response, calls
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


async def test_complete_json_is_read_across_network_fragments(monkeypatch):
    calls = []
    response = Response(fragments=[b'{"resource":', b'{"name": "Syn', b'thetic Host"}}'])
    monkeypatch.setattr('aiohttp.ClientSession', lambda **kwargs: Session(response, calls))
    assert await CalendlyClient('synthetic').request('GET', '/users/me') == {'resource': {'name': 'Synthetic Host'}}
    assert len(calls) == 1


@pytest.mark.parametrize('status,body,uncertain', [(400, {"title": "Bad Request"}, False), (401, {"title": "Unauthorized"}, False), (403, {"title": "Forbidden"}, False), (404, {"title": "Missing"}, False), (500, {"title": "Server Error"}, True), (302, {"title": "Redirect"}, True), (400, {"gateway": "failed"}, True)])
async def test_mutation_error_classification_without_retry(monkeypatch, status, body, uncertain):
    calls = []
    monkeypatch.setattr('aiohttp.ClientSession', lambda **kwargs: Session(Response(status, body), calls))
    with pytest.raises(CalendlyError) as exc:
        await CalendlyClient('synthetic-private-token').request('POST', '/invitees', body={})
    assert exc.value.uncertain is uncertain
    assert len(calls) == 1 and calls[0][2]['allow_redirects'] is False
    assert 'synthetic-private-token' not in str(exc.value)


@pytest.mark.parametrize('raw', [b'not-json', b'x' * 262145, b'[]'])
async def test_malformed_success_is_uncertain(monkeypatch, raw):
    calls = []
    monkeypatch.setattr('aiohttp.ClientSession', lambda **kwargs: Session(Response(201, raw=raw), calls))
    with pytest.raises(CalendlyError) as exc:
        await CalendlyClient('synthetic').request('POST', '/invitees', body={})
    assert exc.value.uncertain and len(calls) == 1


async def test_quota_and_unapproved_operations(monkeypatch):
    calls = []
    monkeypatch.setattr('aiohttp.ClientSession', lambda **kwargs: Session(Response(429, {"title": "Too Many Requests"}, {'X-RateLimit-Reset': '60'}), calls))
    with pytest.raises(CalendlyError) as exc:
        await CalendlyClient('synthetic').request('POST', '/invitees')
    assert exc.value.cooldown == 60 and not exc.value.uncertain
    for method, path in [('DELETE', '/invitees'), ('POST', '/event_types'), ('GET', 'https://evil.invalid/')]:
        with pytest.raises(CalendlyError): await CalendlyClient('synthetic').request(method, path)
    assert len(calls) == 1
