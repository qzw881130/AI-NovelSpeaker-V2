#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PORT="8080"
HOST="0.0.0.0"
LOG_DIR="$ROOT_DIR/logs"
LOG_FILE="$LOG_DIR/server.log"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

usage() {
  cat <<'EOF'
Usage:
  ./start.sh [--port=PORT] [--host=HOST] [-h|--help]

Options:
  --port=PORT   Set HTTP port (default: 8080)
  --host=HOST   Set bind host (default: 0.0.0.0)
  -h, --help    Show this help message and exit
EOF
}

for arg in "$@"; do
  case "$arg" in
    -h|--help)
      usage
      exit 0
      ;;
    --port=*)
      PORT="${arg#--port=}"
      ;;
    --host=*)
      HOST="${arg#--host=}"
      ;;
  esac
done

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [[ "$PORT" -lt 1 || "$PORT" -gt 65535 ]]; then
  echo "[start] Invalid --port value, fallback to 8080"
  PORT="8080"
fi

if [[ -z "$HOST" ]]; then
  echo "[start] Invalid --host value, fallback to 0.0.0.0"
  HOST="0.0.0.0"
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "[start] Virtual environment not found, creating .venv..."
  python3 -m venv "$VENV_DIR"
fi

echo "[start] Installing Python dependencies into .venv..."
"$PYTHON_BIN" -m pip install --upgrade pip >/dev/null
"$PYTHON_BIN" -m pip install -r requirements.txt

echo "[start] Checking old service on port ${PORT}..."
OLD_PIDS="$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${OLD_PIDS}" ]]; then
  echo "[start] Stopping old process(es): ${OLD_PIDS}"
  kill ${OLD_PIDS} 2>/dev/null || true
  sleep 1
  STILL_PIDS="$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${STILL_PIDS}" ]]; then
    echo "[start] Force stopping: ${STILL_PIDS}"
    kill -9 ${STILL_PIDS} 2>/dev/null || true
  fi
else
  echo "[start] No old process found."
fi

if [[ ! -f "data/novels.db" ]]; then
  echo "[start] Database not found, initializing..."
  "$PYTHON_BIN" scripts/init_storage.py
fi

mkdir -p "$LOG_DIR"

echo "[start] Accessible URLs:"
echo "  - Local: http://127.0.0.1:${PORT}/index.html"

LAN_PRINTED=0
if command -v ipconfig >/dev/null 2>&1; then
  LAN1="$(ipconfig getifaddr en0 2>/dev/null || true)"
  LAN2="$(ipconfig getifaddr en1 2>/dev/null || true)"
  if [[ -n "${LAN1}" ]]; then
    echo "  - LAN  : http://${LAN1}:${PORT}/index.html"
    LAN_PRINTED=1
  fi
  if [[ -n "${LAN2}" && "${LAN2}" != "${LAN1}" ]]; then
    echo "  - LAN  : http://${LAN2}:${PORT}/index.html"
    LAN_PRINTED=1
  fi
fi

if [[ "${LAN_PRINTED}" -eq 0 ]] && command -v hostname >/dev/null 2>&1; then
  for ip in $(hostname -I 2>/dev/null || true); do
    if [[ "$ip" != "127.0.0.1" && "$ip" != "::1" ]]; then
      echo "  - LAN  : http://${ip}:${PORT}/index.html"
      LAN_PRINTED=1
    fi
  done
fi

if [[ "${LAN_PRINTED}" -eq 0 ]]; then
  echo "  - LAN  : (not detected automatically)"
fi

echo "[start] Starting server..."
echo "[start] Bind host: ${HOST}"
echo "[start] Log file: ${LOG_FILE}"
NOVELSPEAKER_HOST="$HOST" NOVELSPEAKER_PORT="$PORT" nohup "$PYTHON_BIN" app_server.py >> "$LOG_FILE" 2>&1 &
echo "[start] Server started in background (PID: $!)"
