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
