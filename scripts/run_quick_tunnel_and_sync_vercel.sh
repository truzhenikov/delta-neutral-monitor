#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/root/projects/delta-neutral-monitor"
WEBAPP_DIR="$REPO_DIR/webapp"
RUNTIME_DIR="$REPO_DIR/data/runtime"
LOG_FILE="$RUNTIME_DIR/cloudflared-quick.log"
URL_FILE="$RUNTIME_DIR/current-backend-url.txt"
BACKEND_URL_PATTERN='https://[a-z0-9-]+\.trycloudflare\.com'

mkdir -p "$RUNTIME_DIR"
: > "$LOG_FILE"

cleanup() {
  if [[ -n "${CLOUDFLARED_PID:-}" ]] && kill -0 "$CLOUDFLARED_PID" 2>/dev/null; then
    kill "$CLOUDFLARED_PID" 2>/dev/null || true
    wait "$CLOUDFLARED_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

cloudflared tunnel --no-autoupdate --loglevel info --url http://127.0.0.1:8080 >"$LOG_FILE" 2>&1 &
CLOUDFLARED_PID=$!

backend_url=""
for _ in $(seq 1 90); do
  if [[ -f "$LOG_FILE" ]]; then
    backend_url=$(grep -Eo "$BACKEND_URL_PATTERN" "$LOG_FILE" | head -n1 || true)
    if [[ -n "$backend_url" ]]; then
      break
    fi
  fi
  sleep 1
done

if [[ -z "$backend_url" ]]; then
  echo "cloudflared did not publish a quick tunnel URL" >&2
  cat "$LOG_FILE" >&2 || true
  exit 1
fi

previous_url=""
if [[ -f "$URL_FILE" ]]; then
  previous_url=$(cat "$URL_FILE")
fi

# Cloudflare quick tunnel fallback (optional)
# Requires existing Vercel CLI credentials in the environment where the script runs.
# For fully stable production, prefer a named Cloudflare tunnel or an opened public reverse proxy.
if [[ "$backend_url" != "$previous_url" ]]; then
  cd "$WEBAPP_DIR"
  npx --yes vercel env rm MONITOR_API_BASE_URL production --yes || true
  printf '%s\n' "$backend_url" | npx --yes vercel env add MONITOR_API_BASE_URL production
  npx --yes vercel deploy --prod --yes
  printf '%s' "$backend_url" > "$URL_FILE"
fi

wait "$CLOUDFLARED_PID"
