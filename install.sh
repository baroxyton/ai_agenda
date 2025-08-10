#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"
PY="${PY:-python3}"

# Stop running notifier (if active) before upgrading environment
if command -v systemctl >/dev/null 2>&1; then
  if systemctl --user is-active --quiet calendar-notify.service; then
    echo "[+] Stopping running calendar-notify.service"
    systemctl --user stop calendar-notify.service || true
  fi
fi

echo "[+] Creating venv at $VENV"
$PY -m venv "$VENV"
source "$VENV/bin/activate"
pip install --upgrade pip
pip install -r "$ROOT/requirements.txt"

# Create data dirs once
PYAPP="calendar_pyagenda"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$PYAPP"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/$PYAPP"
mkdir -p "$DATA_DIR" "$CACHE_DIR"

# Create runnable shims
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

purge_bytecode() {
  if [ -d "$ROOT/$1" ]; then
    find "$ROOT/$1" -type d -name __pycache__ -exec rm -rf {} + >/dev/null 2>&1 || true
  fi
}

echo "[+] Creating shims in $BIN_DIR"
cat > "$BIN_DIR/cal-cli" <<EOF
#!/usr/bin/env bash
source "$VENV/bin/activate"
export PYTHONPATH="$ROOT:\$PYTHONPATH"
purge() {
  $(
    cat <<'SH'
purge_bytecode() {
  if [ -d "$ROOT/calendar" ]; then
    find "$ROOT/calendar" -type d -name __pycache__ -exec rm -rf {} + >/dev/null 2>&1 || true
  fi
  if [ -d "$ROOT/calendar_pyagenda" ]; then
    find "$ROOT/calendar_pyagenda" -type d -name __pycache__ -exec rm -rf {} + >/dev/null 2>&1 || true
  fi
}
SH
  )
}
ROOT="$ROOT"; purge
exec python -m calendar_pyagenda.cli "\$@"
EOF
chmod +x "$BIN_DIR/cal-cli"

cat > "$BIN_DIR/cal-gui" <<EOF
#!/usr/bin/env bash
source "$VENV/bin/activate"
export PYTHONPATH="$ROOT:\$PYTHONPATH"
ROOT="$ROOT"
if [ -d "\$ROOT/calendar" ]; then
  find "\$ROOT/calendar" -type d -name __pycache__ -exec rm -rf {} + >/dev/null 2>&1 || true
fi
if [ -d "\$ROOT/calendar_pyagenda" ]; then
  find "\$ROOT/calendar_pyagenda" -type d -name __pycache__ -exec rm -rf {} + >/dev/null 2>&1 || true
fi
exec python -m calendar_pyagenda.gui "\$@"
EOF
chmod +x "$BIN_DIR/cal-gui"

cat > "$BIN_DIR/cal-notify" <<EOF
#!/usr/bin/env bash
source "$VENV/bin/activate"
export PYTHONPATH="$ROOT:\$PYTHONPATH"
ROOT="$ROOT"
if [ -d "\$ROOT/calendar" ]; then
  find "\$ROOT/calendar" -type d -name __pycache__ -exec rm -rf {} + >/dev/null 2>&1 || true
fi
if [ -d "\$ROOT/calendar_pyagenda" ]; then
  find "\$ROOT/calendar_pyagenda" -type d -name __pycache__ -exec rm -rf {} + >/dev/null 2>&1 || true
fi
exec python -m calendar_pyagenda.notify_daemon "\$@"
EOF
chmod +x "$BIN_DIR/cal-notify"

# Add AI shim
cat > "$BIN_DIR/cal-ai" <<EOF
#!/usr/bin/env bash
source "$VENV/bin/activate"
export PYTHONPATH="$ROOT:\$PYTHONPATH"
ROOT="$ROOT"
# Purge bytecode (optional, mirrors style of others)
if [ -d "\$ROOT/calendar" ]; then
  find "\$ROOT/calendar" -type d -name __pycache__ -exec rm -rf {} + >/dev/null 2>&1 || true
fi
if [ -d "\$ROOT/calendar_pyagenda" ]; then
  find "\$ROOT/calendar_pyagenda" -type d -name __pycache__ -exec rm -rf {} + >/dev/null 2>&1 || true
fi
# Run module (ai.py resides inside calendar_pyagenda/)
exec python -m calendar_pyagenda.ai "\$@"
EOF
chmod +x "$BIN_DIR/cal-ai"

# systemd user service
SYSD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$SYSD_DIR"
SERVICE_PATH="$SYSD_DIR/calendar-notify.service"

echo "[+] Installing systemd user service at $SERVICE_PATH"
cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Calendar notifier (lightweight)
After=default.target

[Service]
Type=simple
ExecStart=$BIN_DIR/cal-notify
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable calendar-notify.service || true
echo "[+] (Re)starting calendar-notify.service"
# Try restart first (works if already installed), else start
systemctl --user restart calendar-notify.service 2>/dev/null || systemctl --user start calendar-notify.service || true

echo "[+] Done. Use: cal-gui, cal-cli, cal-notify, cal-ai"
