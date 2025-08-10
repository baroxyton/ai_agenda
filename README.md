# calendar_pyagenda

Fast agenda. Paste invite. Accept. Done.

A lightweight, AI‑assisted personal calendar / agenda focused on speed, local storage, and minimal dependencies. No heavy Electron tray apps, no vendor lock‑in. Just fast CLI + simple Tk GUI + smart natural language ingestion.

## Vibe coding disclosure

This project was wholly vibe-coded. Some features may not work as expected. Public domain, use at your own risk.

## Highlights

- Zero-cloud: events stored locally in a small SQLite database (`~/.local/share/calendar_pyagenda/calendar.db`).
- AI event creation (`cal-ai`): Paste a whole meeting invitation email (raw text) and it extracts the key details (title, date/time, duration, description, location, recurrence, custom notifications).
- Smart notifications: Background daemon (`cal-notify`) sends progressive reminders (month, week, day, hour, now) without a full system tray resident app.
- Per-event custom notify schedule (e.g. `hour,now` or `never`), stored once.
- Natural language refinement loop: Accept / modify AI proposal interactively.
- CLI tools (`cal-cli`) for adding/listing/importing events fast.
- GUI (`cal-gui`) for quick day browsing & CRUD.
- Recurring events via standard RRULE (powered by `dateutil.rrule`).
- ICS import (`cal-cli import-ics path.ics`).
- Timezone‑safe: All timestamps stored in UTC; display converted to local.
- Ex-dates supported (from ICS) to skip specific occurrence dates.
- Minimal footprint: Tk + notify2; no giant frameworks.

## Quick Install

```bash
git clone <this-repo>
cd calendar_pyagenda
./install.sh
```

This creates a virtual environment, installs dependencies, places shims in `~/.local/bin`:

Commands available after adding `~/.local/bin` to PATH:
- `cal-cli`
- `cal-gui`
- `cal-notify` (foreground run)
- `cal-ai`

A systemd user service `calendar-notify.service` is installed & (re)started automatically for background reminders.

## Fast Usage

Add an event (prompting a date picker if no date provided):
```bash
cal-cli add --title "Project sync" --date 2025-08-14 --time 10:00 --duration 30 --location "Room 3"
```

List upcoming (next 14 days default):
```bash
cal-cli list
```

Import from ICS:
```bash
cal-cli import-ics invite.ics
```

Launch simple GUI:
```bash
cal-gui
```

## AI: Paste an Invitation Email

Copy any invite / email text (subject + body). Then:

Wayland (wl-clipboard):
```bash
wl-paste | cal-ai
```

X11 (xclip):
```bash
xclip -selection clipboard -o | cal-ai
```

Generic (middle‑mouse primary selection on some setups):
```bash
printf "%s\n" "$(xsel -o)" | cal-ai
```

You can also just run `cal-ai` and paste manually when prompted.

Example source text (what you copy):
```
Subject: Strategy Review – Tuesday Sept 9, 14:30–15:15 CET
Hi team,
We'll meet to review Q4 strategy.
When: Tue Sep 9, 2025 2:30 PM - 3:15 PM CET
Where: Zoom link below
Please no early reminders, just near start.
Zoom: https://zoom.example/abc
```

Typical AI proposal (notify trimmed to near-event):
- Title extracted (e.g. "Strategy Review")
- Date/time normalized
- Duration inferred (45 min)
- Location (Zoom link) folded into description
- Notify: hour,now (because user asked for only near start)

Accept with 'y' or type a modification like:
```
Make it 60 minutes and rename to Strategy Deep Dive
```

## Notification Model

Default progressive schedule (if you do nothing):
```
month,week,day,hour,now
```

Customize per event:
- Disable entirely: `--notify never`
- Only last-minute: `--notify hour,now`
- Short horizon: `--notify day,hour,now`

CLI example:
```bash
cal-cli add --title "Dentist" --date 2025-09-01 --time 09:00 --notify day,hour,now
```

AI JSON will include `"notify"` ONLY if a non-default pattern is clearly implied (e.g. "no notification", "just remind me right before").

The notifier polls every 5 minutes and ensures each threshold fires once per occurrence (stored in `notifications` table).

## Recurrence (RRULE)

Use standard iCalendar RRULE strings, examples:
- Weekly Mondays+Wednesdays: `FREQ=WEEKLY;BYDAY=MO,WE`
- Daily for 5 days: `FREQ=DAILY;COUNT=5`

Add via CLI:
```bash
cal-cli add --title "Standup" --date 2025-08-11 --time 09:15 --rrule FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR --duration 15
```

## AI Configuration

First run configuration:
```bash
cal-ai --config
# Enter: base URL, model name, API key
```
Config saved at `~/.config/cal-ai/config.json` (0600 permissions).


## Architecture Overview

- Storage: SQLite (events, sent notification thresholds, per-event notify prefs).
- Expansion: Recurrence expanded on demand in memory (`expand_event`).
- Time handling: Convert to UTC on ingest; display localized.
- AI pipeline: System prompt -> attempt parse -> validate -> interactive modification loop (JSON only).
- Notifications: Threshold selection maps time-to-event into named window; deduplicated.

## Design Goals

1. Stay local-first.
2. Never block on network for core operations (AI optional).
3. Minimal cognitive overhead; one-liner event creation.
4. Extensible (RRULE, exdates, custom notify).
5. Plain text friendly (pipe inputs, paste emails).

## Future Ideas

- ICS export.
- GUI editing of notify schedule + exdates.
- Faster recurrence indexing.
- Natural language rescheduling commands.
- Optional CalDAV sync plugin.

## Troubleshooting

- No notifications? Ensure `systemctl --user status calendar-notify.service`.
- Wrong times? Check system timezone; events stored in UTC.
- AI not working? Run `cal-ai --config` and verify network / API key.

## License

Public domain
