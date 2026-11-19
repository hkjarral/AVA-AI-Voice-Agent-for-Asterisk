"""Synthetic contract and failure-injection tests; never contacts Calendly."""
import asyncio
import copy
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.core.calendly_store import CalendlyStore, digest
from src.tools.business.calendly_client import CalendlyClient, CalendlyError, appointment, resource_path, validate_config
from src.tools.business.calendly_service import CalendlyService, ObservedClient, email
from src.tools.runtime_config import resolve_agent_tool_config
from src.tools.adapters.sanitize import sanitize_tool_result_for_json_string

USER = "https://api.calendly.com/users/alex"
ORG = "https://api.calendly.com/organizations/company"
TYPE = "https://api.calendly.com/event_types/consultation"
EVENT = "https://api.calendly.com/scheduled_events/booking"
INVITEE = EVENT + "/invitees/person"
IDENTITY = {"uri": USER, "current_organization": ORG, "name": "Alex", "timezone": "America/Los_Angeles"}


class FakeClient(CalendlyClient):
    def __init__(self):
        self.raw = {"uri": TYPE, "name": "Consultation", "duration": 30, "active": True,
                    "is_paid": False, "deleted_at": None, "kind": "solo", "pooling_type": None,
                    "type": "StandardEventType", "booking_method": "instant",
                    "profile": {"type": "User", "owner": USER, "name": "Alex"},
                    "custom_questions": [], "locations": [], "updated_at": "2026-01-01T00:00:00Z"}
        self.start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat()
        self.invitee = None
        self.event = None
        self.posts = 0
        self.lost = False
        self.reject = False
        self.calls = []
        self.accepted = asyncio.Event()
        self.pause = None
        self.no_match = False
        self.missing_marker = False
        self.overflow = False

    async def request(self, method, path, *, params=None, body=None, timeout=5):
        self.calls.append((method, path, params))
        if path == "/users/me":
            return {"resource": copy.deepcopy(IDENTITY)}
        if path == "/event_types":
            return {"collection": [copy.deepcopy(self.raw)], "pagination": {}}
        if path == "/event_types/consultation":
            return {"resource": copy.deepcopy(self.raw)}
        if path == "/event_type_available_times":
            return {"collection": [{"status": "available", "invitees_remaining": 1, "start_time": self.start}]}
        if method == "POST":
            self.posts += 1
            if self.reject:
                raise CalendlyError("upstream_400")
            self.invitee = {**copy.deepcopy(body["invitee"]), "uri": INVITEE, "event": EVENT,
                            "status": "active", "tracking": copy.deepcopy(body["tracking"]),
                            "questions_and_answers": copy.deepcopy(body["questions_and_answers"]),
                            "cancel_url": "https://calendly.com/cancellations/private",
                            "reschedule_url": "https://calendly.com/reschedulings/private"}
            self.event = {"uri": EVENT, "event_type": TYPE, "status": "active", "start_time": self.start,
                          "end_time": (datetime.fromisoformat(self.start)+timedelta(minutes=30)).isoformat(),
                          "event_memberships": [{"user": USER}]}
            self.accepted.set()
            if self.pause:
                await self.pause.wait()
            if self.lost:
                raise CalendlyError("network_unavailable", uncertain=True)
            return {"resource": copy.deepcopy(self.invitee)}
        if path == "/scheduled_events":
            rows = [] if self.no_match or not self.event or params.get("status") != self.event["status"] else [copy.deepcopy(self.event)]
            return {"collection": rows, "pagination": {"next_page_token": "more"} if self.overflow else {}}
        if path == "/scheduled_events/booking":
            return {"resource": copy.deepcopy(self.event)}
        if path == "/scheduled_events/booking/invitees":
            rows = [] if not self.invitee or params.get("status") != self.invitee["status"] else [copy.deepcopy(self.invitee)]
            if self.missing_marker:
                for row in rows:
                    row["tracking"] = {}
            return {"collection": rows, "pagination": {}}
        if path == "/scheduled_events/booking/invitees/person":
            return {"resource": copy.deepcopy(self.invitee)}
        raise AssertionError((method, path))


@pytest.fixture
def setup(tmp_path):
    store = CalendlyStore(str(tmp_path / "data" / "journal.db"), str(tmp_path / "secrets" / "token.json"))
    conn = store.save_connection(IDENTITY, "synthetic-private-token", 0)
    store.update_connection(conn["revision"], enabled=True)
    fake = FakeClient()
    service = CalendlyService(store, lambda token: fake)
    cfg = {"enabled": True, "appointments": {"consultation": {"uri": TYPE, "connection_id": conn["id"], "one_to_one_confirmed": True, "name": "Consultation", "duration": 30}}}
    return store, service, fake, cfg


async def propose(service, fake, cfg, operation="call:one", setup=False):
    start = datetime.now(timezone.utc) + timedelta(minutes=1)
    result = await service.slots(cfg, operation, "consultation", start.isoformat(), (start+timedelta(days=7)).isoformat(), "America/Los_Angeles", setup=setup)
    params = {"slot_reference": result["slots"][0]["slot_reference"], "name": "Test Invitee", "email": "test@example.com", "answers": [], "booking_confirmed": True, "email_confirmed": True}
    if not setup and not result['questions']:
        recap = await service.book(cfg, operation, params)
        assert recap['status'] == 'confirmation_required'
        params['confirmation_reference'] = recap['confirmation_reference']
    return params


async def due(store, key):
    with store.db() as db:
        db.execute("UPDATE attempts SET lease=0,next_due=0 WHERE id=?", (key,))


async def test_success_duplicate_and_private_result(setup):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    result = await service.book(cfg, "call:one", params)
    assert result["reservation_status"] == "confirmed"
    assert fake.posts == 1
    assert await service.book(cfg, "call:one", {**params, "email": "changed@example.com"}) == result
    assert "test@example.com" not in json.dumps(result)
    assert "cancellations" not in json.dumps(result)
    row = store.attempt(result["attempt_reference"])
    assert "payload" not in json.loads(row["private"])
    assert len(fake.invitee["tracking"]) == 6
    assert fake.invitee["tracking"]["utm_content"].startswith("ava-v1-")
    assert "call:one" not in json.dumps(fake.invitee["tracking"])


