from __future__ import annotations
import os
from pathlib import Path
from datetime import timedelta

APP_NAME = "calendar_pyagenda"

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))) / APP_NAME
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))) / APP_NAME
LOG_PATH = CACHE_DIR / "notify.log"
DB_PATH = DATA_DIR / "calendar.db"

THRESHOLDS = [
    ("month", timedelta(days=30)),
    ("week", timedelta(days=7)),
    ("day", timedelta(days=1)),
    ("hour", timedelta(hours=1)),
]
