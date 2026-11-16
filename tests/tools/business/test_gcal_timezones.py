"""Offline Google Calendar datetime contract and offer-to-book lifecycle tests."""

import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from src.tools.business.gcal_tool import GCalendarTool
from src.tools.business.gcalendar import GCalendar, GoogleCalendarApiError


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc)


@pytest.mark.parametrize(('supplied', 'expected'), [
    ('2030-04-26T14:00:00Z', '2030-04-26T10:00:00-04:00'),
    ('2030-04-26T14:00:00z', '2030-04-26T10:00:00-04:00'),
    ('2030-04-26T16:00:00+02:00', '2030-04-26T10:00:00-04:00'),
    ('2030-04-26T07:00:00-07:00', '2030-04-26T10:00:00-04:00'),
    ('2030-04-26T02:00:00Z', '2030-04-25T22:00:00-04:00'),
    ('2030-01-26T14:00:00Z', '2030-01-26T09:00:00-05:00'),
    ('2030-11-03T06:15:00Z', '2030-11-03T01:15:00-05:00'),
    ('2030-03-10T07:15:00Z', '2030-03-10T03:15:00-04:00'),
    ('2030-04-26T14:00:00', '2030-04-26T14:00:00-04:00'),
    ('2030-01-26T14:00:00', '2030-01-26T14:00:00-05:00'),
])
def test_datetime_contract(supplied, expected):
    result = GCalendarTool()._normalize_datetime_to_calendar_tz(supplied, 'America/New_York')
    assert result.isoformat() == expected
    if supplied.endswith(('Z', 'z')) or '+' in supplied[10:] or '-' in supplied[10:]:
        assert result.astimezone(timezone.utc) == instant(supplied.upper())


def make_tool(monkeypatch, zones, *, selected=None, mode='freebusy', legacy=False):
    """Run the real Google wrapper against in-memory Google API responses."""
    tool = GCalendarTool()
    config = {'enabled': True, 'working_days': list(range(7)), 'timezone': 'Pacific/Auckland'}
    clients = {}
    stores = {}
    for key, zone in zones.items():
        client = GCalendar.__new__(GCalendar)
        client.calendar_id = key
        client.timezone = zone
        client._lock = threading.Lock()
        client.service = MagicMock()
        events = client.service.events.return_value
        store = {}
        stores[key] = store

        def insert(*, body, calendarId, store=store):
            event = {**body, 'id': f'event-{len(store)}', 'htmlLink': 'https://example.invalid/event'}
            store[event['id']] = event
            return SimpleNamespace(execute=lambda: event)

        def get(*, eventId, calendarId, store=store):
            return SimpleNamespace(execute=lambda: store.get(eventId))

        def delete(*, eventId, calendarId, store=store):
            return SimpleNamespace(execute=lambda: store.pop(eventId, None))

        events.insert.side_effect = insert
        events.get.side_effect = get
        events.delete.side_effect = delete
        events.list.return_value.execute.side_effect = lambda store=store: {'items': list(store.values())}
        client.service.freebusy.return_value.query.return_value.execute.return_value = {
            'calendars': {key: {'busy': []}}
        }
        clients[key] = client
    if legacy:
        key = next(iter(zones))
        config.update(calendar_id=key, timezone=zones[key], credentials_path='unused')
        monkeypatch.setattr(tool, '_get_cal', Mock(return_value=clients[key]))
        monkeypatch.setattr(tool, '_get_or_create_cal', Mock(return_value=clients[key]))
    else:
        config['calendars'] = {k: {'calendar_id': k, 'timezone': z} for k, z in zones.items()}
        monkeypatch.setattr(tool, '_get_or_create_cal', Mock(side_effect=lambda creds, key, tz, subject: clients[key]))
    if selected is not None:
        config['selected_calendars'] = selected
    if mode == 'title_prefix':
        config['free_prefix'] = 'Open'
        for store in stores.values():
            store['open'] = {'id': 'open', 'summary': 'Open hours',
                             'start': {'dateTime': '2030-04-26T14:00:00Z'},
                             'end': {'dateTime': '2030-04-26T16:00:00Z'}}
    monkeypatch.setattr(tool, '_get_config', Mock(return_value=config))
    return tool, clients


CTX = SimpleNamespace(call_id='synthetic-timezone-call')