async def test_native_recipient_and_complete_recap_required_before_any_post(setup):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    params.pop('confirmation_reference')
    params['email'] = 'test.person+tag@example.com'
    recap = await service.book(cfg, 'call:one', params)
    assert recap['status'] == 'confirmation_required'
    assert recap['confirmation_recap']['email'] == params['email']
    assert 'dot' in recap['confirmation_recap']['email_spelling']
    assert 'plus' in recap['confirmation_recap']['email_spelling']
    assert all(key in recap for key in ['appointment_name','start_datetime','end_datetime','timezone','duration_minutes','location_summary'])
    assert fake.posts == 0 and store.prior('call:one') is None
    public_recap = sanitize_tool_result_for_json_string(recap, tool_name='calendly_calendar')
    assert public_recap['confirmation_reference'] == recap['confirmation_reference']
    assert public_recap['confirmation_recap'] == recap['confirmation_recap']
    confirmed = {**params, 'confirmation_reference': recap['confirmation_reference']}
    with pytest.raises(CalendlyError, match='confirmation_changed_reconfirm'):
        await service.book(cfg, 'call:one', {**confirmed, 'email':'different@example.com'})
    assert fake.posts == 0
    restarted = CalendlyService(CalendlyStore(str(store.path), str(store.token_path)), lambda token: fake)
    assert (await restarted.book(cfg, 'call:one', confirmed))['reservation_status'] == 'confirmed'
    assert fake.posts == 1


async def test_native_confirmation_cannot_reuse_another_calls_reference(setup):
    _, service, fake, cfg = setup
    first = await propose(service, fake, cfg)
    other = await propose(service, fake, cfg, 'call:other')
    with pytest.raises(CalendlyError, match='proposal_expired'):
        await service.book(cfg, 'call:other', {**other, 'confirmation_reference':first['confirmation_reference']})
    assert fake.posts == 0


async def test_conference_display_name_preserves_existing_readback_qualification(setup):
    store, service, fake, cfg = setup
    fake.raw['locations'] = [{'kind':'google_conference'}]
    entry = appointment(fake.raw, USER)
    store.update_connection(store.connection()['revision'], qualified_types={TYPE:digest(entry)})
    params = await propose(service, fake, cfg)
    params.pop('confirmation_reference')
    recap = await service.book(cfg, 'call:one', params)
    assert recap['location_summary'] == 'Google Meet'
    assert store.connection()['qualified_types'][TYPE] == digest(entry)
    assert fake.posts == 0


def test_canonical_recap_and_reference_are_not_persisted_in_tool_history():
    from src.tools.execution_history import build_in_call_tool_record
    record = build_in_call_tool_record(
        tool_name='calendly_calendar', call_id='synthetic', tool_call_id='synthetic',
        parameters={'action':'create_booking','confirmation_reference':'private-reference'},
        result={'status':'confirmation_required','message':'Nothing is booked.',
                'confirmation_reference':'private-reference','confirmation_recap':{'email':'test.person@example.com'}})
    saved = json.dumps(record)
    assert 'private-reference' not in saved and 'test.person@example.com' not in saved


async def test_read_rate_limit_is_shared_after_restart(setup):
    store, service, fake, cfg = setup
    async def limited(*args, **kwargs):
        raise CalendlyError("upstream_429", cooldown=60)
    fake.request = limited
    with pytest.raises(CalendlyError, match="upstream_429"):
        await service.discovery()
    restarted = CalendlyStore(str(store.path), str(store.token_path))
    assert restarted.connection()["cooldown"] > time.time()
    with pytest.raises(CalendlyError, match="rate_limited"):
        restarted.credentials()


async def test_one_transient_read_retry_per_operation_never_replays_post(setup):
    store, _, fake, _ = setup
    calls = []
    async def unavailable(method, *args, **kwargs):
        calls.append(method)
        raise CalendlyError('network_unavailable', uncertain=method == 'POST')
    fake.request = unavailable
    observed = ObservedClient(fake, store, store.connection())
    with pytest.raises(CalendlyError):
        await observed.request('GET', '/users/me')
    assert calls == ['GET','GET']
    with pytest.raises(CalendlyError):
        await observed.request('GET', '/users/me')
    assert calls == ['GET','GET','GET']
    fresh = ObservedClient(fake, store, store.connection())
    with pytest.raises(CalendlyError) as failure:
        await fresh.request('POST', '/invitees')
    assert failure.value.uncertain and calls == ['GET','GET','GET','POST']
    recovered = []
    async def transient(method, *args, **kwargs):
        recovered.append(method)
        if len(recovered) == 1:
            raise CalendlyError('network_unavailable')
        return {'collection': []}
    fake.request = transient
    assert await ObservedClient(fake, store, store.connection()).request('GET', '/event_type_available_times') == {'collection': []}
    assert recovered == ['GET','GET']


async def test_identity_denial_is_distinct_from_booking_eligibility(setup):
    _, service, fake, _ = setup
    async def denied(*args, **kwargs):
        raise CalendlyError("upstream_403")
    fake.request = denied
    with pytest.raises(CalendlyError, match="identity_access_denied"):
        await service.verify("synthetic-private-token")
    assert fake.posts == 0


async def test_verify_needs_candidate_or_saved_token_and_does_not_require_enabled_booking(setup):
    store, service, fake, _ = setup
    conn = store.connection()
    store.update_connection(conn['revision'], enabled=False)
    assert (await service.verify())['uri'] == USER
    assert fake.posts == 0
    store.disconnect(conn['revision'])
    with pytest.raises(CalendlyError, match='verification_token_required'):
        await service.verify()


async def test_setup_can_preview_while_paused_but_cannot_submit(setup):
    store, service, fake, cfg = setup
    store.update_connection(store.connection()['revision'], enabled=False)
    params = await propose(service, fake, cfg, operation='setup:preview', setup=True)
    with pytest.raises(CalendlyError, match='connection_disabled'):
        await service.book(cfg, 'setup:preview', params)
    with pytest.raises(CalendlyError, match='connection_disabled'):
        await propose(service, fake, cfg)
    assert fake.posts == 0 and store.prior('setup:preview') is None


