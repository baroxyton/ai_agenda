from __future__ import annotations
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, List

from . import db
from .models import Event
from .utils import parse_date_time, now_utc

# Lazy import supporting both new (>=1.0) and legacy (<1.0) openai packages.
try:
    from openai import OpenAI  # new client class
except ImportError:
    OpenAI = None  # type: ignore
try:
    import openai as openai_legacy  # legacy module (has ChatCompletion)
except ImportError:
    openai_legacy = None  # type: ignore

CONFIG_DIR = Path.home() / ".config" / "cal-ai"
CONFIG_PATH = CONFIG_DIR / "config.json"
MAX_TITLE_LEN = 120
RETRY_LIMIT = 3
MAX_MODIFY_ROUNDS = 5  # limit iterative user modification cycles

# NOTE: This string contains many literal JSON braces. Do NOT use str.format on it.
# We will manually replace only the specific placeholder tokens.
SYSTEM_PROMPT = """You are an assistant that converts natural language notes or instructions into a JSON object describing ONE calendar event for `cal-cli add`.

Current local baseline:
- CURRENT_LOCAL_DATETIME: {iso_now}
- LOCAL_DATE (today): {today}
- LOCAL_TIME (now): {now_time}
- LOCAL_TIMEZONE: {tz_name} (UTC{tz_offset})

Notification scheduling:
- Default schedule (if you omit `"notify"`): month,week,day,hour,now
  (progressive awareness windows).
- Allowed thresholds (comma-separated): month,week,day,hour,now
- Special value: never  (suppress all notifications)
- Include a "notify" field ONLY when a NON-default schedule is clearly better or suppression is desired.
  When to customize:
    * Extremely soon (<=3 hours): use "hour,now"
    * Soon (<24h but >3h) and short task: "day,hour,now"
    * User intent to avoid long-range noise (phrases like "just remind me shortly before"): "hour,now"
    * User explicitly wants no reminders (e.g. "log", "record only", "no notification"): "never"
    * Otherwise OMIT the "notify" field (do NOT redundantly output the default).
- Never invent exotic combinations outside allowed thresholds.
- If unsure, omit "notify" to accept default.

Output ONLY a single JSON object (no prose, no code fences, no explanation). Required keys:
- "title": short concise title (string, <= 120 chars)
- "date": start date YYYY-MM-DD (local)

Optional keys:
- "time": HH:MM 24h local start time. Omit or set null if all-day.
- "duration_minutes": positive integer (default 60 if omitted)
- "description": string
- "location": string
- "all_day": boolean (true if no specific start time)
- "rrule": string (iCalendar RRULE, e.g. "FREQ=WEEKLY;BYDAY=MO,WE")
- "notify": ONLY when custom schedule needed (see rules above). Example: "hour,now" or "never"

Rules:
- If the user implies an early wake reminder, interpret "early" as 07:00 unless explicit.
- If phrase like "morning" and no time: 09:00; "noon": 12:00; "afternoon": 15:00; "evening": 18:00; "night": 21:00.
- If duration implied (e.g. "1h meeting"), compute duration_minutes.
- If ambiguous duration for a reminder (e.g. "remind me ..."), you can set a minimal duration like 5.
- If all-day phrasing (e.g. "whole day", "all day"), set all_day true and omit time.
- If multiple events are described, choose ONLY the single most central one.
- Make reasonable assumptions if data missing; note assumptions in description.
- Never include commentary outside the JSON.

Format discipline:
- Return ONLY the JSON object (no leading/trailing text).
- Keys in lower_snake_case exactly as specified.

Examples:

User prompt: "Team sync in 2 hours"
{
  "title": "Team sync",
  "date": "2025-08-10",
  "time": "15:00",
  "duration_minutes": 30,
  "notify": "hour,now",
  "all_day": false
}

User prompt: "Doctor appointment next Tuesday at 9:30 at Downtown Clinic for annual physical"
{
  "title": "Doctor appointment",
  "date": "2025-08-12",
  "time": "09:30",
  "duration_minutes": 30,
  "location": "Downtown Clinic",
  "description": "Annual physical check-up",
  "all_day": false
}

User prompt: "Log gym session tomorrow evening (no notification)"
{
  "title": "Gym session",
  "date": "2025-08-11",
  "time": "18:00",
  "duration_minutes": 60,
  "description": "Workout (logging only)",
  "notify": "never",
  "all_day": false
}

User prompt: "Company offsite next Friday (all day) at Mountain Lodge"
{
  "title": "Company offsite",
  "date": "2025-08-15",
  "all_day": true,
  "description": "Company offsite at Mountain Lodge",
  "location": "Mountain Lodge",
  "duration_minutes": 60
}

Return ONLY the JSON object.
"""

