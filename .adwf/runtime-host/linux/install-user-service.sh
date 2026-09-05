#!/usr/bin/env bash
set -euo pipefail
umask 077

REPO_ROOT="${1:-$(pwd)}"
PYTHON_BIN="${ADWF_PYTHON:-python3}"
TOKEN_FILE="${ADWF_GITHUB_TOKEN_FILE:-$HOME/.config/adwf/github.token}"
UNIT_SOURCE="$REPO_ROOT/.adwf/runtime-host/linux/adwf-execution-node.service.in"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/adwf-execution-node.service"
STATE_DIR="$HOME/.local/state/adwf"
LOG_FILE="$STATE_DIR/execnode-host-install-$(date -u +%Y%m%dT%H%M%SZ).log"
mkdir -p "$STATE_DIR" "$UNIT_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1
trap 'rc=$?; echo "STATUS=$([ "$rc" -eq 0 ] && echo PASS || echo FAIL) EXIT_CODE=$rc"; echo "LOG_FILE=$LOG_FILE"; exit "$rc"' EXIT

echo "ADWF Execution Node user-service install"
REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"
test -f "$REPO_ROOT/AGENTS.md"
test -f "$REPO_ROOT/.adwf/scripts/run_execution_node_host.py"
test -f "$UNIT_SOURCE"
command -v git >/dev/null
command -v systemctl >/dev/null
command -v "$PYTHON_BIN" >/dev/null

VERSION="$($PYTHON_BIN -c 'import platform; print(platform.python_version())')"
test "$VERSION" = "3.12.10" || { echo "ERROR: Python 3.12.10 required, got $VERSION"; exit 20; }
HEAD="$(git -C "$REPO_ROOT" rev-parse HEAD)"
test "${#HEAD}" -eq 40 || { echo "ERROR: invalid Git HEAD"; exit 21; }

test -f "$TOKEN_FILE" || { echo "ERROR: private token file missing at $TOKEN_FILE"; exit 22; }
MODE="$(stat -c '%a' "$TOKEN_FILE")"
case "$MODE" in 600|400) ;; *) echo "ERROR: token file mode must be 600 or 400, got $MODE"; exit 23;; esac

escape_sed() { printf '%s' "$1" | sed 's/[&|]/\\&/g'; }
sed -e "s|@REPO_ROOT@|$(escape_sed "$REPO_ROOT")|g" \
    -e "s|@PYTHON@|$(escape_sed "$(command -v "$PYTHON_BIN")")|g" \
    -e "s|@TOKEN_FILE@|$(escape_sed "$TOKEN_FILE")|g" \
    "$UNIT_SOURCE" > "$UNIT_FILE.tmp"
mv "$UNIT_FILE.tmp" "$UNIT_FILE"
chmod 600 "$UNIT_FILE"

systemctl --user daemon-reload
systemctl --user enable --now adwf-execution-node.service
systemctl --user is-enabled adwf-execution-node.service
systemctl --user is-active adwf-execution-node.service

echo "REPO_HEAD=$HEAD"
echo "SERVICE=adwf-execution-node.service"
if command -v loginctl >/dev/null; then
  LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
  echo "USER_LINGER=${LINGER:-UNKNOWN}"
  if [ "$LINGER" != "yes" ]; then
    echo "NOTICE: user linger is not enabled; boot-without-login is not yet verified."
  fi
fi
