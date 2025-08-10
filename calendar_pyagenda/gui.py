from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox
from tkcalendar import Calendar, DateEntry
from datetime import datetime, timedelta
from typing import Optional

from . import db
from .models import Event, Occurrence
from .utils import to_utc, to_local, dt_range_day_local, now_utc

class EventDialog(tk.Toplevel):
    def __init__(self, master, event: Optional[Event] = None):
        super().__init__(master)
        self.title("Event")
        self.resizable(False, False)
        self.result: Optional[Event] = None
        # Widgets
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Title:").grid(row=0, column=0, sticky="w")
        self.title_var = tk.StringVar(value=event.title if event else "")
        ttk.Entry(frm, textvariable=self.title_var, width=40).grid(row=0, column=1, sticky="w")

        ttk.Label(frm, text="Date:").grid(row=1, column=0, sticky="w")
        self.date_var = tk.StringVar()
        self.date_entry = DateEntry(frm, textvariable=self.date_var, date_pattern="yyyy-mm-dd", width=12)
        self.date_entry.grid(row=1, column=1, sticky="w")

        ttk.Label(frm, text="Start time (HH:MM):").grid(row=2, column=0, sticky="w")
        self.time_var = tk.StringVar(value="09:00")
        time_entry = ttk.Entry(frm, textvariable=self.time_var, width=8)
        time_entry.grid(row=2, column=1, sticky="w")

        self.all_day_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, text="All day", variable=self.all_day_var, command=self._toggle_time).grid(row=3, column=0, sticky="w")

        ttk.Label(frm, text="Duration (min):").grid(row=4, column=0, sticky="w")
        self.duration_var = tk.IntVar(value=60)
        ttk.Entry(frm, textvariable=self.duration_var, width=8).grid(row=4, column=1, sticky="w")

        ttk.Label(frm, text="Location:").grid(row=5, column=0, sticky="w")
        self.location_var = tk.StringVar(value=event.location if event else "")
        ttk.Entry(frm, textvariable=self.location_var, width=40).grid(row=5, column=1, sticky="w")

        ttk.Label(frm, text="Description:").grid(row=6, column=0, sticky="nw")
        self.desc_txt = tk.Text(frm, width=40, height=4)
        self.desc_txt.grid(row=6, column=1, sticky="w")

        ttk.Label(frm, text="RRULE:").grid(row=7, column=0, sticky="w")
        self.rrule_var = tk.StringVar(value=event.rrule if event else "")
        ttk.Entry(frm, textvariable=self.rrule_var, width=40).grid(row=7, column=1, sticky="w")

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, columnspan=2, pady=10)
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=5)
        ttk.Button(btns, text="Cancel", command=self._cancel).pack(side="left")

        # Prefill if editing
        if event:
            start_local = to_local(event.start_utc)
            self.date_var.set(start_local.strftime("%Y-%m-%d"))
            self.time_var.set(start_local.strftime("%H:%M"))
            self.all_day_var.set(event.all_day)
            self._toggle_time()
            if event.description:
                self.desc_txt.insert("1.0", event.description)
            self.duration_var.set(int((event.end_utc - event.start_utc).total_seconds() // 60))

        self.grab_set()
        self.wait_visibility()
        self.focus()

    def _toggle_time(self):
        state = "disabled" if self.all_day_var.get() else "normal"
        # The time entry is the 3rd row widget 1
        # We can access via self.time_var bound Entry
        # No-op; Entry remains editable/read-only via state if needed.

    def _save(self):
        title = self.title_var.get().strip()
        if not title:
            messagebox.showerror("Error", "Title required")
            return
        date_str = self.date_var.get()
        time_str = None if self.all_day_var.get() else self.time_var.get().strip()
        try:
            from .utils import parse_date_time
            start_utc = parse_date_time(date_str, time_str)
        except Exception as e:
            messagebox.showerror("Error", f"Invalid date/time: {e}")
            return
        duration = max(1, int(self.duration_var.get()))
        end_utc = start_utc + timedelta(minutes=duration)
        self.result = Event(
            id=None,
            title=title,
            description=self.desc_txt.get("1.0", "end").strip() or None,
            location=self.location_var.get().strip() or None,
            start_utc=start_utc,
            end_utc=end_utc,
            all_day=self.all_day_var.get(),
            rrule=self.rrule_var.get().strip() or None,
            exdates=[],
        )
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Calendar")
        self.geometry("720x420")

        self.pane = ttk.Panedwindow(self, orient="horizontal")
        self.pane.pack(fill="both", expand=True)

        left = ttk.Frame(self.pane)
        right = ttk.Frame(self.pane)
        self.pane.add(left, weight=1)
        self.pane.add(right, weight=2)

        # Left: month calendar
        self.cal = Calendar(left, selectmode="day")
        self.cal.pack(fill="both", expand=True, padx=8, pady=8)
        self.cal.bind("<<CalendarSelected>>", lambda e: self.refresh_list())

        # Right: event list and buttons
        top = ttk.Frame(right)
        top.pack(fill="both", expand=True, padx=8, pady=(8,0))

        self.list = tk.Listbox(top)
        self.list.pack(fill="both", expand=True)

        btns = ttk.Frame(right)
        btns.pack(fill="x", padx=8, pady=8)
        ttk.Button(btns, text="Add", command=self.on_add).pack(side="left", padx=4)
        ttk.Button(btns, text="Edit", command=self.on_edit).pack(side="left", padx=4)
        ttk.Button(btns, text="Delete", command=self.on_delete).pack(side="left", padx=4)
        ttk.Button(btns, text="Refresh", command=self.refresh_list).pack(side="right", padx=4)

        self.current_occurrences: list[Occurrence] = []
        self.refresh_list()

    def selected_date_local(self) -> datetime:
        d = self.cal.selection_get()
        return datetime(d.year, d.month, d.day)

    def refresh_list(self):
        self.list.delete(0, "end")
        day_local = self.selected_date_local()
        start_utc, end_utc = dt_range_day_local(day_local)
        self.current_occurrences = db.occurrences_between(start_utc, end_utc)
        for o in self.current_occurrences:
            self.list.insert("end", f"[{o.event.id}] {o.display_time_range_local()} :: {o.display_title()}")

    def pick_occurrence(self) -> Optional[Occurrence]:
        sel = self.list.curselection()
        if not sel:
            return None
        idx = sel[0]
        return self.current_occurrences[idx]

    def on_add(self):
        dlg = EventDialog(self)
        if dlg.result:
            ev = dlg.result
            new_id = db.add_event(ev)
            # reload
            self.refresh_list()

    def on_edit(self):
        occ = self.pick_occurrence()
        if not occ:
            messagebox.showinfo("Edit", "Select an event")
            return
        ev = db.get_event(occ.event.id)  # type: ignore[arg-type]
        if ev is None:
            return
        dlg = EventDialog(self, ev)
        if dlg.result:
            edited = dlg.result
            edited.id = ev.id
            # Preserve exdates
            edited.exdates = ev.exdates
            db.update_event(edited)
            self.refresh_list()

    def on_delete(self):
        occ = self.pick_occurrence()
        if not occ:
            messagebox.showinfo("Delete", "Select an event")
            return
        if messagebox.askyesno("Delete", f"Delete event [{occ.event.id}] '{occ.event.title}'?"):
            db.delete_event(occ.event.id)  # type: ignore[arg-type]
            self.refresh_list()

def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