JSON_OBJECT_RE = re.compile(r'\{.*\}', re.DOTALL)

@dataclass
class AiEventProposal:
    title: str
    date: str
    time: Optional[str]
    duration_minutes: int
    description: Optional[str]
    location: Optional[str]
    all_day: bool
    rrule: Optional[str]
    notify: Optional[str]  # new: custom notify schedule or None

def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}

def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    os.chmod(CONFIG_PATH, 0o600)

def interactive_config() -> None:
    print("Configure cal-ai (OpenAI-compatible API)")
    base_url = input("API base URL (e.g. https://api.openai.com/v1/): ").strip()
    model = input("Model (e.g. gpt-4o-mini): ").strip()
    api_key = input("API key: ").strip()
    if not base_url.endswith("/"):
        base_url += "/"
    cfg = {"base_url": base_url, "model": model, "api_key": api_key}
    save_config(cfg)
    print(f"Saved config to {CONFIG_PATH}")

def read_prompt(args: argparse.Namespace) -> str:
    # If prompt provided as arguments
    if args.prompt:
        return " ".join(args.prompt).strip()
    # If data is piped in (stdin not a TTY), read it once
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data.strip()
        # If nothing was piped, fall through to attempt interactive prompt (unlikely)
    # Interactive prompt (no args and running in a terminal)
    try:
        user_input = input("Enter event description: ").strip()
        if not user_input:
            print("No input provided.", file=sys.stderr)
            sys.exit(1)
        return user_input
    except EOFError:
        print("No prompt provided and no interactive input available.", file=sys.stderr)
        sys.exit(1)

def build_client(cfg: Dict[str, Any]):
    if OpenAI is None and openai_legacy is None:
        print("openai package not installed. pip install openai")
        sys.exit(1)
    missing = [k for k in ("base_url", "model", "api_key") if not cfg.get(k)]
    if missing:
        print("Missing config keys:", ", ".join(missing))
        print("Run: cal-ai --config")
        sys.exit(1)
    if OpenAI is not None:
        return OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
    # Legacy style only if ChatCompletion actually exists
    if hasattr(openai_legacy, "ChatCompletion"):
        base = cfg["base_url"]
        import re as _re
        base = _re.sub(r"/+$", "", base)
        openai_legacy.api_key = cfg["api_key"]  # type: ignore
        openai_legacy.api_base = base  # type: ignore
        return openai_legacy
    print("Unsupported openai package: missing OpenAI client and ChatCompletion.\n"
          "Install a supported version, e.g.:\n"
          "  pip install 'openai>=1.0.0'  (preferred)\n"
          "or downgrade to 0.28.x if you need legacy ChatCompletion.")
    sys.exit(1)

def extract_json(text: str) -> Optional[Dict[str, Any]]:
    # Find first JSON object. Strip code fences if present.
    # Strategy: find first { ... } (greedy) and attempt JSON parse progressively.
    candidates: List[str] = []
    # JSON code fence pattern
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        candidates.append(fence.group(1))
    # Fallback: any object
    m = JSON_OBJECT_RE.search(text)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            return json.loads(c)
        except Exception:
            continue
    return None