@pytest.mark.asyncio
@pytest.mark.parametrize('zones,legacy', [
    ({'A': 'America/New_York', 'B': 'Europe/London'}, False),
    ({'A': 'America/New_York', 'B': 'America/New_York'}, False),
    ({'A': 'America/New_York'}, False),
    ({'default': 'America/New_York'}, True),
])
@pytest.mark.parametrize('mode', ['freebusy', 'title_prefix'])
@pytest.mark.parametrize('booking_format', ['as_returned', 'utc_z'])
async def test_offer_book_read_delete_preserves_instant(monkeypatch, zones, legacy, mode, booking_format):
    tool, clients = make_tool(monkeypatch, zones, legacy=legacy, mode=mode)
    result = await tool.execute({'action': 'get_free_slots', 'time_min': '2030-04-26T14:00:00Z',
                                 'time_max': '2030-04-26T18:00:00+02:00', 'duration': 30}, CTX)
    assert result['status'] == 'success' and result['slots']
    assert instant(result['slots'][0]) == instant('2030-04-26T14:00:00Z')
    assert result['tz_disagreement'] == (len(set(zones.values())) > 1)
    assert result['calendar_timezone'] == ('UTC' if result['tz_disagreement'] else 'America/New_York')
    for client in clients.values():
        if mode == 'freebusy':
            body = client.service.freebusy.return_value.query.call_args.kwargs['body']
            start, end = body['timeMin'], body['timeMax']
        else:
            args = client.service.events.return_value.list.call_args.kwargs
            start, end = args['timeMin'], args['timeMax']
        assert instant(start) == instant('2030-04-26T14:00:00Z')
        assert instant(end) == instant('2030-04-26T16:00:00Z')
    pair = result['slots_with_end'][0]
    for key, client in clients.items():
        def encode(value):
            return instant(value).isoformat().replace('+00:00', 'Z') if booking_format == 'utc_z' else value
        booked = await tool.execute({'action': 'create_event', 'calendar_key': key, 'summary': 'Synthetic appointment',
                                     'start_datetime': encode(pair['start']), 'end_datetime': encode(pair['end'])}, CTX)
        assert booked['status'] == 'success'
        payload = client.service.events.return_value.insert.call_args.kwargs['body']
        for part in ('start', 'end'):
            assert instant(payload[part]['dateTime']) == instant(pair[part])
            assert payload[part]['timeZone'] == zones[key]
        assert instant(payload['end']['dateTime']) - instant(payload['start']['dateTime']) == timedelta(minutes=30)
        listed = await tool.execute({'action': 'list_events', 'calendar_key': key,
                                    'time_min': pair['start'], 'time_max': pair['end']}, CTX)
        event = next(e for e in listed['events'] if e['id'] == booked['event_id'])
        assert instant(event['start']) == instant(pair['start'])
        fetched = await tool.execute({'action': 'get_event', 'calendar_key': key, 'event_id': booked['event_id']}, CTX)
        assert fetched['status'] == 'success'
        assert instant(fetched['start']) == instant(pair['start'])
        assert instant(fetched['end']) == instant(pair['end'])
        deleted = await tool.execute({'action': 'delete_event', 'calendar_key': key, 'event_id': booked['event_id']}, CTX)
        assert deleted['status'] == 'success'


@pytest.mark.asyncio
@pytest.mark.parametrize(('start', 'end', 'expected_start', 'expected_end'), [
    ('2030-04-26T14:00:00', '2030-04-26T14:30:00', '2030-04-26T14:00:00-04:00', '2030-04-26T14:30:00-04:00'),
    ('2030-04-26T02:00:00Z', '2030-04-26T02:30:00Z', '2030-04-25T22:00:00-04:00', '2030-04-25T22:30:00-04:00'),
    ('2030-11-03T06:15:00Z', '2030-11-03T06:45:00Z', '2030-11-03T01:15:00-05:00', '2030-11-03T01:45:00-05:00'),
    ('2030-11-03T05:45:00Z', '2030-11-03T06:15:00Z', '2030-11-03T01:45:00-04:00', '2030-11-03T01:15:00-05:00'),
    ('2030-03-10T06:45:00Z', '2030-03-10T07:15:00Z', '2030-03-10T01:45:00-05:00', '2030-03-10T03:15:00-04:00'),
])
async def test_booking_payload_retains_dst_offsets_and_naive_compatibility(monkeypatch, start, end, expected_start, expected_end):
    tool, clients = make_tool(monkeypatch, {'A': 'America/New_York'})
    result = await tool.execute({'action': 'create_event', 'summary': 'Synthetic appointment',
                                'start_datetime': start, 'end_datetime': end}, CTX)
    assert result['status'] == 'success'
    body = clients['A'].service.events.return_value.insert.call_args.kwargs['body']
    assert body['start']['dateTime'] == expected_start
    assert body['end']['dateTime'] == expected_end


