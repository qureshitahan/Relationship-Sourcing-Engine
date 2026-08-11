"""Stagger campaign sends inside a local business-hours window."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Aliased: staggered_send_times_utc() takes a parameter named "timezone", which
# would otherwise shadow the datetime one inside that function.
UTC = dt_timezone.utc

# A/B time-of-day buckets (local hour, minute) the agent rotates between so we
# learn which send time earns more replies. Both are inside business hours.
AB_SEND_BUCKETS: list[tuple[int, int]] = [(9, 30), (13, 30)]

# Lightweight US location -> IANA timezone mapping. We only need the 4 mainland
# zones plus AK/HI; matching is done on substrings of the person's location text
# (city, state name, or 2-letter state code) reported by Apollo/LinkedIn.
_PACIFIC = "America/Los_Angeles"
_MOUNTAIN = "America/Denver"
_CENTRAL = "America/Chicago"
_EASTERN = "America/New_York"

_STATE_TZ: dict[str, str] = {
    # Pacific
    "ca": _PACIFIC, "california": _PACIFIC, "wa": _PACIFIC, "washington": _PACIFIC,
    "or": _PACIFIC, "oregon": _PACIFIC, "nv": _PACIFIC, "nevada": _PACIFIC,
    # Mountain
    "az": _MOUNTAIN, "arizona": _MOUNTAIN, "co": _MOUNTAIN, "colorado": _MOUNTAIN,
    "ut": _MOUNTAIN, "utah": _MOUNTAIN, "nm": _MOUNTAIN, "new mexico": _MOUNTAIN,
    "id": _MOUNTAIN, "idaho": _MOUNTAIN, "mt": _MOUNTAIN, "montana": _MOUNTAIN,
    "wy": _MOUNTAIN, "wyoming": _MOUNTAIN,
    # Central
    "tx": _CENTRAL, "texas": _CENTRAL, "il": _CENTRAL, "illinois": _CENTRAL,
    "mn": _CENTRAL, "minnesota": _CENTRAL, "wi": _CENTRAL, "wisconsin": _CENTRAL,
    "mo": _CENTRAL, "missouri": _CENTRAL, "ia": _CENTRAL, "iowa": _CENTRAL,
    "ks": _CENTRAL, "kansas": _CENTRAL, "ne": _CENTRAL, "nebraska": _CENTRAL,
    "ok": _CENTRAL, "oklahoma": _CENTRAL, "ar": _CENTRAL, "arkansas": _CENTRAL,
    "la": _CENTRAL, "louisiana": _CENTRAL, "tn": _CENTRAL, "tennessee": _CENTRAL,
    "al": _CENTRAL, "alabama": _CENTRAL, "ms": _CENTRAL, "mississippi": _CENTRAL,
    "nd": _CENTRAL, "sd": _CENTRAL,
    # Hawaii / Alaska
    "hi": "Pacific/Honolulu", "hawaii": "Pacific/Honolulu",
    "ak": "America/Anchorage", "alaska": "America/Anchorage",
}

_CITY_TZ: dict[str, str] = {
    "san francisco": _PACIFIC, "los angeles": _PACIFIC, "seattle": _PACIFIC,
    "portland": _PACIFIC, "san diego": _PACIFIC, "san jose": _PACIFIC,
    "denver": _MOUNTAIN, "phoenix": _MOUNTAIN, "salt lake": _MOUNTAIN,
    "chicago": _CENTRAL, "dallas": _CENTRAL, "houston": _CENTRAL,
    "austin": _CENTRAL, "minneapolis": _CENTRAL, "nashville": _CENTRAL,
    "new york": _EASTERN, "boston": _EASTERN, "atlanta": _EASTERN,
    "miami": _EASTERN, "washington": _EASTERN, "philadelphia": _EASTERN,
}


def _zone(name: str):
    """Resolve an IANA zone, falling back to UTC if the host has no tz database.

    ``ZoneInfo`` reads the operating system's zoneinfo files, which Linux ships
    and Windows does not — there the ``tzdata`` package supplies them. When
    neither is present this raises, and because it is called per recipient inside
    the send stage, one missing package took down the whole autopilot run and the
    campaign reported a crash with nothing sent.

    Send timing is a nicety; sending at all is not. Degrade to UTC and log it.
    """
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - any tz database problem, not just missing
        logger.warning(
            "Timezone %r unavailable (install the 'tzdata' package for local send "
            "timing); scheduling in UTC instead",
            name,
        )
        return UTC


def timezone_for_location(location: str | None, default: str = _EASTERN) -> str:
    """Best-effort IANA timezone from a free-text US location (city/state)."""
    if not location:
        return default
    text = location.lower()
    for city, tz in _CITY_TZ.items():
        if city in text:
            return tz
    # Match trailing/standalone state codes or names.
    tokens = [t.strip(" ,.").lower() for t in text.replace(",", " ").split()]
    for tok in tokens:
        if tok in _STATE_TZ:
            return _STATE_TZ[tok]
    for name, tz in _STATE_TZ.items():
        if len(name) > 3 and name in text:
            return tz
    return default


def personalized_send_time_utc(
    *,
    location: str | None,
    order_index: int,
    ab_index: int,
    gap_minutes: int = 7,
    day_offset: int = 0,
    default_tz: str = _EASTERN,
) -> datetime:
    """Pick a UTC send time at a good local hour in the RECIPIENT'S timezone.

    - The local time-of-day is chosen from ``AB_SEND_BUCKETS[ab_index]`` so the
      agent can A/B test morning vs. afternoon sends.
    - ``order_index`` spaces sends out (``gap_minutes`` apart) so a batch never
      fires all at once from the same mailbox.
    - ``day_offset`` starts on a future calendar day (0 = today) so large
      batches can be packed across days without blowing a daily send cap.
    - Stagger is clamped so the slot stays before 6pm local on the intended day.
    - If today's slot has already passed, it rolls to the next day.
    """
    tz = _zone(timezone_for_location(location, default_tz))
    bucket = AB_SEND_BUCKETS[ab_index % len(AB_SEND_BUCKETS)]
    gap = max(1, int(gap_minutes))
    now_local = datetime.now(tz)
    day_offset = max(0, int(day_offset))

    # Clamp stagger so we never leave business hours on the intended day
    # (otherwise large batches quietly spill into the following day).
    end_minute = 17 * 60 + 45  # ~5:45pm local
    bucket_minute = bucket[0] * 60 + bucket[1]
    max_steps = max(0, (end_minute - bucket_minute) // gap)
    stagger = min(max(0, int(order_index)), max_steps) * gap

    base_day = now_local + timedelta(days=day_offset)
    target = base_day.replace(
        hour=bucket[0], minute=bucket[1], second=0, microsecond=0
    ) + timedelta(minutes=stagger)

    # Only roll forward when the intended day is today and the slot is gone.
    if day_offset == 0 and target <= now_local:
        target = (now_local + timedelta(days=1)).replace(
            hour=bucket[0], minute=bucket[1], second=0, microsecond=0
        ) + timedelta(minutes=stagger)
    return target.astimezone(UTC).replace(tzinfo=None)


def staggered_send_times_utc(
    *,
    count: int,
    timezone: str = "America/New_York",
    start_hour: int = 9,
    end_hour: int = 17,
    gap_minutes: int = 2,
) -> list[datetime]:
    """Return UTC datetimes for ``count`` sends, spaced ``gap_minutes`` apart.

    Starts at the next slot inside [start_hour, end_hour) in the given timezone.
    Rolls to the next business day if the window is full.
    """
    if count <= 0:
        return []

    tz = _zone(timezone or "America/New_York")
    start_hour = max(0, min(23, int(start_hour)))
    end_hour = max(start_hour + 1, min(24, int(end_hour)))
    gap = max(1, int(gap_minutes))

    now_local = datetime.now(tz)
    slot = now_local.replace(second=0, microsecond=0)

    # If before window today, start at window open.
    if slot.hour < start_hour:
        slot = slot.replace(hour=start_hour, minute=0)
    elif slot.hour >= end_hour:
        slot = (slot + timedelta(days=1)).replace(hour=start_hour, minute=0)

    window_minutes = (end_hour - start_hour) * 60
    max_per_day = max(1, window_minutes // gap)

    out: list[datetime] = []
    day_offset = 0
    index_in_day = 0

    while len(out) < count:
        if index_in_day >= max_per_day:
            day_offset += 1
            index_in_day = 0
        base = (now_local + timedelta(days=day_offset)).replace(
            hour=start_hour, minute=0, second=0, microsecond=0
        )
        candidate = base + timedelta(minutes=index_in_day * gap)
        if day_offset == 0 and candidate < now_local:
            # First day: bump to now + small buffer if we're already in-window.
            candidate = max(candidate, now_local + timedelta(minutes=1))
            if candidate.hour >= end_hour:
                day_offset += 1
                index_in_day = 0
                continue
        out.append(candidate.astimezone(UTC).replace(tzinfo=None))
        index_in_day += 1

    return out
