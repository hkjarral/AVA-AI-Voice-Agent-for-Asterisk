"""Authentication/setup/recovery tests with synthetic state and no network."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Depends
from fastapi import HTTPException
import portalocker
from httpx import ASGITransport, AsyncClient

import auth
from api import calendly
from src.core.calendly_store import CalendlyStore
from src.tools.business.calendly_service import CalendlyService
from src.tools.business.calendly_client import CalendlyError

IDENTITY = {"uri": "https://api.calendly.com/users/test", "current_organization": "https://api.calendly.com/organizations/test", "name": "Synthetic Host", "timezone": "UTC"}


@pytest.mark.asyncio
async def test_journal_lock_failure_is_a_safe_retryable_setup_error(monkeypatch):
    def blocked():
        raise portalocker.exceptions.AlreadyLocked('synthetic-private-diagnostic')
    monkeypatch.setattr(calendly, 'CalendlyStore', blocked)
    with pytest.raises(HTTPException) as error:
        await calendly.service()
    assert error.value.status_code == 503
    assert error.value.detail['code'] == 'journal_busy'
    assert 'synthetic-private-diagnostic' not in str(error.value.detail)


@pytest.fixture
def api(tmp_path, monkeypatch):
    store = CalendlyStore(str(tmp_path / "db"), str(tmp_path / "token"))
    s = CalendlyService(store)
    async def provide(): return s
    async def verify(token=None):
        if token == "invalid-synthetic-token": raise CalendlyError("upstream_401")
        return IDENTITY
    monkeypatch.setattr(calendly, "service", provide)
    monkeypatch.setattr(s, "verify", verify)
    monkeypatch.setattr(auth, "get_user", lambda username: SimpleNamespace(username="admin", must_change_password=False) if username == "admin" else None)
    app = FastAPI()
    app.include_router(calendly.router, prefix="/api/calendly", dependencies=[Depends(auth.get_current_user)])
    return app, store, {"Authorization": "Bearer " + auth.create_access_token({"sub": "admin"})}


@pytest.mark.asyncio
async def test_all_routes_require_auth(api):
    app, _, _ = api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for route in calendly.router.routes:
            method = next(iter(route.methods))
            response = await client.request(method, "/api/calendly" + route.path.replace("{key}", "example"), json={})
            assert response.status_code == 401, (method, route.path, response.status_code)


@pytest.mark.asyncio
async def test_verify_save_replace_and_disconnect(api):
    app, store, headers = api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        response = await client.post("/api/calendly/connection/verify", json={"token": "synthetic-token"})
        assert response.status_code == 200
        assert not store.token_path.exists()  # Verify is read-only.
        response = await client.put("/api/calendly/connection", json={"token": "synthetic-token", "revision": 0})
        assert response.status_code == 200
        assert "synthetic-token" not in response.text
        revision = response.json()["revision"]
        assert not response.json()["enabled"]
        response = await client.put("/api/calendly/connection", json={"token": "invalid-synthetic-token", "revision": revision})
        assert response.status_code == 422
        assert "invalid-synthetic-token" not in response.text
        assert store.credentials()[1] == "synthetic-token"
        assert (await client.put("/api/calendly/connection", json={"token": "other", "revision": 0})).status_code == 409
        assert (await client.post("/api/calendly/connection/enable", json={"revision": revision})).json()["enabled"]
        assert not (await client.post("/api/calendly/connection/disable", json={"revision": revision})).json()["enabled"]
        assert (await client.request("DELETE", "/api/calendly/connection", json={"revision": revision})).status_code == 200
        assert not store.token_path.exists()
        assert "synthetic-token" not in (await client.get("/api/calendly/connection")).text


@pytest.mark.asyncio
async def test_empty_verification_requires_a_token_without_enabling_booking(api, monkeypatch):
    app, _, headers = api
    s = await calendly.service()
    monkeypatch.setattr(s, 'verify', CalendlyService.verify.__get__(s))
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', headers=headers) as client:
        response = await client.post('/api/calendly/connection/verify', json={})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'verification_token_required'
    assert 'Enter a personal access token' in response.json()['detail']['message']


@pytest.mark.asyncio
async def test_test_routes_reject_arbitrary_operations_and_missing_confirmation(api):
    app, store, headers = api
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        response = await client.post("/api/calendly/test-bookings", json={"operation": "call:someone", "slot_reference": "x", "name": "Test", "email": "test@example.com"})
        assert response.status_code == 422
        assert not store.prior("call:someone")
        assert (await client.post("/api/calendly/attempts/unknown/qualify")).status_code == 422


@pytest.mark.asyncio
async def test_private_links_only_on_authenticated_details(api):
    app, store, headers = api
    conn = store.save_connection(IDENTITY, "synthetic", 0)
    store.update_connection(conn["revision"], enabled=True)
    payload = {"event_type": "https://api.calendly.com/event_types/test", "start_time": "2030-01-01T10:00:00+00:00", "invitee": {"email": "test@example.com"}}
    row, _ = store.admit("setup:test", store.connection(), payload, {"appointment_name": "Test appointment"})
    store.finish(row["id"], "confirmed", {"appointment_name": "Test appointment"}, {"cancel_url": "https://calendly.com/cancellations/private", "reschedule_url": "https://evil.invalid/reschedulings/private"})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        listed = await client.get("/api/calendly/attempts")
        assert "cancellations" not in listed.text and "test@example.com" not in listed.text
        detail = (await client.get(f'/api/calendly/attempts/{row["id"]}')).json()
        assert detail["cancel_url"] == "https://calendly.com/cancellations/private"
        assert "reschedule_url" not in detail
        assert (await client.post(f'/api/calendly/attempts/{row["id"]}/external-resolution', json={"note": "test"})).status_code == 422


@pytest.mark.asyncio
async def test_setup_confirmation_reference_as_slot_returns_correctable_error_without_post(api):
    app, store, headers = api
    conn = store.save_connection(IDENTITY, 'synthetic', 0)
    store.update_connection(conn['revision'], enabled=True)
    operation = 'setup:00000000-0000-4000-8000-000000000000'
    reference = store.proposal(operation, conn['id'], {'confirmation_digest': 'not-a-slot'})
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', headers=headers) as client:
        response = await client.post('/api/calendly/test-bookings', json={
            'operation': operation, 'slot_reference': reference, 'name': 'Test', 'email': 'test@example.com',
            'booking_confirmed': True, 'email_confirmed': True})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'proposal_expired'
    assert store.prior(operation) is None


@pytest.mark.asyncio
async def test_erased_call_attempt_cannot_expose_links_or_restart_recovery(api):
    app, store, headers = api
    conn = store.save_connection(IDENTITY, 'synthetic', 0)
    store.update_connection(conn['revision'], enabled=True)
    payload = {'event_type': 'https://api.calendly.com/event_types/test', 'start_time': '2030-01-01T10:00:00+00:00', 'invitee': {'email': 'test@example.com'}}
    row, _ = store.admit('call:erased', store.connection(), payload, {'appointment_name': 'Private name'})
    store.finish(row['id'], 'confirmed', {'appointment_name': 'Private name'}, {'cancel_url': 'https://calendly.com/cancellations/private'})
    store.cleanup(['erased'])
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', headers=headers) as client:
        response = await client.get('/api/calendly/attempts/'+row['id'])
        detail = response.json()
        assert detail['reservation_status'] == 'erased' and detail['status'] == 'error'
        assert 'does not cancel' in detail['message']
        assert 'cancel_url' not in detail and 'appointment_name' not in detail
        assert 'test@example.com' not in response.text and 'cancellations/private' not in response.text
        assert (await client.post('/api/calendly/attempts/'+row['id']+'/reconcile')).status_code == 409
        assert (await client.post('/api/calendly/attempts/'+row['id']+'/external-resolution', json={'note':'must stay erased'})).status_code == 422
