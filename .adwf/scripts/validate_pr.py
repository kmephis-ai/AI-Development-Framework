#!/usr/bin/env python3
"""PR contract and base-branch trusted-diff validator."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.issue_contract import parse_pr_contract  # noqa: E402
from lib.trust import classify_git_diff  # noqa: E402




def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--event")
    source.add_argument("--body")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--base-sha")
    parser.add_argument("--head-sha")
    args = parser.parse_args()
    event = json.loads(Path(args.event).read_text(encoding="utf-8")) if args.event else {}
    if not isinstance(event, dict):
        event = {}
    pull_request = event.get("pull_request") or {}
    if not isinstance(pull_request, dict):
        pull_request = {}
    raw_body = args.body if args.body is not None else pull_request.get("body")
    body = raw_body if isinstance(raw_body, str) else ""
    contract = parse_pr_contract(body)
    errors = list(contract["errors"])
    if not isinstance(raw_body, str):
        errors.append("PR_BODY_INVALID")
    base_sha = args.base_sha or (pull_request.get("base") or {}).get("sha")
    head_sha = args.head_sha or (pull_request.get("head") or {}).get("sha")
    try:
        classification = classify_git_diff(args.root, str(base_sha or ""), str(head_sha or ""))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        classification = {"result": "BLOCK", "reason_codes": [f"TRUST_DIFF_UNVERIFIABLE:{type(exc).__name__}"]}
    if classification.get("result") == "BLOCK":
        errors.extend(classification.get("reason_codes", []))
    if classification.get("human_required"):
        if contract.get("risk") != "R4":
            errors.append("TRUST_CHANGE_REQUIRES_R4")
        if not str(contract.get("roadmap_id", "")).startswith("GOV-"):
            errors.append("TRUST_CHANGE_REQUIRES_GOV_ROADMAP")
    if errors:
        print("PR CONTRACT + TRUSTED DIFF: FAIL")
        for error in list(dict.fromkeys(errors)):
            print(f"- {error}")
        return 1
    if classification.get("human_required"):
        gate = "OWNER DECISION REQUIRED"
    elif classification.get("authorization_mode") == "STANDING_OWNER_POLICY":
        gate = "AUTO-AUTHORIZED BY STANDING POLICY"
    else:
        gate = "AUTOMATION ELIGIBLE"
    print(
        "PR CONTRACT + TRUSTED DIFF: PASS; "
        f"Roadmap-ID={contract['roadmap_id']}; Issue=#{contract['issue_number']}; "
        f"Risk={contract['risk']}; Gate={gate}; Diff={classification.get('result')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