async def test_lost_response_reconciles_after_restart_without_post(setup):
    store, service, fake, cfg = setup
    conn = store.connection()
    store.update_connection(conn["revision"], qualified=True)
    fake.lost = True
    result = await service.book(cfg, "call:one", await propose(service, fake, cfg))
    assert result["status"] == "pending"
    key = result["attempt_reference"]
    await due(store, key)
    restarted = CalendlyService(CalendlyStore(str(store.path), str(store.token_path)), lambda token: fake)
    recovered = await restarted.reconcile(key)
    assert recovered["reservation_status"] == "confirmed"
    assert fake.posts == 1
    query = next(c[2] for c in fake.calls if c[1] == "/scheduled_events")
    assert query["user"] == USER and query["invitee_email"] == "test@example.com"
    assert "event_type" not in query and "utm_content" not in query


@pytest.mark.parametrize("failure", ["no_match", "missing_marker", "overflow"])
async def test_no_negative_proof_or_retry(setup, failure):
    store, service, fake, cfg = setup
    store.update_connection(store.connection()["revision"], qualified=True)
    fake.lost = True
    params = await propose(service, fake, cfg)
    result = await service.book(cfg, "call:one", params)
    setattr(fake, failure, True)
    key = result["attempt_reference"]
    for _ in range(3):
        await due(store, key)
        result = await service.reconcile(key)
    assert result["reservation_status"] == "needs_operator"
    assert (await service.book(cfg, "call:one", params))["status"] == "pending"
    assert fake.posts == 1


@pytest.mark.parametrize('malformed', ['missing_end', 'bad_membership', 'bad_event', 'bad_invitee'])
async def test_malformed_recovery_reaches_staff_without_reposting(setup, malformed):
    store, service, fake, cfg = setup
    store.update_connection(store.connection()['revision'], qualified=True)
    fake.lost = True
    result = await service.book(cfg, 'call:one', await propose(service, fake, cfg))
    key = result['attempt_reference']
    if malformed == 'missing_end': fake.event.pop('end_time')
    if malformed == 'bad_membership': fake.event['event_memberships'] = [None]
    request = fake.request
    async def damaged(method, path, **kwargs):
        value = await request(method, path, **kwargs)
        if malformed == 'bad_event' and path == '/scheduled_events': value['collection'] = [None]
        if malformed == 'bad_invitee' and path.endswith('/invitees'): value['collection'] = ['invalid']
        return value
    fake.request = damaged
    for _ in range(3):
        await due(store, key)
        result = await service.reconcile(key)
        assert result['code'] == 'invalid_response'
    assert result['reservation_status'] == 'needs_operator' and fake.posts == 1


async def test_malformed_qualification_is_safe_and_never_qualifies(setup):
    store, service, fake, cfg = setup
    operation = 'setup:malformed'
    result = await service.book(cfg, operation, await propose(service, fake, cfg, operation, setup=True))
    fake.event.pop('end_time')
    with pytest.raises(CalendlyError, match='invalid_response'):
        await service.reconcile(result['attempt_reference'], qualification=True)
    assert not store.connection()['qualified'] and fake.posts == 1


async def test_unqualified_discovery_and_external_resolution(setup):
    store, service, fake, cfg = setup
    fake.lost = True
    params = await propose(service, fake, cfg)
    result = await service.book(cfg, "call:one", params)
    key = result["attempt_reference"]
    await due(store, key)
    assert (await service.reconcile(key))["reservation_status"] == "needs_operator"
    store.resolve(key, "admin", "Verified externally")
    assert (await service.book(cfg, "call:one", params))["reservation_status"] == "resolved_externally"
    assert fake.posts == 1


async def test_setup_readback_qualification(setup):
    store, service, fake, cfg = setup
    operation = "setup:123"
    result = await service.book(cfg, operation, await propose(service, fake, cfg, operation, setup=True))
    assert not store.connection()["qualified"]
    await service.reconcile(result["attempt_reference"], qualification=True)
    assert store.connection()["qualified"]
    assert fake.posts == 1


async def test_remote_cancelled_is_not_active_success(setup):
    store, service, fake, cfg = setup
    fake.lost = True
    store.update_connection(store.connection()["revision"], qualified=True)
    result = await service.book(cfg, "call:one", await propose(service, fake, cfg))
    fake.event["status"] = fake.invitee["status"] = "canceled"
    await due(store, result["attempt_reference"])
    result = await service.reconcile(result["attempt_reference"])
    assert result["reservation_status"] == "resolved_remote_cancelled"
    assert result["status"] != "success" and fake.posts == 1


async def cancelled_test(setup):
    store, service, fake, cfg = setup
    old = await service.book(cfg, 'setup:old', await propose(service, fake, cfg, 'setup:old', setup=True))
    fake.event['status'] = fake.invitee['status'] = 'canceled'
    return old


async def test_new_call_can_book_positively_cancelled_duplicate_without_replaying_old_operation(setup):
    store, service, fake, cfg = setup
    old = await cancelled_test(setup)
    params = await propose(service, fake, cfg, 'call:new')
    result = await service.book(cfg, 'call:new', params)
    assert result['reservation_status'] == 'confirmed' and fake.posts == 2
    assert store.attempt(old['attempt_reference'])['state'] == 'verified_cancelled'
    assert (await service.book(cfg, 'setup:old', {}))['reservation_status'] == 'verified_cancelled'
    assert fake.posts == 2
    store.cleanup(['new'])
    assert (await service.book(cfg, 'call:new', {}))['reservation_status'] == 'erased'
    assert fake.posts == 2


@pytest.mark.parametrize('fault', ['active_event', 'active_invitee', 'wrong_host', 'wrong_type', 'wrong_time',
                                  'wrong_duration', 'wrong_marker', 'rescheduled', 'wrong_invitee',
                                  'wrong_event', 'missing_details', 'uncertain', 'not_found', 'null_membership', 'scalar_membership'])