@pytest.mark.asyncio
async def test_dst_fold_duration_cannot_bypass_cap_or_reverse_instants(monkeypatch):
    tool, clients = make_tool(monkeypatch, {'A': 'America/New_York'})
    tool._get_config.return_value['max_event_duration_minutes'] = 30
    for start, end, expected in [
        ('2030-11-03T01:15:00-04:00', '2030-11-03T01:45:00-05:00', 'duration_too_long'),
        ('2030-11-03T01:15:00-05:00', '2030-11-03T01:45:00-04:00', 'invalid_duration'),
    ]:
        result = await tool.execute({'action': 'create_event', 'summary': 'Synthetic appointment',
                                    'start_datetime': start, 'end_datetime': end}, CTX)
        assert result['error_code'] == expected
    clients['A'].service.events.return_value.insert.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['get_free_slots', 'list_events', 'create_event', 'get_event', 'delete_event'])
@pytest.mark.parametrize('selected', [['A'], [], ['deleted']])
async def test_aware_inputs_never_expand_selected_calendar_scope(monkeypatch, action, selected):
    tool, clients = make_tool(monkeypatch, {'A': 'America/New_York', 'B': 'Europe/London'}, selected=selected)
    result = await tool.execute({'action': action, 'calendar_key': 'B', 'summary': 'Synthetic appointment',
                                'time_min': '2030-04-26T14:00:00Z', 'time_max': '2030-04-26T15:00:00Z',
                                'start_datetime': '2030-04-26T14:00:00Z', 'end_datetime': '2030-04-26T14:30:00Z',
                                'event_id': 'synthetic'}, CTX)
    assert result['status'] == 'error'
    for client in clients.values():
        client.service.events.assert_not_called()
        client.service.freebusy.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_datetime_retry_has_no_write_or_tracking_before_success(monkeypatch):
    tool, clients = make_tool(monkeypatch, {'A': 'America/New_York'})
    params = {'action': 'create_event', 'summary': 'Synthetic appointment',
              'start_datetime': 'invalid', 'end_datetime': '2030-04-26T14:30:00Z'}
    result = await tool.execute(params, CTX)
    assert result['status'] == 'error'
    assert not tool._last_event_per_call
    clients['A'].service.events.return_value.insert.assert_not_called()
    result = await tool.execute({**params, 'start_datetime': '2030-04-26T14:00:00Z'}, CTX)
    assert result['status'] == 'success'
    assert clients['A'].service.events.return_value.insert.call_count == 1


@pytest.mark.asyncio
async def test_failed_calendar_read_remains_fail_closed_with_aware_ranges(monkeypatch):
    tool, clients = make_tool(monkeypatch, {'A': 'America/New_York', 'B': 'Europe/London'})
    clients['B'].service.freebusy.return_value.query.return_value.execute.side_effect = GoogleCalendarApiError('Synthetic unavailable')
    result = await tool.execute({'action': 'get_free_slots', 'time_min': '2030-04-26T14:00:00Z',
                                'time_max': '2030-04-26T16:00:00Z'}, CTX)
    assert result['status'] == 'error' and 'slots' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(("start", "end"), [
    ("2030-11-03T06:00:00Z", "2030-11-03T07:00:00Z"),
    ("2030-03-10T06:30:00Z", "2030-03-10T07:30:00Z"),
])
async def test_dst_availability_roundtrip_remains_inside_requested_instants(monkeypatch, start, end):
    tool, clients = make_tool(monkeypatch, {"A": "America/New_York"})
    tool._get_config.return_value.update(working_hours_start=0, working_hours_end=24)
    offered = await tool.execute({"action": "get_free_slots", "time_min": start,
                                  "time_max": end, "duration": 30}, CTX)
    assert offered["status"] == "success" and len(offered["slots_with_end"]) == 2
    for pair in offered["slots_with_end"]:
        assert instant(start) <= instant(pair["start"]) < instant(pair["end"]) <= instant(end)
        assert instant(pair["end"]) - instant(pair["start"]) == timedelta(minutes=30)
        booked = await tool.execute({"action": "create_event", "summary": "Synthetic appointment",
                                     "start_datetime": pair["start"], "end_datetime": pair["end"]}, CTX)
        assert booked["status"] == "success"
        payload = clients["A"].service.events.return_value.insert.call_args.kwargs["body"]
        assert instant(payload["start"]["dateTime"]) == instant(pair["start"])
        assert instant(payload["end"]["dateTime"]) == instant(pair["end"])


