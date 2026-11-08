#!/bin/sh
set -eu

npm --prefix /app/uis/website run dev -- --host "$UI_HOST" --port "$WEBSITE_PORT" --strictPort &
website_pid=$!
npm --prefix /app/uis/backoffice run dev -- --host "$UI_HOST" --port "$BACKOFFICE_PORT" --strictPort &
backoffice_pid=$!

trap 'kill "$website_pid" "$backoffice_pid" 2>/dev/null || true' EXIT
trap 'exit 0' INT TERM
set +e
wait -n
status=$?
exit "$status"