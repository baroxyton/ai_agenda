from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Optional, Iterable
import json
from pathlib import Path

from tzlocal import get_localzone

from . import config

def ensure_dirs() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _attach_local_tz(dt: datetime) -> datetime:
    """Attach system local tz to a naïve datetime (pytz/zoneinfo compatible)."""
    tz = get_localzone()
    # pytz has .localize; zoneinfo does not.
    localize = getattr(tz, "localize", None)
    if callable(localize):
        return localize(dt)  # type: ignore[misc]
    return dt.replace(tzinfo=tz)

def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = _attach_local_tz(dt)
    return dt.astimezone(timezone.utc)

def to_local(dt_utc: datetime) -> datetime:
    return dt_utc.astimezone(get_localzone())

def iso(dt: datetime) -> str:
    # ISO 8601 with timezone
    return dt.astimezone(timezone.utc).isoformat()

def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)

def parse_date_time(date_str: str, time_str: Optional[str]) -> datetime:
    # date_str: YYYY-MM-DD, time_str: HH:MM or None (00:00)
    if time_str:
        h, m = [int(x) for x in time_str.split(":")]
    else:
        h, m = 0, 0
    from datetime import date, time
    local = datetime.combine(datetime.strptime(date_str, "%Y-%m-%d").date(), time(hour=h, minute=m))
    return to_utc(local)

def dt_range_day_local(day_local: datetime) -> tuple[datetime, datetime]:
    # Given a local date (time ignored), return UTC start/end covering that day
    local_start_naive = datetime(day_local.year, day_local.month, day_local.day, 0, 0, 0)
    local_start = _attach_local_tz(local_start_naive)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)

def dumps_exdates(exdates: Iterable[datetime]) -> str:
    return json.dumps([iso(d) for d in exdates])

def loads_exdates(s: Optional[str]) -> list[datetime]:
    if not s:
        return []
    return [parse_iso(x) for x in json.loads(s)]
