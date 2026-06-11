#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PORT="8080"

usage() {
  cat <<'EOF'
Usage:
  ./stop.sh [--port=PORT] [-h|--help]

Stops the service started by ./start.sh; Python virtualenv is not required for stopping.

Options:
  --port=PORT   Set HTTP port to stop (default: 8080)
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
  esac
done

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [[ "$PORT" -lt 1 || "$PORT" -gt 65535 ]]; then
  echo "[stop] Invalid --port value, fallback to 8080"
  PORT="8080"
fi

echo "[stop] Checking service on port ${PORT}..."
PIDS="$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
if [[ -z "${PIDS}" ]]; then
  echo "[stop] No running service found on port ${PORT}."
  exit 0
fi

echo "[stop] Stopping process(es): ${PIDS}"
kill ${PIDS} 2>/dev/null || true
sleep 1

STILL_PIDS="$(lsof -tiTCP:${PORT} -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${STILL_PIDS}" ]]; then
  echo "[stop] Force stopping: ${STILL_PIDS}"
  kill -9 ${STILL_PIDS} 2>/dev/null || true
fi

echo "[stop] Service on port ${PORT} stopped."

VIDEO_EXPORT_PIDS="$(pgrep -f "server.video_export_runner" 2>/dev/null || true)"
if [[ -n "${VIDEO_EXPORT_PIDS}" ]]; then
  echo "[stop] Stopping video export worker process(es): ${VIDEO_EXPORT_PIDS}"
  kill ${VIDEO_EXPORT_PIDS} 2>/dev/null || true
fi
