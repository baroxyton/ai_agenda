from __future__ import annotations
import time
from datetime import datetime, timedelta
import logging

import notify2
from tzlocal import get_localzone

from . import config, db
from .utils import now_utc, to_local, iso

CHECK_INTERVAL_SEC = 300  # 5 minutes

logger = logging.getLogger("calendar.notify")

def setup_logging():
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(config.LOG_PATH)
    fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
    handler.setFormatter(fmt)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

def init_notify():
    notify2.init(config.APP_NAME)

def pick_threshold_name(seconds_to_event: float) -> str | None:
    # New ultra-short phase independent from configured thresholds.
    if 0 <= seconds_to_event <= 4 * 60:
        return "now"
    # Choose the smallest threshold whose delta >= time_to_event
    for name, delta in sorted(config.THRESHOLDS, key=lambda x: x[1]):
        if 0 <= seconds_to_event <= delta.total_seconds():
            return name
    return None

def send_notification(summary: str, body: str):
    n = notify2.Notification(summary, body)
    n.set_urgency(notify2.URGENCY_NORMAL)
    n.show()

def check_once():
    now = now_utc()
    horizon = now + timedelta(days=30)
    occs = db.occurrences_between(now, horizon)
    sent_this_run = 0
    notify_cache: dict[int, str] = {}
    for occ in occs:
        time_to = (occ.start_utc - now).total_seconds()
        th = pick_threshold_name(time_to)
        if not th:
            continue
        # Fetch per-event notify preference (cached)
        ev_id = occ.event.id or -1
        if ev_id not in notify_cache:
            notify_cache[ev_id] = db.get_event_notify(ev_id)
        pref = notify_cache[ev_id]
        if pref == "never":
            continue
        allowed = set(pref.split(","))
        if th not in allowed:
            continue
        occ_start_iso = iso(occ.start_utc)
        if db.has_notified(ev_id, occ_start_iso, th):
            continue
        # Build message
        local_start = to_local(occ.start_utc)
        when_str = {
            "now": "now",
            "hour": "in about an hour",
            "day": "today",
            "week": "within a week",
            "month": "within a month",
        }.get(th, "soon")
        summary = f"Upcoming: {occ.event.title}"
        body = f"{when_str}\n{local_start.strftime('%Y-%m-%d %H:%M')} — {occ.event.location or ''}".strip()
        try:
            send_notification(summary, body)
            db.record_notified(ev_id, occ_start_iso, th)
            sent_this_run += 1
            logger.info("Notified: event_id=%s start=%s threshold=%s (prefs=%s)", ev_id, occ_start_iso, th, pref)
        except Exception as e:
            logger.exception("Notification failed: %s", e)
    return sent_this_run

def main():
    setup_logging()
    init_notify()
    logger.info("Notifier started")
    while True:
        try:
            check_once()
        except Exception as e:
            logger.exception("check_once failed: %s", e)
        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    main()