async def test_unproven_cancellation_never_releases_duplicate(setup, fault):
    store, service, fake, cfg = setup
    old = await cancelled_test(setup)
    if fault == 'active_event': fake.event['status'] = 'active'
    if fault == 'active_invitee': fake.invitee['status'] = 'active'
    if fault == 'wrong_host': fake.event['event_memberships'] = [{'user': USER + '-other'}]
    if fault == 'null_membership': fake.event['event_memberships'] = [None]
    if fault == 'scalar_membership': fake.event['event_memberships'] = ['invalid']
    if fault == 'wrong_type': fake.event['event_type'] = TYPE + '-other'
    if fault == 'wrong_time': fake.event['start_time'] = (datetime.fromisoformat(fake.start)+timedelta(minutes=1)).isoformat()
    if fault == 'wrong_duration': fake.event['end_time'] = fake.start
    if fault == 'wrong_marker': fake.invitee['tracking']['utm_content'] = 'wrong'
    if fault == 'rescheduled': fake.invitee['rescheduled'] = True
    if fault == 'wrong_invitee': fake.invitee['uri'] = INVITEE + '-other'
    if fault == 'wrong_event': fake.event['uri'] = EVENT + '-other'
    if fault in {'missing_details', 'uncertain'}:
        row = store.attempt(old['attempt_reference'])
        # Inject retention/recovery states directly; normal finish correctly
        # refuses to rewrite an already settled row.
        with store.db() as db:
            db.execute('UPDATE attempts SET state=?,private=? WHERE id=?',
                       ('uncertain' if fault == 'uncertain' else 'confirmed', '{}' if fault == 'missing_details' else row['private'], row['id']))
    if fault == 'not_found':
        original = fake.request
        async def request(method, path, **kw):
            if path == '/scheduled_events/booking': raise CalendlyError('upstream_404')
            return await original(method, path, **kw)
        fake.request = request
    result = await service.book(cfg, 'call:new', await propose(service, fake, cfg, 'call:new'))
    assert result['code'] == 'existing_request_needs_staff'
    assert fake.posts == 1
    assert store.attempt(old['attempt_reference'])['state'] != 'verified_cancelled'


async def test_concurrent_calls_after_verified_cancellation_submit_only_once(setup):
    store, service, fake, cfg = setup
    await cancelled_test(setup)
    a = await propose(service, fake, cfg, 'call:a')
    b = await propose(service, fake, cfg, 'call:b')
    results = await asyncio.gather(service.book(cfg, 'call:a', a), service.book(cfg, 'call:b', b))
    assert fake.posts == 2
    assert sum(result.get('reservation_status') == 'confirmed' for result in results) == 1


async def test_disable_during_cancellation_readback_still_blocks_admission(setup):
    store, service, fake, cfg = setup
    await cancelled_test(setup)
    params = await propose(service, fake, cfg, 'call:new')
    original = fake.request
    async def request(method, path, **kw):
        result = await original(method, path, **kw)
        if path == '/scheduled_events/booking/invitees/person':
            store.update_connection(store.connection()['revision'], enabled=False)
        return result
    fake.request = request
    with pytest.raises(CalendlyError, match='connection_changed'):
        await service.book(cfg, 'call:new', params)
    assert fake.posts == 1 and store.prior('call:new') is None


async def test_spoken_weekday_uses_local_date_and_survives_sanitizer(setup):
    _, service, fake, cfg = setup
    # UTC Sunday is Saturday in Pacific time. Avoid a moving fixed date by
    # selecting the next UTC Sunday, including a possible DST transition.
    when = datetime.now(timezone.utc) + timedelta(days=8)
    when += timedelta(days=(6-when.weekday()) % 7)
    when = when.replace(hour=1, minute=0, second=0, microsecond=0)
    fake.start = when.isoformat()
    start = when - timedelta(minutes=1)
    slots = await service.slots(cfg, 'call:date', 'consultation', start.isoformat(), (start+timedelta(days=1)).isoformat(), 'America/Los_Angeles')
    assert slots['slots'][0]['spoken_date'].startswith('Saturday,')
    recap = await service.book(cfg, 'call:date', {'slot_reference': slots['slots'][0]['slot_reference'], 'name': 'Test', 'email': 'test@example.com'})
    assert recap['spoken_date'] == slots['slots'][0]['spoken_date']
    safe = sanitize_tool_result_for_json_string(recap, tool_name='calendly_calendar')
    assert safe['spoken_date'].startswith('Saturday,')


async def native_confirmation(setup, monkeypatch, call='native', answers=None):
    from src.tools.business import calendly_calendar as native
    from src.tools.context import ToolExecutionContext
    from src.core.models import CallSession
    from src.core.session_store import SessionStore
    store, service, fake, cfg = setup
    monkeypatch.setattr(native, 'CalendlyStore', lambda: store)
    monkeypatch.setattr(native, 'CalendlyService', lambda _: service)
    sessions = SessionStore()
    session = CallSession(call_id=call, caller_channel_id=call)
    await sessions.upsert_call(session)
    context = ToolExecutionContext(call_id=call, session_store=sessions, config={'tools': {'calendly_calendar': cfg}})
    tool = native.CalendlyCalendarTool()
    slots = await tool.execute({'action': 'get_available_slots', 'appointment_key': 'consultation', 'timezone': 'America/Los_Angeles'}, context)
    params = {'action': 'create_booking', 'slot_reference': slots['slots'][0]['slot_reference'], 'name': 'Test', 'email': 'test@example.com', 'answers': answers or []}
    recap = await tool.execute(params, context)
    assert recap['status'] == 'confirmation_required'
    return tool, context, session, params, recap


async def test_native_reference_only_approval_submits_exact_prepared_details_once(setup, monkeypatch):
    from dataclasses import asdict
    _, _, fake, _ = setup
    tool, context, session, _, recap = await native_confirmation(setup, monkeypatch)
    final = {'action': 'create_booking', 'confirmation_reference': recap['confirmation_reference'], 'email_confirmed': True, 'booking_confirmed': True}
    assert '_calendly_confirmation' not in asdict(session)
    assert '_calendly_confirmation' not in repr(session)
    result = await tool.execute(final, context)
    assert result['reservation_status'] == 'confirmed' and fake.posts == 1
    assert fake.invitee['name'] == 'Test' and fake.invitee['email'] == 'test@example.com'
    assert not session._calendly_confirmation
    assert await tool.execute(final, context) == result
    assert fake.posts == 1


