"""Regression coverage for calendar slot alignment and interval containment."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.tools.business._calendar_utils import build_slot_starts, round_up_to_next_slot


@pytest.mark.parametrize(
    ("start", "step", "expected"),
    [
        ("10:00:00", 30, "2026-04-29T10:00:00"),
        ("10:00:30", 30, "2026-04-29T10:30:00"),
        ("10:01:00", 30, "2026-04-29T10:30:00"),
        ("10:01:30", 30, "2026-04-29T10:30:00"),
        ("10:30:00.000005", 30, "2026-04-29T11:00:00"),
        ("10:00:00.000001", 1, "2026-04-29T10:01:00"),
        ("10:15:30", 15, "2026-04-29T10:30:00"),
        ("10:30:30", 45, "2026-04-29T11:15:00"),
        ("00:00:00.000001", 30, "2026-04-29T00:30:00"),
        ("23:30:00.000001", 30, "2026-04-30T00:00:00"),
        ("23:59:59.999999", 30, "2026-04-30T00:00:00"),
    ],
)
@pytest.mark.parametrize("tz", [None, ZoneInfo("America/Los_Angeles")])
def test_round_up_to_next_slot_never_rounds_down(start, step, expected, tz):
    original = datetime.fromisoformat(f"2026-04-29T{start}").replace(tzinfo=tz)
    expected_dt = datetime.fromisoformat(expected).replace(tzinfo=tz)

    rounded = round_up_to_next_slot(original, step)

    assert rounded == expected_dt
    assert rounded >= original
    assert rounded.tzinfo is original.tzinfo
    assert round_up_to_next_slot(rounded, step) == rounded


@pytest.mark.parametrize("fraction", ["30", "00.000005"])
@pytest.mark.parametrize(
    ("end", "expected"),
    [
        ("10:30:00", []),
        ("10:59:59.999999", []),
        ("11:00:00", ["10:30:00"]),
        ("11:30:00", ["10:30:00", "11:00:00"]),
    ],
)
def test_build_slot_starts_keeps_entire_slot_inside_free_interval(fraction, end, expected):
    start_dt = datetime.fromisoformat(f"2026-04-29T10:00:{fraction}+00:00")
    end_dt = datetime.fromisoformat(f"2026-04-29T{end}+00:00")

    slots = build_slot_starts([(start_dt, end_dt)], 30)

    assert slots == [
        datetime.fromisoformat(f"2026-04-29T{clock}+00:00") for clock in expected
    ]
    assert all(start_dt <= slot and slot + timedelta(minutes=30) <= end_dt for slot in slots)


def test_build_slot_starts_handles_midnight_and_skips_empty_intervals():
    start = datetime.fromisoformat("2026-04-29T23:30:00.000001+00:00")
    midnight = datetime.fromisoformat("2026-04-30T00:00:00+00:00")
    end = midnight + timedelta(minutes=30)

    assert build_slot_starts([(end, start), (start, start), (start, end)], 30) == [midnight]


@pytest.mark.parametrize(("start", "end", "expected"), [
    ("2030-11-03T06:00:00+00:00", "2030-11-03T07:00:00+00:00",
     ["2030-11-03T01:00:00-05:00", "2030-11-03T01:30:00-05:00"]),
    ("2030-03-10T06:30:00+00:00", "2030-03-10T07:30:00+00:00",
     ["2030-03-10T01:30:00-05:00", "2030-03-10T03:00:00-04:00"]),
])
def test_slot_grid_steps_elapsed_instants_through_dst(start, end, expected):
    zone = ZoneInfo("America/New_York")
    start_dt, end_dt = [datetime.fromisoformat(value).astimezone(zone) for value in (start, end)]
    slots = build_slot_starts([(start_dt, end_dt)], 30)
    assert [slot.isoformat() for slot in slots] == expected


def test_slot_grid_keeps_local_alignment_in_fractional_offset_zone():
    start = datetime(2030, 4, 26, 9, tzinfo=ZoneInfo("Asia/Kathmandu"))
    assert build_slot_starts([(start, start + timedelta(hours=1))], 30) == [start, start + timedelta(minutes=30)]


@pytest.mark.parametrize('aware', [True, False])
def test_first_slot_after_unaligned_start_before_fallback_is_chronological(aware):
    zone = ZoneInfo('America/New_York') if aware else None
    start = datetime(2027, 11, 7, 1, 50, tzinfo=zone)
    end = datetime(2027, 11, 7, 2, 30, tzinfo=zone)
    slots = build_slot_starts([(start, end)], 30)
    assert [slot.isoformat() for slot in slots] == (
        ['2027-11-07T01:00:00-05:00', '2027-11-07T01:30:00-05:00', '2027-11-07T02:00:00-05:00']
        if aware else ['2027-11-07T02:00:00']
    )


@pytest.mark.parametrize('zone,duration,start,end,expected', [
    ('America/New_York', 45, '2027-03-14T06:30:00+00:00', '2027-03-14T08:00:00+00:00',
     ['2027-03-14T01:30:00-05:00', '2027-03-14T03:00:00-04:00']),
    ('Australia/Lord_Howe', 15, '2027-04-03T14:50:00+00:00', '2027-04-03T16:00:00+00:00',
     ['2027-04-04T01:30:00+10:30', '2027-04-04T01:45:00+10:30',
      '2027-04-04T02:00:00+10:30', '2027-04-04T02:15:00+10:30']),
])
def test_zoned_grid_preserves_local_alignment_across_gaps_and_half_hour_folds(zone, duration, start, end, expected):
    from src.tools.business._calendar_utils import build_zoned_slot_starts

    slots = build_zoned_slot_starts([(datetime.fromisoformat(start), datetime.fromisoformat(end))],
                                    duration, ZoneInfo(zone))
    assert [slot.isoformat() for slot in slots] == expected
