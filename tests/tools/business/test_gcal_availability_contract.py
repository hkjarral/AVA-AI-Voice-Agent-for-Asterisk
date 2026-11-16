"""Offline Google events API contract tests for title-prefix booking availability."""

from copy import deepcopy
import threading
from unittest.mock import Mock

import pytest

from src.tools.business.gcal_tool import GCalendarTool
from src.tools.business.gcalendar import GCalendar


def timed(summary, start='2027-04-26T10:00:00Z', end='2027-04-26T10:30:00Z', **fields):
    return dict(summary=summary, start={'dateTime': start}, end={'dateTime': end}, **fields)


def all_day(summary, start='2027-04-26', end='2027-04-27', **fields):
    return dict(summary=summary, start={'date': start}, end={'date': end}, **fields)


class MemoryEvents:
    """Fake only the HTTP boundary; execute real GCalendar create/list/delete."""

    def __init__(self, events):
        self.items = deepcopy(events)
        self.insert = Mock(side_effect=self._insert)
        self.list = Mock(side_effect=lambda **kwargs: Mock(execute=lambda: {'items': deepcopy(self.items)}))
        self.delete = Mock(side_effect=self._delete)

    def _insert(self, *, calendarId, body):
        def execute():
            event = dict(deepcopy(body), id=f'booking-{len(self.items)}')
            self.items.append(event)
            return deepcopy(event)
        return Mock(execute=execute)

    def _delete(self, *, calendarId, eventId):
        def execute():
            self.items = [event for event in self.items if event.get('id') != eventId]
        return Mock(execute=execute)


@pytest.fixture
def calendar_contract(tool_context, monkeypatch):
    config = {
        'enabled': True, 'credentials_path': '/unused', 'calendar_id': 'primary',
        'timezone': 'UTC', 'free_prefix': 'Open', 'busy_prefix': 'Busy', 'max_slots_returned': 0,
    }
    tool_context.get_config_value = Mock(side_effect=lambda *args, **kwargs: config)
    events = MemoryEvents([timed('Open hours', '2027-04-26T09:00:00Z', '2027-04-26T12:00:00Z')])
    client = GCalendar.__new__(GCalendar)
    client.timezone = 'UTC'
    client.calendar_id = 'primary'
    client._lock = threading.Lock()
    client.service = Mock()
    client.service.events.return_value = events
    client.service.freebusy.return_value.query.return_value.execute.return_value = {
        'calendars': {'primary': {'busy': [{'start': '2027-04-26T10:00:00Z', 'end': '2027-04-26T10:30:00Z'}]}}
    }
    monkeypatch.setattr(GCalendarTool, '_get_cal', lambda *args: client)
    monkeypatch.setattr(GCalendarTool, '_get_or_create_cal', lambda *args: client)
    return GCalendarTool(), tool_context, config, events, client


async def search(tool, context, **params):
    return await tool.execute(dict(action='get_free_slots', time_min='2027-04-26T09:00:00',
                                   time_max='2027-04-26T12:00:00', duration=30, **params), context)