@pytest.mark.parametrize("provider", ["openai", "openai_realtime", "deepgram", "elevenlabs", "local_llm", "google"])
def test_provider_schemas_carry_same_datetime_contract(provider):
    from src.tools.adapters.google import GoogleToolAdapter

    tool = GCalendarTool()
    if provider == "google":
        parameters = GoogleToolAdapter(SimpleNamespace(get_all=lambda: [tool])).get_tools_config()[0]["functionDeclarations"][0]["parameters"]
    else:
        declaration = getattr(tool.definition, f"to_{provider}_schema")()
        declaration = declaration.get("function", declaration)
        # ElevenLabs wraps the declaration in tool_config.
        declaration = declaration.get("tool_config", declaration)
        parameters = declaration["parameters"]
    descriptions = parameters if provider == "local_llm" else parameters["properties"]
    assert "represents an instant" in descriptions["time_min"]["description"]
    assert "naive inputs are local" in descriptions["time_max"]["description"]
    assert "including its offset" in descriptions["start_datetime"]["description"]
    assert "stripped" not in str(parameters)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["get_free_slots", "list_events"])
async def test_naive_legacy_ranges_stay_calendar_local(monkeypatch, action):
    tool, clients = make_tool(monkeypatch, {"default": "America/New_York"}, legacy=True)
    result = await tool.execute({"action": action, "time_min": "2030-04-26T09:00:00",
                                "time_max": "2030-04-26T10:00:00"}, CTX)
    assert result["status"] == "success"
    client = clients["default"]
    if action == "get_free_slots":
        args = client.service.freebusy.return_value.query.call_args.kwargs["body"]
        assert result["slots"][0] == "2030-04-26T09:00:00-04:00"
    else:
        args = client.service.events.return_value.list.call_args.kwargs
    assert args["timeMin"] == "2030-04-26T09:00:00-04:00"
    assert args["timeMax"] == "2030-04-26T10:00:00-04:00"


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['freebusy', 'title_prefix'])
@pytest.mark.parametrize('start,first', [('05:00:00', '05:00:00'), ('05:50:00', '06:00:00')])
async def test_fallback_slots_include_both_folds_in_instant_order(monkeypatch, mode, start, first):
    tool, clients = make_tool(monkeypatch, {'A': 'America/New_York'}, mode=mode)
    tool._get_config.return_value.update(working_hours_start=0, working_hours_end=24, max_slots_returned=0)
    lower, upper = f'2027-11-07T{start}Z', '2027-11-07T07:30:00Z'
    if mode == 'title_prefix':
        events = clients['A'].service.events.return_value
        events.list.return_value.execute.side_effect = lambda: {'items': [
            {'summary': 'Open hours', 'start': {'dateTime': lower}, 'end': {'dateTime': upper}}
        ]}
    offered = await tool.execute({'action': 'get_free_slots', 'time_min': lower, 'time_max': upper, 'duration': 30}, CTX)
    assert offered['status'] == 'success'
    cursor = instant(f'2027-11-07T{first}Z')
    expected = []
    while cursor + timedelta(minutes=30) <= instant(upper):
        expected.append(cursor)
        cursor += timedelta(minutes=30)
    assert [instant(slot) for slot in offered['slots']] == expected
    for pair in offered['slots_with_end']:
        assert instant(pair['end']) - instant(pair['start']) == timedelta(minutes=30)


@pytest.mark.asyncio
@pytest.mark.parametrize('aggregation', ['single', 'all', 'any'])
@pytest.mark.parametrize('busy,expected_count', [([], 1), ([('2027-11-07T05:40:00Z', '2027-11-07T06:05:00Z')], 0),
                                                 ([('2027-11-07T06:05:00Z', '2027-11-07T06:10:00Z')], 1)])