def validate_payload(raw: Dict[str, Any]) -> (Optional[AiEventProposal], List[str]):
    errs: List[str] = []
    def err(msg: str):
        errs.append(msg)

    title = str(raw.get("title", "")).strip()
    if not title:
        err("title missing/empty")
    if len(title) > MAX_TITLE_LEN:
        title = title[:MAX_TITLE_LEN].rstrip()
    date = raw.get("date")
    if not isinstance(date, str):
        err("date missing")
    else:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except Exception:
            err(f"date invalid: {date}")

    all_day = bool(raw.get("all_day", False))
    time_val = raw.get("time", None)
    if time_val in ("", "null"):
        time_val = None
    if all_day:
        time_val = None
    if time_val is not None:
        if not isinstance(time_val, str):
            err("time not string")
        else:
            if not re.fullmatch(r"\d{2}:\d{2}", time_val):
                err(f"time invalid format: {time_val}")
            else:
                h, m = map(int, time_val.split(":"))
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    err(f"time out of range: {time_val}")

    duration = raw.get("duration_minutes", 60)
    try:
        duration = int(duration)
        if duration <= 0:
            err("duration_minutes must be > 0")
            duration = 60
    except Exception:
        err("duration_minutes invalid (not int)")
        duration = 60

    description = raw.get("description")
    if description is not None:
        description = str(description).strip()
    location = raw.get("location")
    if location is not None:
        location = str(location).strip()
    rrule = raw.get("rrule")
    if rrule is not None:
        rrule = str(rrule).strip()
        # Light sanity check
        if not re.match(r"^[A-Z0-9=;,-]+$", rrule):
            err(f"rrule suspicious: {rrule}")

    notify_val = raw.get("notify", None)
    notify_norm: Optional[str] = None
    if notify_val is not None:
        if isinstance(notify_val, str):
            nv = notify_val.strip()
            if nv == "":
                notify_val = None
            else:
                try:
                    notify_norm = db.normalize_notify_arg(nv)
                    # Drop if equals default (we treat as not custom)
                    if notify_norm == db.DEFAULT_NOTIFY:
                        notify_norm = None
                except ValueError as e:
                    err(f"notify invalid: {e}")
        else:
            err("notify must be string if provided")

    if errs:
        return None, errs
    prop = AiEventProposal(
        title=title,
        date=date,  # type: ignore
        time=time_val,
        duration_minutes=duration,
        description=description or None,
        location=location or None,
        all_day=all_day,
        rrule=rrule or None,
        notify=notify_norm,
    )
    return prop, errs

def event_from_proposal(p: AiEventProposal) -> Event:
    start_utc = parse_date_time(p.date, None if p.all_day else p.time)
    end_utc = start_utc + timedelta(minutes=p.duration_minutes)
    ev = Event(
        id=None,
        title=p.title,
        description=p.description,
        location=p.location,
        start_utc=start_utc,
        end_utc=end_utc,
        all_day=p.all_day,
        rrule=p.rrule,
        exdates=[],
    )
    return ev

def ask_yes_no(prompt: str) -> bool:
    # Works even if stdin was a pipe (and already consumed) by using /dev/tty.
    if sys.stdin.isatty():
        try:
            resp = input(f"{prompt} [y/N]: ")
        except EOFError:
            return False
        return resp.strip().lower() in ("y", "yes")
    # Attempt to read from controlling terminal
    try:
        with open("/dev/tty", "r") as tty_in, open("/dev/tty", "w") as tty_out:
            tty_out.write(f"{prompt} [y/N]: ")
            tty_out.flush()
            resp = tty_in.readline()
        return resp.strip().lower() in ("y", "yes")
    except Exception:
        # Non-interactive environment; default to No
        return False

