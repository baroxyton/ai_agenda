from __future__ import annotations
import sqlite3
from typing import List, Optional, Iterable, Tuple
from datetime import datetime, timezone, timedelta

from . import config
from .models import Event, Occurrence, expand_event
from .utils import ensure_dirs, iso, parse_iso, loads_exdates, dumps_exdates, now_utc

_conn: Optional[sqlite3.Connection] = None

# New notification preference constants/utilities
ALLOWED_THRESHOLDS = ["month", "week", "day", "hour", "now"]
DEFAULT_NOTIFY = ",".join(ALLOWED_THRESHOLDS)

def normalize_notify_arg(value: str) -> str:
    """
    Normalize a --notify argument.
    Returns canonical comma-separated string (e.g. 'day,hour,now') or 'never'.
    Raises ValueError on invalid input.
    Accepted tokens: month, week, day, hour, now, never, default
    """
    v = (value or "").strip().lower()
    if not v:
        raise ValueError("empty notify value")
    if v in ("default", "defaults"):
        return DEFAULT_NOTIFY
    if v == "never":
        return "never"
    parts = [p.strip() for p in v.split(",") if p.strip()]
    if not parts:
        raise ValueError("no thresholds specified")
    invalid = [p for p in parts if p not in ALLOWED_THRESHOLDS]
    if invalid:
        raise ValueError(f"invalid threshold(s): {', '.join(invalid)}")
    # Preserve canonical ordering, remove duplicates
    ordered = [t for t in ALLOWED_THRESHOLDS if t in parts]
    return ",".join(ordered)

def get_event_notify(event_id: int) -> str:
    """
    Returns the stored notify string or the default if none stored.
    """
    c = conn()
    row = c.execute("SELECT notify FROM event_notify WHERE event_id=?", (event_id,)).fetchone()
    if row and row["notify"]:
        return row["notify"]
    return DEFAULT_NOTIFY

def set_event_notify(event_id: int, notify: str) -> None:
    """
    Store/overwrite notify preference (canonical string or 'never').
    """
    c = conn()
    with c:
        c.execute(
            "INSERT INTO event_notify (event_id, notify) VALUES (?, ?) "
            "ON CONFLICT(event_id) DO UPDATE SET notify=excluded.notify",
            (event_id, notify),
        )

def _connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = _connect()
        init_db(_conn)
    return _conn

def init_db(c: sqlite3.Connection) -> None:
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            location TEXT,
            start_utc TEXT NOT NULL,
            end_utc TEXT NOT NULL,
            all_day INTEGER NOT NULL DEFAULT 0,
            rrule TEXT,
            exdates TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications (
            event_id INTEGER NOT NULL,
            occurrence_start TEXT NOT NULL,
            threshold TEXT NOT NULL,
            notified_at TEXT NOT NULL,
            PRIMARY KEY (event_id, occurrence_start, threshold),
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS event_notify (
            event_id INTEGER PRIMARY KEY,
            notify TEXT NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
        );
        -- notifier_day table removed (per-day global cap deprecated)
        """
    )

def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        location=row["location"],
        start_utc=parse_iso(row["start_utc"]),
        end_utc=parse_iso(row["end_utc"]),
        all_day=bool(row["all_day"]),
        rrule=row["rrule"],
        exdates=loads_exdates(row["exdates"]),
        created_at=parse_iso(row["created_at"]),
        updated_at=parse_iso(row["updated_at"]),
    )

def add_event(ev: Event) -> int:
    c = conn()
    now = iso(now_utc())
    with c:
        cur = c.execute(
            """
            INSERT INTO events (title, description, location, start_utc, end_utc, all_day, rrule, exdates, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ev.title,
                ev.description,
                ev.location,
                iso(ev.start_utc),
                iso(ev.end_utc),
                1 if ev.all_day else 0,
                ev.rrule,
                dumps_exdates(ev.exdates),
                now,
                now,
            ),
        )
        return int(cur.lastrowid)

def update_event(ev: Event) -> None:
    assert ev.id is not None
    c = conn()
    with c:
        c.execute(
            """
            UPDATE events SET title=?, description=?, location=?, start_utc=?, end_utc=?, all_day=?, rrule=?, exdates=?, updated_at=?
            WHERE id=?
            """,
            (
                ev.title,
                ev.description,
                ev.location,
                iso(ev.start_utc),
                iso(ev.end_utc),
                1 if ev.all_day else 0,
                ev.rrule,
                dumps_exdates(ev.exdates),
                iso(now_utc()),
                ev.id,
            ),
        )

def delete_event(event_id: int) -> None:
    c = conn()
    with c:
        c.execute("DELETE FROM events WHERE id=?", (event_id,))

def get_event(event_id: int) -> Optional[Event]:
    c = conn()
    row = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    return _row_to_event(row) if row else None

def list_events() -> List[Event]:
    c = conn()
    rows = c.execute("SELECT * FROM events ORDER BY start_utc ASC").fetchall()
    return [_row_to_event(r) for r in rows]

def occurrences_between(start_utc: datetime, end_utc: datetime) -> List[Occurrence]:
    out: List[Occurrence] = []
    for ev in list_events():
        out.extend(expand_event(ev, start_utc, end_utc))
    out.sort(key=lambda o: o.start_utc)
    return out

# Notifier state

def has_notified(event_id: int, occurrence_start_iso: str, threshold: str) -> bool:
    c = conn()
    row = c.execute(
        "SELECT 1 FROM notifications WHERE event_id=? AND occurrence_start=? AND threshold=?",
        (event_id, occurrence_start_iso, threshold),
    ).fetchone()
    return row is not None

def record_notified(event_id: int, occurrence_start_iso: str, threshold: str) -> None:
    c = conn()
    with c:
        c.execute(
            "INSERT OR IGNORE INTO notifications (event_id, occurrence_start, threshold, notified_at) VALUES (?, ?, ?, ?)",
            (event_id, occurrence_start_iso, threshold, iso(now_utc())),
        )

# Removed get_daily_count and inc_daily_count (no longer needed)