async def test_freebusy_range_with_reversed_fold_clocks_is_clipped_by_instant(monkeypatch, aggregation, busy, expected_count):
    zones = {'A': 'America/New_York'}
    if aggregation != 'single':
        zones['B'] = 'America/New_York'
    tool, clients = make_tool(monkeypatch, zones)
    tool._get_config.return_value.update(working_hours_start=0, working_hours_end=24, max_slots_returned=0)
    for key, client in clients.items():
        client.service.freebusy.return_value.query.return_value.execute.return_value = {
            'calendars': {key: {'busy': [{'start': start, 'end': end} for start, end in busy]}}
        }
    offered = await tool.execute({'action': 'get_free_slots', 'time_min': '2027-11-07T01:20:00-04:00',
                                  'time_max': '2027-11-07T01:10:00-05:00', 'duration': 30,
                                  'aggregate_mode': aggregation if aggregation != 'single' else 'all'}, CTX)
    assert offered['status'] == 'success'
    assert offered['slots_with_end'] == ([{'start': '2027-11-07T01:30:00-04:00',
                                          'end': '2027-11-07T01:00:00-05:00'}] if expected_count else [])


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [True, False])
@pytest.mark.parametrize('start,end', [
    ({'dateTime': '2030-04-26T15:00:00', 'timeZone': 'Europe/London'},
     {'dateTime': '2030-04-26T15:30:00', 'timeZone': 'Europe/London'}),
    ({'dateTime': '2030-04-26T10:00:00'}, {'dateTime': '2030-04-26T10:30:00'}),
    ({'dateTime': '2030-04-26T15:00:00', 'timeZone': 'Europe/London'},
     {'dateTime': '2030-04-26T09:30:00', 'timeZone': 'America/Chicago'}),
    ({'dateTime': '2030-04-26T16:00:00+02:00', 'timeZone': 'Pacific/Auckland'},
     {'dateTime': '2030-04-26T16:30:00+02:00', 'timeZone': 'Pacific/Auckland'}),
])
async def test_api_busy_event_uses_endpoint_zone_or_calendar_fallback(monkeypatch, legacy, start, end):
    key = 'default' if legacy else 'A'
    tool, clients = make_tool(monkeypatch, {key: 'America/New_York'}, mode='title_prefix', legacy=legacy)
    events = clients[key].service.events.return_value
    original = events.list.return_value.execute.side_effect
    events.list.return_value.execute.side_effect = lambda: {
        'items': original()['items'] + [{'summary': 'Busy synthetic appointment', 'start': start, 'end': end}]
    }
    result = await tool.execute({'action': 'get_free_slots', 'time_min': '2030-04-26T14:00:00Z',
                                'time_max': '2030-04-26T16:00:00Z', 'duration': 30}, CTX)
    assert result['status'] == 'success'
    assert [instant(slot) for slot in result['slots']] == [instant('2030-04-26T14:30:00Z'),
                                                          instant('2030-04-26T15:00:00Z'),
                                                          instant('2030-04-26T15:30:00Z')]
    assert instant('2030-04-26T14:00:00Z') not in [instant(slot) for slot in result['slots']]