def accept_or_modify(prompt: str) -> (str, Optional[str]):
    """
    Returns:
      ("accept", None)  -> user accepted
      ("abort", None)   -> user declined outright
      ("modify", instruction) -> user wants modification
    Rules:
      y / yes => accept
      n / no  => abort (only if no extra instruction text)
      If input starts with 'no'/'n' but has extra text => treat as modify
      Any other non-empty text => modify
      Empty => abort
    """
    resp = ""
    if sys.stdin.isatty():
        try:
            resp = input(f"{prompt} [y/N or enter modification]: ").strip()
        except EOFError:
            return ("abort", None)
    else:
        # Try controlling terminal like previous ask_yes_no
        try:
            with open("/dev/tty", "r") as tty_in, open("/dev/tty", "w") as tty_out:
                tty_out.write(f"{prompt} [y/N or enter modification]: ")
                tty_out.flush()
                resp = tty_in.readline().strip()
        except Exception:
            return ("abort", None)

    lower = resp.lower()
    if lower in ("y", "yes"):
        return ("accept", None)
    if lower in ("n", "no"):
        return ("abort", None)
    if lower.startswith("no ") or lower.startswith("no,") or lower.startswith("n ") or lower.startswith("n,"):
        parts = resp.split(None, 1)
        if len(parts) == 1:
            return ("abort", None)
        return ("modify", parts[1].lstrip(", ").strip() or None)
    if resp:
        return ("modify", resp)
    return ("abort", None)

def modify_proposal(
    client,
    model: str,
    conversation: List[Dict[str, str]],
    current_raw_json: Dict[str, Any],
    instruction: str,
) -> Optional[AiEventProposal]:
    """
    Ask the model to modify the existing JSON according to the user's instruction.
    Returns new AiEventProposal or None on failure.
    """
    # Provide prior JSON explicitly (assistant message already present before call; we just add a user instruction)
    user_msg = (
        "Modify the previous JSON event to satisfy this request:\n"
        f"{instruction}\n"
        "Return ONLY the updated JSON object."
    )
    conversation.append({"role": "user", "content": user_msg})

    for attempt in range(1, RETRY_LIMIT + 1):
        raw_resp = chat_generate(client, model, conversation)
        data = extract_json(raw_resp)
        if not data:
            conversation.append({
                "role": "user",
                "content": "Could not find JSON object. Respond ONLY with corrected JSON."
            })
            continue
        prop, errs = validate_payload(data)
        if errs:
            conversation.append({
                "role": "user",
                "content": "Validation errors: " + "; ".join(errs) + " . Return corrected JSON ONLY."
            })
            continue
        # Append assistant JSON to conversation for potential further modifications
        conversation.append({"role": "assistant", "content": json.dumps(data, indent=2)})
        return prop
    return None

def format_preview(p: AiEventProposal) -> str:
    lines = [
        f"Title: {p.title}",
        f"Date:  {p.date}",
        f"Time:  {(p.time or '(all-day)')}",
        f"Duration: {p.duration_minutes} min",
    ]
    if p.location:
        lines.append(f"Location: {p.location}")
    if p.description:
        lines.append(f"Description: {p.description[:200] + ('...' if len(p.description) > 200 else '')}")
    if p.rrule:
        lines.append(f"RRULE: {p.rrule}")
    if p.notify:
        lines.append(f"Notify schedule: {p.notify}")
    else:
        lines.append("Notify schedule: (default)")
    return "\n".join(lines)

def chat_generate(client, model: str, conversation: List[Dict[str, str]]) -> str:
    # New style (>=1.0)
    if hasattr(client, "chat") and hasattr(client.chat, "completions"):
        resp = client.chat.completions.create(
            model=model,
            messages=conversation,
            temperature=0.2,
        )
        return resp.choices[0].message.content or ""
    # Legacy style
    if hasattr(client, "ChatCompletion"):
        resp = client.ChatCompletion.create(  # type: ignore
            model=model,
            messages=conversation,
            temperature=0.2,
        )
        return resp["choices"][0]["message"]["content"]  # type: ignore
    raise RuntimeError("No supported chat API found on client. Reinstall/upgrade openai package.")

