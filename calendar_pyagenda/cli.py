from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
from typing import Optional

import tkinter as tk
from tkinter import ttk, messagebox
from tkcalendar import DateEntry

from . import db
from .models import Event
from .utils import parse_date_time, to_utc, now_utc
from .ics_import import import_ics

def pick_datetime_gui() -> Optional[tuple[str, Optional[str]]]:
    # Returns (YYYY-MM-DD, HH:MM) or None if cancelled
    root = tk.Tk()
    root.title("Select date and time")
    root.geometry("280x160")
    root.resizable(False, False)

    frm = ttk.Frame(root, padding=10)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Date:").grid(row=0, column=0, sticky="w")
    date_var = tk.StringVar()
    date_entry = DateEntry(frm, textvariable=date_var, date_pattern="yyyy-mm-dd", width=12)
    date_entry.grid(row=0, column=1, sticky="w")

    time_var_h = tk.StringVar(value="09")
    time_var_m = tk.StringVar(value="00")
    ttk.Label(frm, text="Time:").grid(row=1, column=0, sticky="w")
    spin_h = ttk.Spinbox(frm, from_=0, to=23, width=5, textvariable=time_var_h, format="%02.0f")
    spin_m = ttk.Spinbox(frm, from_=0, to=59, width=5, textvariable=time_var_m, format="%02.0f")
    spin_h.grid(row=1, column=1, sticky="w")
    spin_m.grid(row=1, column=1, padx=(50,0), sticky="w")

    all_day_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(frm, text="All day", variable=all_day_var).grid(row=2, column=0, columnspan=2, sticky="w")

    result: dict[str, Optional[str]] = {"date": None, "time": None}
    def on_ok():
        d = date_var.get()
        if not d:
            messagebox.showerror("Error", "Please select a date")
            return
        if all_day_var.get():
            result["date"] = d
            result["time"] = None
        else:
            h = time_var_h.get().zfill(2)
            m = time_var_m.get().zfill(2)
            result["date"] = d
            result["time"] = f"{h}:{m}"
        root.destroy()

    def on_cancel():
        result["date"] = None
        result["time"] = None
        root.destroy()

    btns = ttk.Frame(frm)
    btns.grid(row=3, column=0, columnspan=2, pady=10)
    ttk.Button(btns, text="OK", command=on_ok).pack(side="left", padx=5)
    ttk.Button(btns, text="Cancel", command=on_cancel).pack(side="left")

    root.mainloop()
    if result["date"] is None:
        return None
    return result["date"], result["time"]

def cmd_add(args: argparse.Namespace) -> None:
    title = args.title
    if not title:
        print("Title is required")
        return
    date_str = args.date
    time_str = args.time
    all_day = args.all_day
    if date_str is None:
        picked = pick_datetime_gui()
        if not picked:
            print("Cancelled.")
            return
        date_str, time_str = picked
        all_day = time_str is None
    start_utc = parse_date_time(date_str, None if all_day else time_str)
    duration = timedelta(minutes=args.duration)
    end_utc = start_utc + duration
    ev = Event(
        id=None,
        title=title,
        description=args.description,
        location=args.location,
        start_utc=start_utc,
        end_utc=end_utc,
        all_day=all_day,
        rrule=args.rrule,
        exdates=[],
    )
    new_id = db.add_event(ev)
    # Handle notification preference
    if args.notify is not None:
        try:
            norm = db.normalize_notify_arg(args.notify)
            # Only store if differs from default or is 'never'
            if norm == "never" or norm != db.DEFAULT_NOTIFY:
                db.set_event_notify(new_id, norm)
            print(f"Added event #{new_id}: {title} (notify={norm})")
            return
        except ValueError as e:
            print(f"Added event #{new_id}: {title} (notify=DEFAULT). Invalid --notify ignored: {e}")
            return
    print(f"Added event #{new_id}: {title}")

def cmd_list(args: argparse.Namespace) -> None:
    now = now_utc()
    window_end = now + timedelta(days=args.days)
    occs = db.occurrences_between(now, window_end)
    if not occs:
        print("No upcoming events.")
        return
    for o in occs:
        print(f"- [{o.event.id}] {o.display_title()} :: {o.display_time_range_local()}")

def cmd_import(args: argparse.Namespace) -> None:
    count = import_ics(args.path)
    print(f"Imported {count} event(s).")

def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cal-cli", description="Lightweight calendar CLI")
    sub = p.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="Add an event")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--date", help="YYYY-MM-DD (omit to open picker)")
    p_add.add_argument("--time", help="HH:MM (optional; ignored for --all-day)")
    p_add.add_argument("--duration", type=int, default=60, help="Duration in minutes (default 60)")
    p_add.add_argument("--description")
    p_add.add_argument("--location")
    p_add.add_argument("--all-day", action="store_true")
    p_add.add_argument("--rrule", help="RRULE, e.g., FREQ=WEEKLY;BYDAY=MO,WE", default=None)
    p_add.add_argument(
        "--notify",
        help=(
            "Comma-separated thresholds controlling notifications for this event. "
            "Allowed: month,week,day,hour,now,never,default. "
            "Default (if omitted): month,week,day,hour,now. "
            "Example: --notify hour,now  (only near-event reminders) ; --notify never (disable)."
        ),
        default=None,
    )
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="List upcoming events")
    p_list.add_argument("--days", type=int, default=14, help="How many days ahead (default 14)")
    p_list.set_defaults(func=cmd_list)

    p_imp = sub.add_parser("import-ics", help="Import iCalendar file")
    p_imp.add_argument("path")
    p_imp.set_defaults(func=cmd_import)

    return p

def main(argv: Optional[list[str]] = None) -> None:
    parser = make_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return
    args.func(args)

if __name__ == "__main__":
    main()