@pytest.mark.asyncio
@pytest.mark.parametrize('prefixes', [('Open', 'Busy'), ('Available', 'Reserved'), ('Open', '')])
@pytest.mark.parametrize('summary', ['Consultation', 'Open appointment'])
async def test_book_search_restart_delete_contract(calendar_contract, prefixes, summary):
    tool, context, config, events, client = calendar_contract
    config['free_prefix'], config['busy_prefix'] = prefixes
    events.items[0]['summary'] = prefixes[0] + ' hours'
    before = await search(tool, context)
    assert '2027-04-26T10:00:00+00:00' in before['slots']
    booked = await tool.execute(dict(action='create_event', summary=summary,
        description='Synthetic appointment', start_datetime='2027-04-26T10:00:00',
        end_datetime='2027-04-26T10:30:00'), context)
    assert booked['status'] == 'success'
    body = events.insert.call_args.kwargs['body']
    assert body['summary'] == summary  # Preserve operator/caller titles.
    assert body['description'] == 'Synthetic appointment'
    assert body['transparency'] == 'opaque'
    # Repeated searches and a new tool instance must see persisted busy state.
    for reader in (tool, tool, GCalendarTool()):
        result = await search(reader, context, busy_prefix='Different')
        assert result['status'] == 'success'
        assert '2027-04-26T10:00:00+00:00' not in result['slots']
        assert len(result['slots']) == 5
    deleted = await tool.execute(dict(action='delete_event', event_id=booked['event_id']), context)
    assert deleted['status'] == 'success'
    assert (await search(GCalendarTool(), context))['slots'] == before['slots']
    assert events.list.call_args.kwargs['calendarId'] == 'primary'
    client.service.freebusy.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('event,blocked', [
    (timed('Busy appointment'), True),
    (timed('Busy appointment', transparency='opaque'), True),
    (all_day('Busy away'), True),
    (all_day('Busy tentative', status='tentative'), True),
    (timed('Busy available', transparency='transparent'), False),
    (all_day('Busy available', transparency='transparent'), False),
    (timed('Busy deleted', status='cancelled'), False),
    ({'summary': 'Busy deleted', 'status': 'cancelled'}, False),
    (timed('Unrelated appointment'), False),
    (all_day('Unrelated holiday'), False),
    ({'summary': 'Unrelated malformed'}, False),
])
async def test_explicit_prefix_transparency_cancelled_and_unrelated_contract(calendar_contract, event, blocked):
    tool, context, _, events, _ = calendar_contract
    events.items.append(event)
    result = await search(tool, context)
    assert result['status'] == 'success'
    assert ('2027-04-26T10:00:00+00:00' not in result['slots']) == blocked


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['transparent', 'cancelled'])
async def test_operator_can_release_marked_booking(calendar_contract, change):
    tool, context, _, events, _ = calendar_contract
    await tool.execute(dict(action='create_event', summary='Appointment',
        start_datetime='2027-04-26T10:00:00', end_datetime='2027-04-26T10:30:00'), context)
    if change == 'transparent':
        events.items[-1]['transparency'] = 'transparent'
    else:
        events.items[-1]['status'] = 'cancelled'
    assert '2027-04-26T10:00:00+00:00' in (await search(GCalendarTool(), context))['slots']


