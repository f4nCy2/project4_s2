#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
HOST="${FRONTEND_HOST:-127.0.0.1}"
PORT="${WS_PORT:-8080}"
BASE_URL="http://${HOST}:${PORT}"

OPEN_PAGES=true
if [[ "${1:-}" == "--no-open" ]]; then
  OPEN_PAGES=false
fi

is_server_up() {
  curl -fsS "${BASE_URL}/api/status" >/dev/null 2>&1
}

start_backend_if_needed() {
  if is_server_up; then
    echo "[frontend] Server already running at ${BASE_URL}"
    return
  fi

  echo "[frontend] Starting backend server in background..."
  cd "$PROJECT_ROOT"
  mkdir -p .logs

  # Respect existing env vars from caller (TCP_HOST/TCP_PORT/WS_PORT/etc.).
  nohup "$PROJECT_ROOT/start.sh" > "$PROJECT_ROOT/.logs/start_frontend.log" 2>&1 &
  local pid=$!
  echo "[frontend] start.sh pid=${pid}"

  for i in {1..40}; do
    if is_server_up; then
      echo "[frontend] Server is ready."
      return
    fi
    sleep 0.5
  done

  echo "[frontend] Server did not become ready in time."
  echo "[frontend] Check logs: $PROJECT_ROOT/.logs/start_frontend.log"
  exit 1
}

open_pages() {
  local urls=(
    "${BASE_URL}/control/"
    "${BASE_URL}/scheduler/"
    "${BASE_URL}/files2/"
  )

  if [[ "$OPEN_PAGES" == true ]]; then
    if command -v open >/dev/null 2>&1; then
      for u in "${urls[@]}"; do
        open "$u" >/dev/null 2>&1 || true
      done
      echo "[frontend] Opened pages in browser."
    else
      echo "[frontend] 'open' command not found. Please open manually:"
      printf '  %s\n' "${urls[@]}"
    fi
  else
    echo "[frontend] --no-open set. URLs:"
    printf '  %s\n' "${urls[@]}"
  fi
}

start_backend_if_needed
open_pages

echo "[frontend] Done."
