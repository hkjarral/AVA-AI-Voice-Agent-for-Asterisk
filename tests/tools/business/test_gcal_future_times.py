"""Offline future-time acceptance for Google availability and booking.

Each test supplies its own clock; no Google credentials or network are used.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from zoneinfo import ZoneInfo

import pytest

from src.tools.business.gcal_tool import GCalendarTool
from src.tools.business.gcalendar import GoogleCalendarApiError


UTC = timezone.utc
CONTEXT = SimpleNamespace(call_id="synthetic-future-time")


@pytest.fixture
def calendar_factory(monkeypatch):
    def make(mode="freebusy", topology="legacy", zone="UTC", now="2027-04-26T14:00:00+00:00", **overrides):
        config = {
            "enabled": True, "credentials_path": "/unused", "calendar_id": "synthetic",
            "timezone": zone, "working_days": list(range(7)),
            "free_prefix": "Open" if mode == "title_prefix" else "",
            **overrides,
        }
        if topology != "legacy":
            config["calendars"] = {
                key: {"credentials_path": "/unused", "calendar_id": key, "timezone": zone}
                for key in ("A", "B")
            }
        tool = GCalendarTool()
        apis = {key: MagicMock() for key in ("default", "A", "B")}
        for api in apis.values():
            api.freebusy_query.return_value = []
            api.list_events.return_value = []
            api.create_event.return_value = {"id": "synthetic-created"}
        tool._get_config = Mock(return_value=config)
        tool._get_cal = Mock(return_value=apis["default"])
        tool._get_or_create_cal = Mock(side_effect=lambda creds, key, *args: apis.get(key, apis["default"]))
        clock = Mock(return_value=datetime.fromisoformat(now))
        monkeypatch.setattr("src.tools.business.gcal_tool.utc_now", clock)
        return tool, apis, clock, config
    return make


async def availability(tool, apis, start, end, mode="freebusy", topology="legacy", duration=30):
    # Events mimic Google's response, including timezone offsets. Title-prefix
    # overlap behavior is separately covered by #867, not reimplemented here.
    zone = ZoneInfo(tool._get_config(CONTEXT)["timezone"])
    def aware(value):
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
    for api in apis.values():
        api.list_events.return_value = [{
            "summary": "Open hours",
            "start": {"dateTime": aware(start).isoformat()},
            "end": {"dateTime": aware(end).isoformat()},
        }]
    params = {"action": "get_free_slots", "time_min": start, "time_max": end, "duration": duration}
    if topology == "targeted":
        params["calendar_key"] = "B"
    elif topology in ("all", "any"):
        params["aggregate_mode"] = topology
    return await tool.execute(params, CONTEXT)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["freebusy", "title_prefix"])
@pytest.mark.parametrize("topology", ["legacy", "named", "targeted", "all", "any"])
@pytest.mark.parametrize("now", ["14:00:00", "14:00:00.000001", "14:29:59"])
async def test_today_filters_before_default_cap_and_counts_only_future(calendar_factory, mode, topology, now):
    tool, apis, clock, _ = calendar_factory(mode, topology, now=f"2027-04-26T{now}+00:00")
    result = await availability(tool, apis, "2027-04-26T09:00:00", "2027-04-26T17:00:00", mode, topology)
    assert result["status"] == "success"
    assert result["availability_mode"] == mode
    assert result["reason"] == "available"
    assert result["slots"] == [f"2027-04-26T{hour}+00:00" for hour in ("14:30:00", "15:00:00", "15:30:00")]
    assert result["total_slots_available"] == 5
    assert result["slots_truncated"] is True
    assert "showing 3 of 5" in result["message"]
    assert clock.call_count == 1
    for pair in result["slots_with_end"]:
        assert datetime.fromisoformat(pair["end"]) - datetime.fromisoformat(pair["start"]) == timedelta(minutes=30)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["freebusy", "title_prefix"])
@pytest.mark.parametrize("topology", ["legacy", "all", "any"])
@pytest.mark.parametrize("end", ["13:00:00", "14:00:00", "14:30:00"])
async def test_elapsed_ranges_return_empty_with_recovery(calendar_factory, mode, topology, end):
    tool, apis, _, _ = calendar_factory(mode, topology)
    result = await availability(tool, apis, "2027-04-26T09:00:00", f"2027-04-26T{end}", mode, topology)
    assert result["status"] == "success"
    assert result["slots"] == result["slots_with_end"] == []
    assert result["total_slots_available"] == 0
    assert result["slots_truncated"] is False
    assert result["reason"] == "no_future_slots"
    assert "later time or date" in result["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["freebusy", "title_prefix"])
@pytest.mark.parametrize("cap,expected_count", [(0, 5), (-1, 5), (1, 1), (None, 3), ("invalid", 3)])
async def test_existing_cap_defaults_and_disable_semantics(calendar_factory, mode, cap, expected_count):
    tool, apis, _, _ = calendar_factory(mode, max_slots_returned=cap)
    result = await availability(tool, apis, "2027-04-26T09:00:00", "2027-04-26T17:00:00", mode)
    assert len(result["slots"]) == expected_count
    assert result["total_slots_available"] == 5
    assert result["slots_truncated"] is (expected_count < 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["freebusy", "title_prefix"])
@pytest.mark.parametrize("zone,now,day,first", [
    ("America/Los_Angeles", "2027-04-27T00:00:00+00:00", "2027-04-26", None),
    ("Asia/Kolkata", "2027-04-26T08:30:00+00:00", "2027-04-26", "14:30:00"),
    ("Pacific/Auckland", "2027-04-26T21:00:00+00:00", "2027-04-27", "09:30:00"),
    ("America/New_York", "2027-03-14T13:00:00+00:00", "2027-03-14", "09:30:00"),
    ("America/New_York", "2027-11-07T14:00:00+00:00", "2027-11-07", "09:30:00"),
])
async def test_future_filter_uses_calendar_instant_across_dates_and_dst(calendar_factory, mode, zone, now, day, first):
    tool, apis, clock, _ = calendar_factory(mode, zone=zone, now=now)
    result = await availability(tool, apis, f"{day}T09:00:00", f"{day}T17:00:00", mode)
    assert result["status"] == "success"
    if first is None:
        assert result["slots"] == []
    else:
        assert datetime.fromisoformat(result["slots"][0]).strftime("%H:%M:%S") == first
    assert all(datetime.fromisoformat(s).astimezone(UTC) > clock.return_value for s in result["slots"])


def booking(start, end, **params):
    return {"action": "create_event", "summary": "Synthetic appointment",
            "start_datetime": start, "end_datetime": end, **params}


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", ["legacy", "targeted"])
@pytest.mark.parametrize("max_duration", [0, 240])
@pytest.mark.parametrize("start,end", [
    ("2000-01-03T09:00:00", "2000-01-03T09:30:00"),
    ("2027-04-26T13:59:59", "2027-04-26T14:29:59"),
    ("2027-04-26T14:00:00", "2027-04-26T14:30:00"),
    ("2027-04-26T13:59:59.999999", "2027-04-26T14:29:59.999999"),
])
async def test_past_and_exact_now_booking_never_writes_or_changes_tracking(calendar_factory, topology, max_duration, start, end):
    tool, apis, clock, _ = calendar_factory(topology=topology, max_event_duration_minutes=max_duration)
    previous = {"event_id": "prior-owned-event", "calendar_key": "default"}
    tool._last_event_per_call[CONTEXT.call_id] = previous.copy()
    result = await tool.execute(booking(start, end, **({"calendar_key": "B"} if topology == "targeted" else {})), CONTEXT)
    assert result["error_code"] == "past_booking"
    assert result["status"] == "error"
    assert "get_free_slots" in result["message"] and "confirm" in result["message"]
    assert "No event was created" in result["message"]
    for api in apis.values():
        api.create_event.assert_not_called()
    assert tool._last_event_per_call[CONTEXT.call_id] == previous
    clock.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("zone,now,start", [
    ("Asia/Kolkata", "2027-04-26T08:30:00+00:00", "2027-04-26T14:00:00"),
    ("America/Los_Angeles", "2027-04-27T00:00:00+00:00", "2027-04-26T16:30:00"),
    # First 01:30 in New York has elapsed during the second 01:00 hour.
    ("America/New_York", "2027-11-07T06:00:00+00:00", "2027-11-07T01:30:00"),
])
async def test_booking_guard_compares_utc_instants(calendar_factory, zone, now, start):
    tool, apis, _, _ = calendar_factory(zone=zone, now=now)
    end = (datetime.fromisoformat(start) + timedelta(minutes=30)).isoformat()
    result = await tool.execute(booking(start, end), CONTEXT)
    assert result["error_code"] == "past_booking"
    apis["default"].create_event.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["freebusy", "title_prefix"])
@pytest.mark.parametrize("now,start,end,expected", [
    ("2027-04-26T23:45:00+00:00", "2027-04-26T23:00:00", "2027-04-27T02:00:00", "2027-04-27T00:00:00+00:00"),
    ("2027-03-14T08:00:00+00:00", "2027-03-13T23:00:00", "2027-03-14T06:00:00", "2027-03-14T04:30:00-04:00"),
    ("2027-11-07T06:45:00+00:00", "2027-11-06T23:00:00", "2027-11-07T04:00:00", "2027-11-07T02:00:00-05:00"),
])
async def test_range_crossing_midnight_and_dst_excludes_elapsed_instants(calendar_factory, mode, now, start, end, expected):
    zone = "UTC" if now.startswith("2027-04") else "America/New_York"
    tool, apis, clock, _ = calendar_factory(mode, zone=zone, now=now, working_hours_start=0, working_hours_end=24)
    result = await availability(tool, apis, start, end, mode)
    assert result["status"] == "success"
    assert result["slots"][0] == expected
    assert all(datetime.fromisoformat(s).astimezone(UTC) > clock.return_value for s in result["slots"])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["freebusy", "title_prefix"])
async def test_future_filter_preserves_busy_blocks_and_nondefault_duration(calendar_factory, mode):
    tool, apis, _, _ = calendar_factory(mode, now="2027-04-26T14:00:00+00:00")
    api = apis["default"]
    busy = ("2027-04-26T14:15:00+00:00", "2027-04-26T15:00:00+00:00")
    api.freebusy_query.return_value = [busy]
    # Use side_effect so the Open-window helper cannot replace this busy block.
    api.list_events.side_effect = lambda *args: [
        {"summary": "Open hours", "start": {"dateTime": "2027-04-26T09:00:00+00:00"},
         "end": {"dateTime": "2027-04-26T17:00:00+00:00"}},
        {"summary": "Busy meeting", "start": {"dateTime": busy[0]}, "end": {"dateTime": busy[1]}},
    ]
    result = await availability(tool, apis, "2027-04-26T09:00:00", "2027-04-26T17:00:00", mode, duration=45)
    assert result["slots"] == ["2027-04-26T15:00:00+00:00", "2027-04-26T15:45:00+00:00"]
    assert result["slot_duration_minutes"] == 45
    assert result["slots_with_end"][-1]["end"] == "2027-04-26T16:30:00+00:00"


@pytest.mark.asyncio
async def test_expired_offer_recovers_by_querying_and_confirming_new_time(calendar_factory):
    tool, apis, clock, _ = calendar_factory()
    offer = await availability(tool, apis, "2027-04-26T09:00:00", "2027-04-26T17:00:00")
    first = offer["slots_with_end"][0]
    clock.return_value = datetime.fromisoformat(first["start"])
    expired = await tool.execute(booking(first["start"], first["end"]), CONTEXT)
    assert expired["error_code"] == "past_booking"
    apis["default"].create_event.assert_not_called()
    refreshed = await availability(tool, apis, "2027-04-26T09:00:00", "2027-04-26T17:00:00")
    next_slot = refreshed["slots_with_end"][0]
    booked = await tool.execute(booking(next_slot["start"], next_slot["end"]), CONTEXT)
    assert booked["status"] == "success"
    apis["default"].create_event.assert_called_once()
    assert tool._last_event_per_call[CONTEXT.call_id]["event_id"] == "synthetic-created"
    assert clock.call_count == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["freebusy", "title_prefix"])
@pytest.mark.parametrize("topology", ["legacy", "targeted", "all", "any"])
async def test_slow_calendar_reads_use_one_final_cutoff_before_cap(calendar_factory, mode, topology):
    tool, apis, clock, _ = calendar_factory(mode, topology, now="2027-04-26T13:59:59+00:00")
    queried = []

    def delayed_read(key, api):
        method = api.freebusy_query if mode == "freebusy" else api.list_events

        def read(*args):
            # Model blocking client/API work without sleeping or network. A
            # second calendar crosses another boundary; all results must use
            # the later cutoff, not a per-calendar or execute-entry snapshot.
            queried.append(key)
            hour = "14:00:01" if len(queried) == 1 else "14:30:01"
            clock.return_value = datetime.fromisoformat(f"2027-04-26T{hour}+00:00")
            return method.return_value

        return read

    for key, api in apis.items():
        method = api.freebusy_query if mode == "freebusy" else api.list_events
        method.side_effect = delayed_read(key, api)

    result = await availability(tool, apis, "2027-04-26T09:00:00", "2027-04-26T17:00:00", mode, topology)
    expected_first = "15:00:00" if topology in ("all", "any") else "14:30:00"
    expected_count = 4 if topology in ("all", "any") else 5
    assert result["slots"][0] == f"2027-04-26T{expected_first}+00:00"
    assert result["total_slots_available"] == expected_count
    assert len(result["slots"]) == 3
    assert all(datetime.fromisoformat(s) > clock.return_value for s in result["slots"])
    clock.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["freebusy", "title_prefix"])
async def test_final_availability_cutoff_is_after_slot_generation(calendar_factory, monkeypatch, mode):
    from src.tools.business import gcal_tool as module

    tool, apis, clock, _ = calendar_factory(mode, now="2027-04-26T13:59:59+00:00")
    generate = module.build_slot_starts

    def delayed_generation(*args):
        result = generate(*args)
        clock.return_value = datetime(2027, 4, 26, 14, 0, 1, tzinfo=UTC)
        return result

    monkeypatch.setattr(module, "build_slot_starts", delayed_generation)
    result = await availability(tool, apis, "2027-04-26T09:00:00", "2027-04-26T17:00:00", mode)
    assert result["slots"][0] == "2027-04-26T14:30:00+00:00"
    assert result["total_slots_available"] == 5
    clock.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("topology", ["legacy", "targeted"])
async def test_slow_client_resolution_rejects_expired_booking_then_fresh_retry_succeeds(calendar_factory, topology):
    tool, apis, clock, _ = calendar_factory(topology=topology)
    lookup = tool._get_cal if topology == "legacy" else tool._get_or_create_cal
    original = lookup.side_effect
    delayed = True

    def resolve(*args):
        if delayed:
            clock.return_value = datetime(2027, 4, 26, 14, 0, 2, tzinfo=UTC)
        return original(*args) if original else lookup.return_value

    lookup.side_effect = resolve
    parameters = booking("2027-04-26T14:00:01", "2027-04-26T14:30:01",
                         **({"calendar_key": "B"} if topology == "targeted" else {}))
    rejected = await tool.execute(parameters, CONTEXT)
    assert rejected["status"] == "error"
    assert rejected["error_code"] == "past_booking"
    assert not tool._last_event_per_call
    for api in apis.values():
        api.create_event.assert_not_called()
    clock.assert_called_once()

    delayed = False
    clock.return_value = datetime(2027, 4, 26, 14, 30, tzinfo=UTC)
    parameters.update(start_datetime="2027-04-26T14:30:01", end_datetime="2027-04-26T15:00:01")
    retried = await tool.execute(parameters, CONTEXT)
    assert retried["status"] == "success"
    apis["B" if topology == "targeted" else "default"].create_event.assert_called_once()
    assert clock.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("aggregate,expected", [("all", "error"), ("any", "success")])
async def test_calendar_failure_still_fails_closed_for_all(calendar_factory, aggregate, expected):
    tool, apis, _, _ = calendar_factory(topology=aggregate)
    apis["B"].freebusy_query.side_effect = GoogleCalendarApiError("Synthetic unavailable")
    result = await availability(tool, apis, "2027-04-26T09:00:00", "2027-04-26T17:00:00", topology=aggregate)
    assert result["status"] == expected
    if expected == "success":
        assert result["slots"][0] == "2027-04-26T14:30:00+00:00"
    else:
        assert "unavailable" in result["message"]


@pytest.mark.asyncio
async def test_future_booking_keeps_api_error_and_no_tracking(calendar_factory):
    tool, apis, _, _ = calendar_factory()
    apis["default"].create_event.side_effect = GoogleCalendarApiError("Synthetic forbidden")
    result = await tool.execute(booking("2027-04-26T14:00:01", "2027-04-26T14:30:01"), CONTEXT)
    assert result["error_code"] == "forbidden_calendar"
    assert not tool._last_event_per_call
    apis["default"].create_event.assert_called_once()


@pytest.mark.asyncio
async def test_cancellation_of_availability_propagates_without_booking(calendar_factory):
    tool, apis, _, _ = calendar_factory()
    apis["default"].freebusy_query.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await availability(tool, apis, "2027-04-26T09:00:00", "2027-04-26T17:00:00")
    apis["default"].create_event.assert_not_called()


@pytest.mark.asyncio
async def test_historical_listing_is_not_filtered(calendar_factory):
    tool, apis, clock, _ = calendar_factory()
    apis["default"].list_events.return_value = [{"id": "historical-event"}]
    result = await tool.execute({"action": "list_events", "time_min": "2000-01-03T09:00:00",
                                 "time_max": "2000-01-03T17:00:00"}, CONTEXT)
    assert result["status"] == "success"
    apis["default"].list_events.assert_called_once()
    clock.assert_not_called()