@pytest.mark.asyncio
@pytest.mark.parametrize('zone,day,end,expected', [
    ('America/New_York', '2027-03-13', '2027-03-16', ['2027-03-16T00:00:00-04:00', '2027-03-16T00:30:00-04:00']),
    ('America/New_York', '2027-11-06', '2027-11-09', ['2027-11-09T00:00:00-05:00', '2027-11-09T00:30:00-05:00']),
    ('Asia/Kathmandu', '2027-04-26', '2027-04-28', ['2027-04-28T00:00:00+05:45', '2027-04-28T00:30:00+05:45']),
])
async def test_multiday_all_day_busy_has_calendar_midnight_exclusive_end(calendar_contract, zone, day, end, expected):
    tool, context, config, events, client = calendar_contract
    config['timezone'] = client.timezone = zone
    # Include an all-day Open window; Google returns overlapping whole events,
    # so availability must also be clipped to the requested range.
    events.items = [all_day('Open hours', day, end[:8] + f'{int(end[-2:]) + 1:02d}'),
                    all_day('Busy vacation', day, end)]
    result = await tool.execute(dict(action='get_free_slots', time_min=day + 'T00:00:00',
                                    time_max=end + 'T01:00:00', duration=30), context)
    assert result['status'] == 'success'
    assert result['slots'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('transparent', [False, True])
async def test_all_day_open_is_clipped_to_search_and_allows_transparency(calendar_contract, transparent):
    tool, context, _, events, _ = calendar_contract
    events.items = [all_day('Open hours', transparency='transparent' if transparent else 'opaque')]
    result = await search(tool, context)
    assert result['status'] == 'success'
    assert len(result['slots']) == 6
    assert result['slots'][0] == '2027-04-26T09:00:00+00:00'
    assert result['slots_with_end'][-1]['end'] == '2027-04-26T12:00:00+00:00'


@pytest.mark.asyncio
@pytest.mark.parametrize('event', [
    {'summary': 'Busy missing'},
    all_day('Busy missing end', end=''),
    all_day('Busy invalid date', start='2027-02-30'),
    all_day('Busy invalid format', start='20270426'),
    all_day('Busy zero', end='2027-04-26'),
    all_day('Busy reversed', end='2027-04-25'),
    dict(summary='Busy mixed', start={'date': '2027-04-26'}, end={'dateTime': '2027-04-27T00:00:00Z'}),
    dict(summary='Busy ambiguous', start={'date': '2027-04-26', 'dateTime': '2027-04-26T10:00:00Z'}, end={'date': '2027-04-27'}),
    timed('Busy invalid', start='invalid'),
    timed('Busy missing timezone', start='2027-04-26T10:00:00'),
    timed('Busy reversed', end='2027-04-26T09:00:00Z'),
    {'summary': 'Open missing'},
])
async def test_relevant_malformed_events_fail_closed_and_recover_after_repair(calendar_contract, event):
    tool, context, _, events, _ = calendar_contract
    events.items.append(event)
    result = await search(tool, context)
    assert result['status'] == 'error'
    assert not result.get('slots')
    events.items.pop()
    assert len((await search(tool, context))['slots']) == 6


@pytest.mark.asyncio
async def test_timed_api_offsets_and_explicit_timezones_are_respected(calendar_contract):
    tool, context, _, events, _ = calendar_contract
    events.items.append(dict(summary='Busy foreign timezone',
        start={'dateTime': '2027-04-26T06:00:00', 'timeZone': 'America/New_York'},
        end={'dateTime': '2027-04-26T12:30:00+02:00'}))
    result = await search(tool, context)
    assert result['status'] == 'success'
    assert '2027-04-26T10:00:00+00:00' not in result['slots']
    assert len(result['slots']) == 5


@pytest.mark.asyncio
async def test_native_freebusy_keeps_working_hours_and_ignores_prefix_arguments(calendar_contract):
    tool, context, config, events, client = calendar_contract
    config['free_prefix'] = ''
    events.items = []
    result = await search(tool, context, free_prefix='Open', busy_prefix='Busy')
    assert result['status'] == 'success'
    assert result['availability_mode'] == 'freebusy'
    assert '2027-04-26T10:00:00+00:00' not in result['slots']
    assert len(result['slots']) == 5
    events.list.assert_not_called()
    client.service.freebusy.assert_called_once()


@pytest.mark.asyncio
async def test_legacy_unmarked_booking_requires_explicit_operator_title_repair(calendar_contract):
    tool, context, config, events, _ = calendar_contract
    config['busy_prefix'] = 'Reserved'
    legacy = timed('Consultation')
    events.items.append(legacy)
    # Pre-marker bookings intentionally remain indistinguishable from unrelated
    # ignored events. Repair only after an operator reviews the appointment.
    assert '2027-04-26T10:00:00+00:00' in (await search(tool, context))['slots']
    legacy['summary'] = 'Reserved Consultation'
    assert '2027-04-26T10:00:00+00:00' not in (await search(GCalendarTool(), context))['slots']
    assert legacy.get('extendedProperties') is None
    events.insert.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_create_that_completes_remotely_still_blocks_fresh_search(calendar_contract):
    import asyncio

    tool, context, _, events, _ = calendar_contract
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    normal_insert = events._insert

    def delayed_insert(**kwargs):
        request = normal_insert(**kwargs)
        def execute():
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError('fixture write was not released')
            try:
                return request.execute()
            finally:
                finished.set()
        return Mock(execute=execute)

    events.insert.side_effect = delayed_insert
    task = asyncio.create_task(tool.execute(dict(action='create_event', summary='Appointment',
        start_datetime='2027-04-26T10:00:00', end_datetime='2027-04-26T10:30:00'), context))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 2)
    assert '2027-04-26T10:00:00+00:00' not in (await search(GCalendarTool(), context))['slots']
    events.insert.assert_called_once()


