from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from dateutil.rrule import rrulestr

from .utils import to_local

@dataclass
class Event:
    id: Optional[int]
    title: str
    description: Optional[str]
    location: Optional[str]
    start_utc: datetime
    end_utc: datetime
    all_day: bool = False
    rrule: Optional[str] = None
    exdates: List[datetime] = field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def duration(self) -> timedelta:
        return self.end_utc - self.start_utc

@dataclass
class Occurrence:
    event: Event
    start_utc: datetime
    end_utc: datetime

    def display_title(self) -> str:
        loc = f" @ {self.event.location}" if self.event.location else ""
        return f"{self.event.title}{loc}"

    def display_time_range_local(self) -> str:
        s = to_local(self.start_utc)
        e = to_local(self.end_utc)
        if self.event.all_day:
            return f"{s.strftime('%Y-%m-%d')} (all day)"
        same_day = s.date() == e.date()
        if same_day:
            return f"{s.strftime('%Y-%m-%d %H:%M')} - {e.strftime('%H:%M')}"
        return f"{s.strftime('%Y-%m-%d %H:%M')} - {e.strftime('%Y-%m-%d %H:%M')}"

def expand_event(event: Event, window_start_utc: datetime, window_end_utc: datetime) -> List[Occurrence]:
    out: List[Occurrence] = []
    base_dt = event.start_utc
    duration = event.duration
    exset = {d.replace(microsecond=0) for d in event.exdates}
    # Single occurrence falls within window?
    if not event.rrule:
        if event.end_utc > window_start_utc and base_dt < window_end_utc:
            out.append(Occurrence(event, base_dt, base_dt + duration))
        return out
    # Recurring
    rule = rrulestr(event.rrule, dtstart=base_dt)
    # between() includes boundaries if inc=True
    for dt in rule.between(window_start_utc, window_end_utc, inc=True):
        if dt.replace(tzinfo=base_dt.tzinfo).replace(microsecond=0) in exset:
            continue
        out.append(Occurrence(event, dt, dt + duration))
    return out