def run_ai(args: argparse.Namespace) -> None:
    cfg = load_config()
    client = build_client(cfg)
    model = cfg["model"]
    user_prompt = read_prompt(args)
    # New timezone/context additions
    now_local = datetime.now().astimezone()
    today_str = now_local.strftime("%Y-%m-%d")
    now_time_str = now_local.strftime("%H:%M")
    offset_raw = now_local.strftime("%z")  # e.g. +0200
    tz_offset = offset_raw[:3] + ":" + offset_raw[3:]
    tz_name = now_local.tzname() or "Local"
    # Replaced str.format (which broke due to literal braces) with manual safe replacements.
    replacements = {
        "today": today_str,
        "now_time": now_time_str,
        "tz_name": tz_name,
        "tz_offset": tz_offset,
        "iso_now": now_local.isoformat(timespec="minutes"),
    }
    system_prompt_filled = SYSTEM_PROMPT
    for k, v in replacements.items():
        system_prompt_filled = system_prompt_filled.replace(f"{{{k}}}", v)
    conversation: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt_filled},
        {"role": "user", "content": user_prompt},
    ]

    proposal: Optional[AiEventProposal] = None
    raw_json_obj: Optional[Dict[str, Any]] = None
    for attempt in range(1, RETRY_LIMIT + 1):
        raw_resp = chat_generate(client, model, conversation)
        data = extract_json(raw_resp)
        if not data:
            conversation.append({"role": "user", "content": "Could not find JSON object. Respond ONLY with JSON per spec."})
            continue
        prop, errs = validate_payload(data)
        if errs:
            conversation.append({
                "role": "user",
                "content": "Validation errors: " + "; ".join(errs) + " . Please return corrected JSON ONLY."
            })
            continue
        proposal = prop
        raw_json_obj = data
        # Record assistant JSON for future modification context
        conversation.append({"role": "assistant", "content": json.dumps(data, indent=2)})
        break

    if not proposal or not raw_json_obj:
        print("Failed to obtain valid event after retries.")
        sys.exit(1)

    modify_round = 0
    while True:
        print("Proposed event:\n" + format_preview(proposal) + "\n")
        action, instr = accept_or_modify("Add this event?")
        if action == "accept":
            break
        if action == "abort":
            print("Aborted.")
            return
        # action == modify
        if modify_round >= MAX_MODIFY_ROUNDS:
            print("Modification limit reached. Aborting.")
            return
        modify_round += 1
        if not instr:
            print("No modification instruction provided; aborting.")
            return
        new_prop = modify_proposal(client, model, conversation, raw_json_obj, instr)
        if not new_prop:
            print("Failed to apply modification; aborting.")
            return
        proposal = new_prop
        # Update raw_json_obj from last assistant JSON (conversation last message)
        try:
            raw_json_obj = json.loads(conversation[-1]["content"])
        except Exception:
            pass
        continue  # loop to display updated preview

    ev = event_from_proposal(proposal)
    new_id = db.add_event(ev)
    # Apply notify from JSON or CLI flag
    applied = None
    if proposal.notify:
        if proposal.notify == "never" or proposal.notify != db.DEFAULT_NOTIFY:
            db.set_event_notify(new_id, proposal.notify)
        applied = proposal.notify
    elif getattr(args, "notify", None) is not None:
        try:
            norm = db.normalize_notify_arg(args.notify)
            if norm == "never" or norm != db.DEFAULT_NOTIFY:
                db.set_event_notify(new_id, norm)
            applied = norm
        except ValueError:
            pass
    if applied:
        print(f"Added event #{new_id}: {proposal.title} (notify={applied})")
    else:
        print(f"Added event #{new_id}: {proposal.title}")

def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cal-ai", description="AI-powered calendar event creator")
    p.add_argument("--config", action="store_true", help="Configure API credentials")
    p.add_argument(
        "--notify",
        help=(
            "Per-event notification thresholds: month,week,day,hour,now,never,default "
            "(same semantics as cal-cli). Example: --notify hour,now"
        ),
        default=None,
    )
    p.add_argument("prompt", nargs="*", help="Natural language prompt (optional if piping stdin)")
    return p

def main(argv: Optional[List[str]] = None) -> None:
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.config:
        interactive_config()
        return
    run_ai(args)

if __name__ == "__main__":
    main()
