"""Shared calendar helpers for business calendar tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo


def get_zoneinfo(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo((tz_name or "").strip() or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def parse_iso_datetime(dt_str: str) -> datetime:
    raw = (dt_str or "").strip()
    if not raw:
        raise ValueError("Empty datetime string")
    if raw.upper().endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def normalize_to_tz(dt_str: str, tz_name: str | None, respect_offset: bool = True) -> datetime:
    """Parse an ISO datetime and return it in the requested timezone.

    When respect_offset is true, timezone-aware inputs are treated as instants.
    Naive inputs are interpreted as wall-clock time in tz_name.
    """
    tz = get_zoneinfo(tz_name)
    parsed = parse_iso_datetime(dt_str)
    if parsed.tzinfo is not None and respect_offset:
        return parsed.astimezone(tz)
    return parsed.replace(tzinfo=tz)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def graph_datetime(dt: datetime) -> str:
    """Return a Microsoft Graph DateTimeTimeZone dateTime string in UTC."""
    return to_utc(dt).replace(tzinfo=None).isoformat(timespec="seconds")


def coerce_hour(raw_value, default: int) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    if value < 0 or value > 24:
        return default
    return value


def coerce_working_days(raw_value) -> set[int]:
    if isinstance(raw_value, list) and raw_value:
        try:
            days = {int(d) for d in raw_value if 0 <= int(d) <= 6}
            if days:
                return days
        except (TypeError, ValueError):
            pass
    return {0, 1, 2, 3, 4}


def working_hours_mask(
    range_start: datetime,
    range_end: datetime,
    tz_name: str,
    work_start_hour: int,
    work_end_hour: int,
    work_days: set[int],
) -> list[tuple[datetime, datetime]]:
    tz = get_zoneinfo(tz_name)
    rs = range_start.astimezone(tz)
    re_ = range_end.astimezone(tz)
    intervals: list[tuple[datetime, datetime]] = []
    cur_day = rs.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur_day <= re_:
        if cur_day.weekday() in work_days:
            day_open = cur_day.replace(hour=work_start_hour)
            day_close = (
                cur_day.replace(hour=work_end_hour)
                if work_end_hour < 24
                else cur_day + timedelta(days=1)
            )
            start = max(day_open, rs)
            end = min(day_close, re_)
            if start < end:
                intervals.append((start, end))
        cur_day = cur_day + timedelta(days=1)
    return intervals


def subtract_busy(
    free_blocks: Iterable[tuple[datetime, datetime]],
    busy_blocks: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    free_sorted = sorted(free_blocks, key=lambda item: item[0])
    busy_sorted = sorted(busy_blocks, key=lambda item: item[0])
    available: list[tuple[datetime, datetime]] = []
    for free_start, free_end in free_sorted:
        current_start = free_start
        for busy_start, busy_end in busy_sorted:
            if busy_end <= current_start or busy_start >= free_end:
                continue
            if current_start < busy_start:
                available.append((current_start, busy_start))
            current_start = max(current_start, busy_end)
        if current_start < free_end:
            available.append((current_start, free_end))
    return available


def union_intervals(interval_groups: Iterable[Iterable[tuple[datetime, datetime]]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for group in interval_groups:
        merged.extend(group)
    if not merged:
        return []
    merged.sort(key=lambda item: item[0])
    out: list[tuple[datetime, datetime]] = []
    cur_start, cur_end = merged[0]
    for start, end in merged[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            out.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    out.append((cur_start, cur_end))
    return out


def intersect_intervals(
    left: list[tuple[datetime, datetime]],
    right: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    i, j = 0, 0
    result: list[tuple[datetime, datetime]] = []
    left_sorted = sorted(left, key=lambda item: item[0])
    right_sorted = sorted(right, key=lambda item: item[0])
    while i < len(left_sorted) and j < len(right_sorted):
        start = max(left_sorted[i][0], right_sorted[j][0])
        end = min(left_sorted[i][1], right_sorted[j][1])
        if start < end:
            result.append((start, end))
        if left_sorted[i][1] < right_sorted[j][1]:
            i += 1
        else:
            j += 1
    return result


def round_up_to_next_slot(dt: datetime, step_minutes: int) -> datetime:
    """Align to a duration multiple from midnight without moving before dt."""
    total_minutes = dt.hour * 60 + dt.minute
    if dt.second or dt.microsecond or total_minutes % step_minutes != 0:
        # The integer ceiling below must include any fractional minute.
        if dt.second or dt.microsecond:
            total_minutes += 1
        quotient = (total_minutes + step_minutes - 1) // step_minutes
        new_total = quotient * step_minutes
        if new_total >= 24 * 60:
            days_add = new_total // (24 * 60)
            new_total = new_total % (24 * 60)
            base = dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_add)
            return base.replace(hour=new_total // 60, minute=new_total % 60)
        return dt.replace(hour=new_total // 60, minute=new_total % 60, second=0, microsecond=0)
    return dt


def build_slot_starts(
    intervals: Iterable[tuple[datetime, datetime]],
    duration_minutes: int,
) -> list[datetime]:
    duration = timedelta(minutes=duration_minutes)
    starts: list[datetime] = []
    for start, end in intervals:
        if start.tzinfo is not None:
            # Round all local grid points, including both folds, before choosing
            # the first chronological instant. Rounding the initial wall clock
            # alone can skip the later occurrence of an earlier local hour.
            starts.extend(build_zoned_slot_starts([(start, end)], duration_minutes, start.tzinfo))
            continue
        if end <= start:
            continue
        cursor = round_up_to_next_slot(start, duration_minutes)
        while cursor + duration <= end:
            starts.append(cursor)
            cursor += duration
    return starts


def build_zoned_slot_starts(
    intervals: Iterable[tuple[datetime, datetime]],
    duration_minutes: int,
    tz: ZoneInfo,
) -> list[datetime]:
    """Align local starts while checking elapsed duration across DST changes.

    All-day windows can span a clock change: nonexistent local times are not
    slots, and repeated times are distinct instants. Generate the local grid
    with both folds, round-trip through UTC, then check complete slot inclusion.
    """
    duration = timedelta(minutes=duration_minutes)
    starts: set[datetime] = set()
    for start, end in intervals:
        start_utc, end_utc = to_utc(start), to_utc(end)
        if end_utc <= start_utc:
            continue
        day = start_utc.astimezone(tz).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        last_day = end_utc.astimezone(tz).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
        while day <= last_day:
            next_day = day + timedelta(days=1)
            wall = day
            while wall < next_day:
                for fold in (0, 1):
                    candidate = wall.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)
                    if candidate.astimezone(tz).replace(tzinfo=None) != wall:
                        continue
                    if start_utc <= candidate and candidate + duration <= end_utc:
                        starts.add(candidate)
                wall += duration
            day = next_day
    return [start.astimezone(tz) for start in sorted(starts)]