@pytest.mark.asyncio
@pytest.mark.parametrize('aggregate,expected_status', [('all', 'error'), ('any', 'success')])
@pytest.mark.parametrize('bad_zone', ['Invalid/Zone', 123])
async def test_invalid_api_endpoint_zone_fails_closed_per_calendar(monkeypatch, aggregate, expected_status, bad_zone):
    tool, clients = make_tool(monkeypatch, {'A': 'America/New_York', 'B': 'America/New_York'}, mode='title_prefix')
    events = clients['B'].service.events.return_value
    original = events.list.return_value.execute.side_effect
    events.list.return_value.execute.side_effect = lambda: {'items': original()['items'] + [
        {'summary': 'Busy invalid zone', 'start': {'dateTime': '2030-04-26T10:00:00', 'timeZone': bad_zone},
         'end': {'dateTime': '2030-04-26T10:30:00', 'timeZone': bad_zone}}
    ]}
    result = await tool.execute({'action': 'get_free_slots', 'aggregate_mode': aggregate,
                                'time_min': '2030-04-26T14:00:00Z', 'time_max': '2030-04-26T16:00:00Z'}, CTX)
    assert result['status'] == expected_status
    if aggregate == 'all':
        assert not result.get('slots')
    else:
        assert result['slots'] and result['busy_blocks_found'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_start,bad_end', [
    ({}, {'dateTime': '2030-04-26T10:30:00'}),
    (None, {'dateTime': '2030-04-26T10:30:00'}),
    ({'dateTime': ''}, {'dateTime': '2030-04-26T10:30:00'}),
    ({'dateTime': 'not-a-date'}, {'dateTime': '2030-04-26T10:30:00'}),
    ({'dateTime': '2030-04-26'}, {'dateTime': '2030-04-26T10:30:00'}),
    ({'dateTime': '2030-04-26T10:00:00', 'date': '2030-04-26'}, {'dateTime': '2030-04-26T10:30:00'}),
    ({'dateTime': '2030-04-26T10:30:00'}, {'dateTime': '2030-04-26T10:00:00'}),
])
async def test_malformed_timed_api_bounds_fail_closed_and_repaired_retry_recovers(monkeypatch, bad_start, bad_end):
    tool, clients = make_tool(monkeypatch, {'A': 'America/New_York'}, mode='title_prefix')
    events = clients['A'].service.events.return_value
    original = events.list.return_value.execute.side_effect
    busy = {'summary': 'Busy malformed appointment', 'start': bad_start, 'end': bad_end}
    events.list.return_value.execute.side_effect = lambda: {'items': original()['items'] + [busy]}
    params = {'action': 'get_free_slots', 'time_min': '2030-04-26T14:00:00Z', 'time_max': '2030-04-26T16:00:00Z'}
    failed = await tool.execute(params, CTX)
    assert failed['status'] == 'error' and not failed.get('slots')
    busy.update(start={'dateTime': '2030-04-26T10:00:00'}, end={'dateTime': '2030-04-26T10:30:00'})
    repaired = await tool.execute(params, CTX)
    assert repaired['status'] == 'success'
    assert instant(repaired['slots'][0]) == instant('2030-04-26T14:30:00Z')
    clients['A'].service.events.return_value.insert.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('zone', ['Europe/London', None])
async def test_offsetless_open_event_uses_same_api_datetime_contract(monkeypatch, zone):
    tool, clients = make_tool(monkeypatch, {'A': 'America/New_York'}, mode='title_prefix')
    start_clock, end_clock = ('15:00:00', '16:00:00') if zone else ('10:00:00', '11:00:00')
    start, end = {'dateTime': f'2030-04-26T{start_clock}'}, {'dateTime': f'2030-04-26T{end_clock}'}
    if zone:
        start['timeZone'] = end['timeZone'] = zone
    clients['A'].service.events.return_value.list.return_value.execute.side_effect = lambda: {'items': [
        {'summary': 'Open offsetless window', 'start': start, 'end': end},
        {'summary': 'Busy first half', 'start': {'dateTime': '2030-04-26T14:00:00Z'},
         'end': {'dateTime': '2030-04-26T14:30:00Z'}}
    ]}
    result = await tool.execute({'action': 'get_free_slots', 'time_min': '2030-04-26T14:00:00Z',
                                'time_max': '2030-04-26T15:00:00Z'}, CTX)
    assert result['status'] == 'success'
    assert [instant(slot) for slot in result['slots']] == [instant('2030-04-26T14:30:00Z')]


@pytest.mark.asyncio
async def test_naive_api_event_without_usable_event_or_calendar_zone_is_not_utc(monkeypatch):
    tool, clients = make_tool(monkeypatch, {'A': 'Invalid/Calendar'}, mode='title_prefix')
    events = clients['A'].service.events.return_value
    original = events.list.return_value.execute.side_effect
    events.list.return_value.execute.side_effect = lambda: {'items': original()['items'] + [
        {'summary': 'Busy missing usable zone', 'start': {'dateTime': '2030-04-26T10:00:00'},
         'end': {'dateTime': '2030-04-26T10:30:00'}}
    ]}
    result = await tool.execute({'action': 'get_free_slots', 'time_min': '2030-04-26T14:00:00Z',
                                'time_max': '2030-04-26T16:00:00Z'}, CTX)
    assert result['status'] == 'error' and not result.get('slots')
