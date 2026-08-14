#!/usr/bin/env python3
"""Run the ratcheted mypy gate. Dependencies are hash-locked separately."""
from __future__ import annotations
from pathlib import Path
import subprocess,sys
ROOT=Path(__file__).resolve().parents[2]
TARGETS=[
 '.adwf/lib/policy.py','.adwf/lib/cost_guard.py','.adwf/lib/assurance.py',
 '.adwf/lib/trusted_context.py','.adwf/lib/evidence_resolver.py',
 '.adwf/lib/provider_contracts.py','.adwf/lib/strict_json.py',
]
def main()->int:
    try:
        import mypy.version  # type: ignore[import-not-found]
    except ImportError:
        print('TYPECHECK CORE: NOT_VERIFIED: mypy dependency not installed'); return 2
    if mypy.version.__version__!='1.11.2':
        print('TYPECHECK CORE: BLOCK: unexpected mypy version'); return 1
    cmd=[sys.executable,'-m','mypy','--config-file',str(ROOT/'.adwf/mypy.ini'),*TARGETS]
    r=subprocess.run(cmd,cwd=ROOT,check=False); print('TYPECHECK CORE:', 'PASS' if r.returncode==0 else 'FAIL'); return r.returncode
if __name__=='__main__': raise SystemExit(main())
