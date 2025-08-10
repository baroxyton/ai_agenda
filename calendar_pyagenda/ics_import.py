from __future__ import annotations
from typing import Tuple, Optional, List
from datetime import datetime, timedelta, timezone

from icalendar import Calendar as ICal
from icalendar import Event as ICalEvent

from .models import Event
from .utils import to_utc
from . import db

def _get_dt(v) -> datetime:
    # v may be date or datetime
    if isinstance(v.dt, datetime):
        dt = v.dt
    else:
        # all-day date -> midnight local
        from datetime import time
        dt = datetime.combine(v.dt, time(0, 0))
    if dt.tzinfo is None:
        # assume local if missing tz
        return to_utc(dt)
    return dt.astimezone(timezone.utc)

def _rrule_to_str(rrule_val) -> Optional[str]:
    if not rrule_val:
        return None
    try:
        # vRecur to bytes
        return rrule_val.to_ical().decode("utf-8")
    except Exception:
        # Sometimes it's a dict-like; icalendar can stringify it
        return str(rrule_val)

def import_ics(path: str) -> int:
    with open(path, "rb") as f:
        cal = ICal.from_ical(f.read())
    count = 0
    for comp in cal.walk():
        if comp.name != "VEVENT":
            continue
        ev: ICalEvent = comp  # type: ignore[assignment]
        summary = str(ev.get("SUMMARY", "Untitled"))
        desc = str(ev.get("DESCRIPTION", "")) or None
        loc = str(ev.get("LOCATION", "")) or None
        dtstart = _get_dt(ev.get("DTSTART"))
        dtend_prop = ev.get("DTEND")
        if dtend_prop is not None:
            dtend = _get_dt(dtend_prop)
        else:
            # If no DTEND, use DURATION or default 1 hour
            duration = ev.get("DURATION")
            if duration:
                dtend = dtstart + duration.dt  # type: ignore[attr-defined]
            else:
                dtend = dtstart + timedelta(hours=1)
        all_day = False
        if isinstance(ev.get("DTSTART").dt, datetime) is False:
            all_day = True
        rrule_str = _rrule_to_str(ev.get("RRULE"))
        # EXDATE can appear multiple times; normalize to list of datetimes
        exdates: List[datetime] = []
        for ex in ev.getall("EXDATE"):
            vals = ex.dts
            for v in vals:
                exdates.append(_get_dt(v))
        new = Event(
            id=None,
            title=summary,
            description=desc,
            location=loc,
            start_utc=dtstart,
            end_utc=dtend,
            all_day=all_day,
            rrule=rrule_str,
            exdates=exdates,
        )
        db.add_event(new)
        count += 1
    return count
