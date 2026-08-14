#!/usr/bin/env bash
set -euo pipefail
# Requires GitHub CLI authenticated with label and Project permissions.
# Usage: ADWF_PROJECT_OWNER=<user-or-org> ADWF_PROJECT_NUMBER=<n> [ADWF_PROJECT_APPLY=true] ./setup_project.sh
: "${ADWF_PROJECT_OWNER:?set ADWF_PROJECT_OWNER}"
: "${ADWF_PROJECT_NUMBER:?set ADWF_PROJECT_NUMBER}"
command -v gh >/dev/null || { echo "gh CLI required" >&2; exit 2; }
echo "Project bootstrap target: ${ADWF_PROJECT_OWNER} #${ADWF_PROJECT_NUMBER}"
apply_args=()
if [[ "${ADWF_PROJECT_APPLY:-false}" == "true" ]]; then
  apply_args=(--apply)
fi
python3 .adwf/scripts/sync_labels.py "${apply_args[@]}"
python3 .adwf/scripts/project_bootstrap.py --owner "$ADWF_PROJECT_OWNER" --number "$ADWF_PROJECT_NUMBER" "${apply_args[@]}"
echo "Четыре owner views сверяются с .adwf/project-layout.json; GitHub не даёт стабильного write API для views."