@pytest.mark.asyncio
async def test_agent_calendar_selection_is_enforced_before_api_access(calendar_contract):
    tool, context, config, events, _ = calendar_contract
    config['calendars'] = {'allowed': {'calendar_id': 'primary', 'timezone': 'UTC'},
                           'private': {'calendar_id': 'private', 'timezone': 'UTC'}}
    config['selected_calendars'] = ['allowed']
    config['_agent_scope_resolved'] = True
    result = await search(tool, context, calendar_key='private')
    assert result['status'] == 'error'
    result = await tool.execute(dict(action='create_event', calendar_key='private', summary='Appointment',
        start_datetime='2027-04-26T10:00:00', end_datetime='2027-04-26T10:30:00'), context)
    assert result['status'] == 'error'
    events.list.assert_not_called()
    events.insert.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('day,next_day,count', [
    ('2027-03-14', '2027-03-15', 46),
    ('2027-11-07', '2027-11-08', 50),
])
async def test_all_day_open_during_dst_change_only_returns_real_complete_slots(calendar_contract, day, next_day, count):
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    tool, context, config, events, client = calendar_contract
    config['timezone'] = client.timezone = 'America/New_York'
    events.items = [all_day('Open hours', day, next_day)]
    result = await tool.execute(dict(action='get_free_slots', time_min=day + 'T00:00:00',
                                    time_max=next_day + 'T00:00:00', duration=30), context)
    assert result['status'] == 'success'
    assert len(result['slots']) == count
    instants = []
    for slot in result['slots_with_end']:
        start, end = map(datetime.fromisoformat, (slot['start'], slot['end']))
        assert start.astimezone(ZoneInfo('America/New_York')).isoformat() == slot['start']
        assert end.astimezone(timezone.utc) - start.astimezone(timezone.utc) == timedelta(minutes=30)
        instants.append(start.astimezone(timezone.utc))
    assert len(set(instants)) == count
    assert instants == sorted(instants)
    assert result['slots_with_end'][-1]['end'] == next_day + 'T00:00:00' + ('-04:00' if count == 46 else '-05:00')


@pytest.mark.asyncio
async def test_repeated_hour_busy_event_blocks_only_its_actual_instant(calendar_contract):
    tool, context, config, events, client = calendar_contract
    config['timezone'] = client.timezone = 'America/New_York'
    events.items = [all_day('Open hours', '2027-11-07', '2027-11-08'),
                    timed('Busy repeated hour', '2027-11-07T01:00:00-05:00', '2027-11-07T01:30:00-05:00')]
    result = await tool.execute(dict(action='get_free_slots', time_min='2027-11-07T00:00:00',
                                    time_max='2027-11-07T03:00:00', duration=30), context)
    assert result['status'] == 'success'
    assert len(result['slots']) == 7
    assert '2027-11-07T01:00:00-04:00' in result['slots']
    assert '2027-11-07T01:00:00-05:00' not in result['slots']


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,status', [('all', 'error'), ('any', 'success')])
async def test_malformed_calendar_respects_shared_vs_any_aggregation(calendar_contract, monkeypatch, mode, status):
    from copy import copy

    tool, context, config, events, client = calendar_contract
    config['calendars'] = {
        'valid': {'calendar_id': 'valid', 'timezone': 'UTC'},
        'broken': {'calendar_id': 'broken', 'timezone': 'UTC'},
    }
    broken = copy(client)
    broken.calendar_id = 'broken'
    broken.service = Mock()
    broken.service.events.return_value = MemoryEvents(events.items + [{'summary': 'Busy invalid'}])
    monkeypatch.setattr(GCalendarTool, '_get_or_create_cal', lambda self, creds, key, *args:
                        broken if key == 'broken' else client)
    result = await search(tool, context, aggregate_mode=mode)
    assert result['status'] == status
    if mode == 'all':
        assert not result.get('slots')
    else:
        assert len(result['slots']) == 6
    events.insert.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_all_day_open_does_not_create_availability(calendar_contract):
    tool, context, _, events, _ = calendar_contract
    events.items = [all_day('Open cancelled', status='cancelled')]
    result = await search(tool, context)
    assert result['status'] == 'success'
    assert result['slots'] == []
    assert result['reason'] == 'no_open_windows'