@pytest.mark.parametrize('selection,expected', [(None, 1), ([], 0)])
async def test_null_native_selection_matches_binding_and_guidance(setup, monkeypatch, selection, expected):
    from src.tools.business import calendly_calendar as native
    from src.tools.context import ToolExecutionContext
    from src.tools.runtime_guidance import build_in_call_tool_runtime_guidance
    store, service, fake, cfg = setup
    cfg['selected_appointments'] = selection
    monkeypatch.setattr(native, 'CalendlyStore', lambda: store)
    monkeypatch.setattr(native, 'CalendlyService', lambda _: service)
    context = ToolExecutionContext(call_id='null-selection', config={'tools': {'calendly_calendar': cfg}})
    tool = native.CalendlyCalendarTool()
    result = await tool.execute({'action': 'list_appointment_types'}, context)
    assert result['status'] == 'success' and len(result['appointment_types']) == expected
    guidance = build_in_call_tool_runtime_guidance(context.config, ['calendly_calendar'])
    assert ('consultation' in guidance) == bool(expected)
    slots = await tool.execute({'action': 'get_available_slots', 'appointment_key': 'consultation', 'timezone': 'UTC'}, context)
    assert slots['status'] == ('success' if expected else 'error')
    assert fake.posts == 0


@pytest.mark.parametrize('fault', ['missing_flags', 'changed_email', 'changed_name', 'wrong_reference', 'expired', 'missing_cache', 'cleanup', 'disabled'])
async def test_native_confirmation_hydration_never_infers_approval_or_bypasses_checks(setup, monkeypatch, fault):
    store, _, fake, _ = setup
    tool, context, session, _, recap = await native_confirmation(setup, monkeypatch)
    final = {'action': 'create_booking', 'confirmation_reference': recap['confirmation_reference'], 'email_confirmed': True, 'booking_confirmed': True}
    if fault == 'missing_flags': final.pop('booking_confirmed')
    if fault == 'changed_email': final['email'] = 'different@example.com'
    if fault == 'changed_name': final['name'] = 'Different'
    if fault == 'wrong_reference': final['confirmation_reference'] = 'invented'
    if fault == 'expired': session._calendly_confirmation['expires'] = 0
    if fault == 'missing_cache': session._calendly_confirmation = {}
    if fault == 'cleanup': session.cleanup_in_progress = True
    if fault == 'disabled': store.update_connection(store.connection()['revision'], enabled=False)
    result = await tool.execute(final, context)
    assert result['status'] == 'error' and fake.posts == 0
    assert store.prior('call:native') is None


async def test_correction_replaces_pending_reference_and_requires_new_approval(setup, monkeypatch):
    _, _, fake, _ = setup
    tool, context, _, params, first = await native_confirmation(setup, monkeypatch)
    second = await tool.execute({**params, 'email': 'corrected@example.com'}, context)
    assert second['status'] == 'confirmation_required'
    stale = await tool.execute({'action': 'create_booking', 'confirmation_reference': first['confirmation_reference'], 'email_confirmed': True, 'booking_confirmed': True}, context)
    assert stale['code'] == 'confirmation_changed_reconfirm' and fake.posts == 0
    result = await tool.execute({'action': 'create_booking', 'confirmation_reference': second['confirmation_reference'], 'email_confirmed': True, 'booking_confirmed': True}, context)
    assert result['reservation_status'] == 'confirmed' and fake.invitee['email'] == 'corrected@example.com'


async def test_native_reference_only_preserves_required_answers_without_caching_unknown_fields(setup, monkeypatch):
    _, _, fake, _ = setup
    fake.raw['custom_questions'] = [{'name': 'Company?', 'position': 2, 'enabled': True, 'required': True, 'type': 'single_select', 'answer_choices': ['One', 'Two']}]
    tool, context, session, _, recap = await native_confirmation(setup, monkeypatch, answers=[{'position': 2, 'answer': 'One', 'ignored': 'do not retain'}])
    assert session._calendly_confirmation['parameters']['answers'] == [{'position': 2, 'answer': 'One'}]
    result = await tool.execute({'action': 'create_booking', 'confirmation_reference': recap['confirmation_reference'], 'email_confirmed': True, 'booking_confirmed': True}, context)
    assert result['reservation_status'] == 'confirmed' and fake.posts == 1
    assert fake.invitee['questions_and_answers'] == [{'question': 'Company?', 'position': 2, 'answer': 'One'}]


async def test_concurrent_same_operation_one_post(setup):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    fake.pause = asyncio.Event()
    first = asyncio.create_task(service.book(cfg, "call:one", params))
    await asyncio.wait_for(fake.accepted.wait(), 3)
    second = await service.book(cfg, "call:one", params)
    assert second["status"] == "pending"
    fake.pause.set()
    assert (await first)["status"] == "success"
    assert fake.posts == 1


