#!/usr/bin/env python3
"""Validate one external/thin consumer binding. This command never writes."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.external_consumer_binding import (  # noqa: E402
    BINDING_REL,
    ExternalConsumerBindingError,
    load_binding,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--consumer-root", required=True)
    parser.add_argument("--framework-root", default=str(ROOT))
    parser.add_argument("--binding")
    args = parser.parse_args()
    binding = args.binding or str(Path(args.consumer_root) / BINDING_REL)
    try:
        _, result = load_binding(
            args.consumer_root,
            args.framework_root,
            binding_path=binding,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ExternalConsumerBindingError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "status": "BLOCK",
            "reason": str(exc),
            "runtime_evidence": "NOT_VERIFIED",
            "mutation_authority": "NONE_BINDING_IS_PROOF_ONLY",
        }, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
