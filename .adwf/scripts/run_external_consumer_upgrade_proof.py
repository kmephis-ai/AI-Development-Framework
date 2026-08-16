#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import argparse, json, sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.external_consumer_upgrade_proof import ExternalConsumerUpgradeProofError, run_external_consumer_upgrade_proof  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Run exact external-consumer ADWF A→B→A→B proof in disposable workspace")
    for name in ("consumer-root","consumer-repository","consumer-sha","consumer-tree","source-root","source-sha","source-tree","target-root","target-sha","target-tree","provider-run-id"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--product-name", default="PrihRashOnline-v2")
    p.add_argument("--default-branch", default="main")
    p.add_argument("--repository-visibility", default="PUBLIC")
    args = p.parse_args()
    try:
        report = run_external_consumer_upgrade_proof(
            args.consumer_root, args.source_root, args.target_root,
            consumer_repository=args.consumer_repository,
            consumer_sha=args.consumer_sha, consumer_tree=args.consumer_tree,
            source_sha=args.source_sha, source_tree=args.source_tree,
            target_sha=args.target_sha, target_tree=args.target_tree,
            product_name=args.product_name, default_branch=args.default_branch,
            repository_visibility=args.repository_visibility,
            provider_run_id=args.provider_run_id,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except ExternalConsumerUpgradeProofError as exc:
        print(json.dumps({"status":"BLOCK","reason":str(exc),"write_back_performed":False}, ensure_ascii=False, sort_keys=True))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
