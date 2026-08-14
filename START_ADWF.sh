#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
( sleep 2; python3 -m webbrowser http://127.0.0.1:8765/ >/dev/null 2>&1 || true ) &
exec python3 .adwf/adwf.py dashboard serve --bind 127.0.0.1 --port 8765