async def test_cancel_after_remote_commit_retains_journal(setup):
    store, service, fake, cfg = setup
    fake.pause = asyncio.Event()
    params = await propose(service, fake, cfg)
    task = asyncio.create_task(service.book(cfg, "call:one", params))
    await asyncio.wait_for(fake.accepted.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.prior("call:one")["state"] == "uncertain"
    assert (await service.book(cfg, "call:one", params))["status"] == "pending"
    assert fake.posts == 1


async def test_disconnect_and_different_account_fail_closed(setup):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    store.disconnect(store.connection()["revision"])
    with pytest.raises(CalendlyError):
        await service.book(cfg, "call:one", params)
    conn = store.save_connection({**IDENTITY, "uri": USER + "2"}, "new-synthetic-token", store.connection()["revision"])
    store.update_connection(conn["revision"], enabled=True)
    with pytest.raises(CalendlyError):
        await service.book(cfg, "call:one", params)
    assert fake.posts == 0


@pytest.mark.parametrize("field", ["email_confirmed", "booking_confirmed"])
async def test_no_confirmation_no_post(setup, field):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    params[field] = False
    with pytest.raises(CalendlyError, match="confirmation_required"):
        await service.book(cfg, "call:one", params)
    assert fake.posts == 0


async def test_metadata_change_requires_reconfirmation(setup):
    _, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    fake.raw["duration"] = 45
    with pytest.raises(CalendlyError, match="proposal_changed_reconfirm"):
        await service.book(cfg, "call:one", params)
    assert fake.posts == 0


async def test_required_questions_exact_payload_and_redaction(setup):
    _, service, fake, cfg = setup
    fake.raw["custom_questions"] = [{"name": "Company?", "position": 2, "enabled": True, "required": True, "type": "single_select", "answer_choices": ["One", "Two"]}]
    params = await propose(service, fake, cfg)
    with pytest.raises(CalendlyError):
        await service.book(cfg, "call:one", params)
    params["answers"] = [{"position": 2, "answer": "One"}]
    recap = await service.book(cfg, "call:one", params)
    assert recap['status'] == 'confirmation_required'
    params['confirmation_reference'] = recap['confirmation_reference']
    assert (await service.book(cfg, "call:one", params))["status"] == "success"
    assert fake.invitee["questions_and_answers"] == [{"question": "Company?", "position": 2, "answer": "One"}]


@pytest.mark.parametrize("patch", [{"kind": "group"}, {"pooling_type": "round_robin"}, {"is_paid": True}, {"duration_options": [15, 30]}, {"active": False}, {"booking_method": "poll"}])
def test_unsupported_metadata(setup, patch):
    _, _, fake, _ = setup
    assert not appointment({**fake.raw, **patch}, USER)["supported"]


def test_paths_config_email_and_scope(setup):
    _, _, _, cfg = setup
    validate_config(cfg)
    with pytest.raises(ValueError):
        validate_config({**cfg, "token": "must-not-be-in-yaml"})
    for uri in ["https://evil.invalid/users/a", "http://api.calendly.com/users/a", "https://api.calendly.com/users/../a", USER+"?token=x"]:
        with pytest.raises(CalendlyError): resource_path(uri)
    assert email("Test+tag@EXAMPLE.com") == "Test+tag@example.com"
    with pytest.raises(CalendlyError): email("test@example.com\r\nBcc: other@example.com")
    scoped = resolve_agent_tool_config({"tools": {"calendly_calendar": cfg}}, {"calendly_calendar": {"appointment_policy": "selected", "appointment_keys": []}})
    assert scoped.config["tools"]["calendly_calendar"]["selected_appointments"] == []


def test_existing_calls_do_not_gain_calendly_without_explicit_tool_assignment():
    from src.tools.base import ToolPhase
    from src.tools.registry import ToolRegistry
    from src.tools.business.calendly_calendar import CalendlyCalendarTool
    registry = ToolRegistry.isolated()
    registry.register(CalendlyCalendarTool)
    assert registry.get_tools_for_context(ToolPhase.IN_CALL, context_tool_names=[]) == []
    assert [tool.definition.name for tool in registry.get_tools_for_context(
        ToolPhase.IN_CALL, context_tool_names=['calendly_calendar'])] == ['calendly_calendar']


def test_permission_and_retention_tombstone(setup):
    store, _, _, cfg = setup
    assert store.token_path.stat().st_mode & 0o077 == 0
    assert store.path.stat().st_mode & 0o077 == 0
    payload = {"event_type": TYPE, "start_time": "2030-01-01T10:00:00+00:00", "invitee": {"email": "test@example.com"}}
    row, admitted = store.admit("call:retained", store.connection(), payload, {})
    assert admitted
    store.cleanup(["retained"])
    assert store.attempt(row["id"])["private"] == "{}"
    assert store.prior("call:retained") is not None
    with store.db() as db:
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 1000


def test_provider_structured_slots_survive():
    result = sanitize_tool_result_for_json_string({"status": "success", "slots": [{"slot_reference": "safe", "start_datetime": "2030-01-01T12:00:00Z"}], "questions": [{"question": "Purpose?"}], "private": {"token": "secret"}}, tool_name="calendly_calendar")
    assert result["slots"][0]["slot_reference"] == "safe"
    assert "private" not in result


async def test_native_next_availability_defaults_and_correctable_inputs(setup, monkeypatch):
    from src.tools.business import calendly_calendar as native
    from src.tools.context import ToolExecutionContext
    store, service, fake, cfg = setup
    monkeypatch.setattr(native, 'CalendlyStore', lambda: store)
    monkeypatch.setattr(native, 'CalendlyService', lambda _: service)
    context = ToolExecutionContext(call_id='next', config={'tools': {'calendly_calendar': cfg}})
    tool = native.CalendlyCalendarTool()
    params = {'action': 'get_available_slots', 'appointment_key': 'consultation'}
    missing_timezone = await tool.execute(params, context)
    assert missing_timezone['code'] == 'invalid_timezone'
    assert 'timezone' in missing_timezone['message']
    assert not fake.calls
    before = datetime.now(timezone.utc)
    result = await tool.execute({**params, 'timezone': 'America/Los_Angeles'}, context)
    assert result['status'] == 'success' and len(result['slots']) == 1
    a, b = map(datetime.fromisoformat, [result['search_start'], result['search_end']])
    assert before + timedelta(seconds=59) < a < datetime.now(timezone.utc) + timedelta(seconds=61)
    assert b - a == timedelta(days=7)
    explicit = (before + timedelta(days=1)).isoformat()
    result = await tool.execute({**params, 'timezone': 'America/Los_Angeles', 'start_datetime': explicit}, context)
    assert result['search_start'] == explicit
    assert datetime.fromisoformat(result['search_end']) - datetime.fromisoformat(explicit) == timedelta(days=7)
    for changes, code in [({'end_datetime': explicit}, 'search_start_required'),
                          ({'start_datetime': 'not-a-date'}, 'invalid_datetime'),
                          ({'start_datetime': (before-timedelta(days=1)).isoformat()}, 'invalid_search_window')]:
        invalid = await tool.execute({**params, 'timezone': 'America/Los_Angeles', **changes}, context)
        assert invalid['status'] == 'error' and invalid['code'] == code
        assert 'No booking was submitted' in invalid['message']
    invented = await tool.execute({'action': 'create_booking', 'slot_reference': fake.start}, context)
    assert invented['code'] == 'proposal_expired'
    assert 'exact returned slot_reference' in invented['message']
    assert fake.posts == 0 and store.prior('call:next') is None


async def test_next_availability_sorts_before_limiting_and_honors_window(setup):
    _, service, fake, cfg = setup
    original = fake.request
    start = datetime.now(timezone.utc) + timedelta(minutes=1)
    end = start + timedelta(days=7)
    async def request(method, path, **kwargs):
        if path == '/event_type_available_times':
            times = [end, start-timedelta(seconds=1)] + [start+timedelta(hours=i) for i in range(12, 0, -1)]
            return {'collection': [{'status': 'available', 'invitees_remaining': 1, 'start_time': t.isoformat()} for t in times]}
        return await original(method, path, **kwargs)
    fake.request = request
    result = await service.slots(cfg, 'call:earliest', 'consultation', start.isoformat(), end.isoformat(), 'America/Los_Angeles')
    assert [datetime.fromisoformat(row['start_datetime']).astimezone(timezone.utc) for row in result['slots']] == [start+timedelta(hours=i) for i in range(1, 11)]
    assert result['slots_truncated'] is True and fake.posts == 0


@pytest.mark.parametrize('setup_mode', [False, True])
async def test_confirmation_reference_as_slot_is_correctable_before_admission(setup, setup_mode):
    store, service, fake, cfg = setup
    operation = 'setup:one' if setup_mode else 'call:one'
    ref = store.proposal(operation, store.connection()['id'], {'confirmation_digest': 'not-a-slot'})
    with pytest.raises(CalendlyError, match='proposal_expired'):
        await service.book(cfg, operation, {'slot_reference': ref})
    assert fake.posts == 0 and store.prior(operation) is None


@pytest.mark.parametrize('fault', ['cleanup', 'history_setup', 'purge', 'due', 'reconcile', 'cancel'])
async def test_recovery_failures_are_isolated_and_cancellation_propagates(tmp_path, monkeypatch, fault, caplog):
    from types import SimpleNamespace
    from src.tools.business import calendly_service as module
    from src.core import call_history
    journal = tmp_path / 'db'; journal.touch()
    history = tmp_path / 'history'; history.touch()
    monkeypatch.setenv('CALENDLY_DB_PATH', str(journal))
    monkeypatch.setenv('CALL_HISTORY_DB_PATH', str(history))
    calls = []
    passes = 0
    def fail(): raise sqlite3.OperationalError('private diagnostic test@example.com')
    def cleanup():
        calls.append('cleanup')
        if fault == 'cleanup': fail()
    def due_keys():
        calls.append('due')
        if fault == 'due' and passes == 0: fail()
        return ['bad', 'good']
    async def purge():
        calls.append('purge')
        if fault == 'purge': fail()
    def get_history():
        calls.append('history')
        if fault == 'history_setup': fail()
        return SimpleNamespace(retry_pending_calendly_purges=purge)
    async def reconcile(key):
        calls.append(key)
        if key == 'bad' and fault == 'reconcile': fail()
        if key == 'bad' and fault == 'cancel': raise asyncio.CancelledError()
    async def stop(_):
        nonlocal passes
        passes += 1
        if fault != 'due' or passes == 2: raise asyncio.CancelledError()
    store = SimpleNamespace(cleanup=cleanup, due=due_keys)
    monkeypatch.setattr(module, 'CalendlyStore', lambda: store)
    monkeypatch.setattr(module, 'CalendlyService', lambda _: SimpleNamespace(reconcile=reconcile))
    monkeypatch.setattr(call_history, 'get_call_history_store', get_history)
    monkeypatch.setattr(module.asyncio, 'sleep', stop)
    with pytest.raises(asyncio.CancelledError): await module.recovery_loop()
    if fault == 'cancel': assert 'good' not in calls and passes == 0
    else: assert calls[-2:] == ['bad', 'good']
    if fault == 'due': assert calls.count('due') == 2
    assert 'private diagnostic' not in caplog.text and 'test@example.com' not in caplog.text


@pytest.mark.parametrize('manual', [False, True])
async def test_local_cooldown_preserves_recovery_budget_and_resumes_after_expiry(setup, monkeypatch, manual):
    store, service, fake, cfg = setup
    store.update_connection(store.connection()['revision'], qualified=True)
    fake.lost = True
    result = await service.book(cfg, 'call:one', await propose(service, fake, cfg))
    key = result['attempt_reference']
    await due(store, key)
    assert store.due() == [key]
    store.observe_cooldown(store.connection(), 3600)  # Can race after due selection.
    before = store.attempt(key)
    reads = len(fake.calls)
    for _ in range(4):
        assert store.due() == []
        with pytest.raises(CalendlyError, match='rate_limited'):
            await service.reconcile(key, manual=manual)
    after = store.attempt(key)
    assert [(after[k], before[k]) for k in ['state', 'passes', 'checks', 'next_due', 'lease']] == [(before[k], before[k]) for k in ['state', 'passes', 'checks', 'next_due', 'lease']]
    assert len(fake.calls) == reads and fake.posts == 1
    future = store.connection()['cooldown'] + 1
    monkeypatch.setattr('src.core.calendly_store.time.time', lambda: future)
    assert store.due() == [key]
    assert (await service.reconcile(key, manual=manual))['reservation_status'] == 'confirmed'
    assert fake.posts == 1 and store.attempt(key)['passes'] == (0 if manual else 1)


@pytest.mark.parametrize('invalid', [{'one_to_one_confirmed': False}, {'connection_id': 'other'}])
async def test_incomplete_appointment_does_not_hide_valid_agent_appointments(setup, monkeypatch, invalid):
    from src.tools.business import calendly_calendar as native
    from src.tools.context import ToolExecutionContext
    store, service, fake, cfg = setup
    cfg['appointments']['incomplete'] = {**cfg['appointments']['consultation'], **invalid}
    monkeypatch.setattr(native, 'CalendlyStore', lambda: store)
    monkeypatch.setattr(native, 'CalendlyService', lambda _: service)
    context = ToolExecutionContext(call_id='list-mixed', config={'tools': {'calendly_calendar': cfg}})
    tool = native.CalendlyCalendarTool()
    result = await tool.execute({'action': 'list_appointment_types'}, context)
    assert result['status'] == 'success' and [v['key'] for v in result['appointment_types']] == ['consultation']
    denied = await tool.execute({'action': 'get_available_slots', 'appointment_key': 'incomplete', 'timezone': 'UTC'}, context)
    assert denied['status'] == 'error' and denied['code'] == 'appointment_setup_incomplete'
    assert fake.posts == 0


async def erase_history_during_booking(store, monkeypatch):
    from src.core.call_history import CallHistoryStore, CallRecord
    monkeypatch.setenv('CALL_HISTORY_ENABLED', 'true')
    monkeypatch.setenv('CALL_HISTORY_RETENTION_DAYS', '0')
    monkeypatch.setenv('CALENDLY_DB_PATH', str(store.path))
    monkeypatch.setenv('CALENDLY_TOKEN_PATH', str(store.token_path))
    history = CallHistoryStore(str(store.path.parent / 'erased-history.db'))
    now = datetime.now(timezone.utc)
    await history.save(CallRecord(call_id='one', start_time=now, end_time=now))
    record = await history.get_by_call_id('one')
    assert await history.delete(record.id)
    assert await history.get_by_call_id('one') is None
    with sqlite3.connect(history._db_path) as db:
        assert db.execute('SELECT * FROM pending_calendly_purges').fetchall() == []


def assert_erased_barrier(store, key, before):
    row = store.attempt(key)
    assert row['state'] == 'erased' and row['lease'] == 0
    assert row['private'] == row['public'] == '{}'
    assert row['operation'] == before['operation'] and row['duplicate_key'] == before['duplicate_key']
    assert key not in store.due()
    payload = json.loads(before['private'])['payload']
    assert store.admit('call:other', store.connection(), payload, {})[1] is False
    with pytest.raises(CalendlyError, match='resolution_not_allowed'):
        store.resolve(key, 'operator', 'Must not add details after erasure')


@pytest.mark.parametrize('outcome', ['confirmed', 'uncertain', 'cancelled'])
async def test_deleted_history_cannot_be_repopulated_by_inflight_booking_finish(setup, monkeypatch, outcome):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    fake.pause = asyncio.Event()
    fake.lost = outcome == 'uncertain'
    task = asyncio.create_task(service.book(cfg, 'call:one', params))
    await asyncio.wait_for(fake.accepted.wait(), timeout=3)
    before = store.prior('call:one')
    await erase_history_during_booking(store, monkeypatch)
    assert_erased_barrier(store, before['id'], before)
    if outcome == 'cancelled':
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    else:
        fake.pause.set()
        result = await task
        assert result['reservation_status'] == 'erased' and result['status'] == 'error'
        assert 'does not cancel' in result['message']
    assert_erased_barrier(store, before['id'], before)
    result = await service.book(cfg, 'call:one', params)
    assert result['reservation_status'] == 'erased' and fake.posts == 1


async def test_deleted_history_cannot_be_repopulated_by_inflight_reconciliation(setup, monkeypatch):
    store, service, fake, cfg = setup
    store.update_connection(store.connection()['revision'], qualified=True)
    fake.lost = True
    result = await service.book(cfg, 'call:one', await propose(service, fake, cfg))
    key = result['attempt_reference']
    await due(store, key)
    entered, release = asyncio.Event(), asyncio.Event()
    original = fake.request
    async def pause(method, path, **kwargs):
        if path == '/scheduled_events':
            entered.set()
            await release.wait()
        return await original(method, path, **kwargs)
    fake.request = pause
    task = asyncio.create_task(service.reconcile(key))
    await asyncio.wait_for(entered.wait(), timeout=3)
    before = store.attempt(key)
    # Reconciliation has read its private lookup details before the delete.
    await erase_history_during_booking(store, monkeypatch)
    release.set()
    assert (await task)['reservation_status'] == 'erased'
    assert store.attempt(key)['private'] == store.attempt(key)['public'] == '{}'
    assert store.attempt(key)['state'] == 'erased' and key not in store.due()
    assert store.prior('call:one')['id'] == key and fake.posts == 1
    assert before['passes'] == 1



async def test_history_erasure_before_admission_blocks_prepared_booking_and_creates_no_private_attempt(setup, monkeypatch):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    assert store.prior('call:one') is None
    await erase_history_during_booking(store, monkeypatch)
    result = await service.book(cfg, 'call:one', params)
    assert result['reservation_status'] == 'erased' and fake.posts == 0
    row = store.prior('call:one')
    assert row['state'] == 'erased' and row['private'] == row['public'] == '{}'
    assert row['duplicate_key'].startswith('erased:')



async def test_erasing_a_verified_cancelled_call_does_not_reblock_another_calls_booking(setup):
    store, service, fake, cfg = setup
    params = await propose(service, fake, cfg)
    first = await service.book(cfg, 'call:one', params)
    old = store.attempt(first['attempt_reference'])
    fake.event['status'] = fake.invitee['status'] = 'canceled'
    conn, client = await service.credentials()
    assert await service.verify_cancelled_duplicate(old, conn, client)
    store.cleanup(['one'])
    assert (await service.book(cfg, 'call:one', params))['reservation_status'] == 'erased'
    new_params = await propose(service, fake, cfg, 'call:replacement')
    result = await service.book(cfg, 'call:replacement', new_params)
    assert result['reservation_status'] == 'confirmed' and fake.posts == 2
    assert store.attempt(old['id'])['state'] == 'erased'
    assert store.attempt(old['id'])['private'] == store.attempt(old['id'])['public'] == '{}'
