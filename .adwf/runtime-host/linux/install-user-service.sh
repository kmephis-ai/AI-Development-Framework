#!/usr/bin/env bash
set -euo pipefail
umask 077

REPO_ROOT="${1:-$(pwd)}"
ACTION="${2:-install}"
PYTHON_BIN="${ADWF_PYTHON:-python3}"
TOKEN_FILE="${ADWF_GITHUB_TOKEN_FILE:-$HOME/.config/adwf/github.token}"
UNIT_SOURCE="$REPO_ROOT/.adwf/runtime-host/linux/adwf-execution-node.service.in"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/adwf-execution-node.service"
STATE_DIR="$HOME/.local/state/adwf"
LOG_FILE="$STATE_DIR/execnode-host-${ACTION}-$(date -u +%Y%m%dT%H%M%SZ).log"
SERVICE="adwf-execution-node.service"

case "$ACTION" in
  install|verify|disable) ;;
  *) echo "ERROR: action must be install, verify, or disable" >&2; exit 2 ;;
esac

mkdir -p "$STATE_DIR" "$UNIT_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1
trap 'rc=$?; echo "STATUS=$([ "$rc" -eq 0 ] && echo PASS || echo FAIL) EXIT_CODE=$rc"; echo "LOG_FILE=$LOG_FILE"; exit "$rc"' EXIT

echo "ADWF Execution Node user-service action=$ACTION"
REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"
test -f "$REPO_ROOT/AGENTS.md"
command -v git >/dev/null
command -v systemctl >/dev/null
HEAD="$(git -C "$REPO_ROOT" rev-parse HEAD)"
test "${#HEAD}" -eq 40 || { echo "ERROR: invalid Git HEAD"; exit 21; }
echo "REPO_HEAD=$HEAD"
echo "SERVICE=$SERVICE"

verify_service() {
  test -f "$UNIT_FILE" || { echo "ERROR: user service unit missing at $UNIT_FILE"; return 30; }
  systemctl --user is-enabled "$SERVICE"
  systemctl --user is-active "$SERVICE"
  echo "UNIT_FILE=$UNIT_FILE"
  if command -v loginctl >/dev/null; then
    LINGER="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
    echo "USER_LINGER=${LINGER:-UNKNOWN}"
    if [ "$LINGER" != "yes" ]; then
      echo "NOTICE: user linger is not enabled; boot-without-login is not yet verified."
    fi
  fi
}

if [ "$ACTION" = "disable" ]; then
  systemctl --user disable --now "$SERVICE"
  if systemctl --user is-active --quiet "$SERVICE"; then
    echo "ERROR: service still active after disable"; exit 31
  fi
  if systemctl --user is-enabled --quiet "$SERVICE"; then
    echo "ERROR: service still enabled after disable"; exit 32
  fi
  echo "SERVICE_ACTIVE=inactive"
  echo "SERVICE_ENABLED=disabled"
  echo "NOTICE: token, repository, logs, and unit file were preserved."
  exit 0
fi

if [ "$ACTION" = "verify" ]; then
  verify_service
  exit 0
fi

test -f "$REPO_ROOT/.adwf/scripts/run_execution_node_host.py"
test -f "$UNIT_SOURCE"
command -v "$PYTHON_BIN" >/dev/null
VERSION="$($PYTHON_BIN -c 'import platform; print(platform.python_version())')"
test "$VERSION" = "3.12.10" || { echo "ERROR: Python 3.12.10 required, got $VERSION"; exit 20; }

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
systemctl --user enable --now "$SERVICE"
verify_service
