#!/usr/bin/env python3
"""Executable documentation contracts for ADWF v1.6.

Every fenced example must be explicitly classified as run / parse / skip(reason).
Safe run examples execute as argv without shell; JSON parse examples use duplicate-key rejection.
"""
from __future__ import annotations
from pathlib import Path
import re,shlex,subprocess,sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.adwf'))
from lib.strict_json import loads as strict_loads
LINK=re.compile(r'\[[^\]]+\]\(([^)]+)\)')
MARK=re.compile(r'^<!--\s*adwf-doc:\s*(run|parse|skip(?:\(reason=([^)]+)\))?)\s*-->$')
CORE={'README.md','INSTALL.md','SECURITY.md','SPECIFICATION.md','ADWS.md'}
ALLOWED_RUN_PREFIXES=(('python','.adwf/adwf.py'),('python','.adwf/scripts/validate_pipeline_ir.py'),('python','.adwf/scripts/validate_ci.py'))

def docs()->list[Path]:
    values=[*ROOT.glob('*.md'),*ROOT.glob('docs/**/*.md'),*ROOT.glob('.adwf/**/*.md')]
    return sorted({p.resolve() for p in values if p.is_file() and '.adwf-runtime' not in p.parts})

def fence_contracts(path:Path)->list[str]:
    lines=path.read_text(encoding='utf-8').splitlines(); errors=[]; inside=False; marker=None; lang=''; body=[]
    for i,line in enumerate(lines):
        if line.lstrip().startswith('```'):
            if not inside:
                prev=lines[i-1].strip() if i else ''; m=MARK.fullmatch(prev)
                if not m: errors.append(f'{path.relative_to(ROOT)}:{i+1}:FENCE_CLASSIFICATION_MISSING'); marker=None
                else:
                    marker=m.group(1)
                    if marker.startswith('skip') and not m.group(2): errors.append(f'{path.relative_to(ROOT)}:{i}:SKIP_REASON_MISSING')
                lang=line.strip()[3:].strip().lower(); body=[]; inside=True
            else:
                content='\n'.join(body).strip()
                if marker=='parse':
                    if lang!='json': errors.append(f'{path.relative_to(ROOT)}:{i+1}:PARSE_LANGUAGE_UNSUPPORTED:{lang or "none"}')
                    else:
                        try: strict_loads(content)
                        except Exception as exc: errors.append(f'{path.relative_to(ROOT)}:{i+1}:JSON_EXAMPLE_INVALID:{type(exc).__name__}')
                elif marker=='run':
                    for raw in [x.strip() for x in body if x.strip() and not x.lstrip().startswith('#')]:
                        if any(ch in raw for ch in '|;&><`$'):
                            errors.append(f'{path.relative_to(ROOT)}:{i+1}:RUN_METACHAR_FORBIDDEN'); continue
                        try: argv=shlex.split(raw)
                        except ValueError: errors.append(f'{path.relative_to(ROOT)}:{i+1}:RUN_ARGV_INVALID'); continue
                        if tuple(argv[:2]) not in ALLOWED_RUN_PREFIXES:
                            errors.append(f'{path.relative_to(ROOT)}:{i+1}:RUN_COMMAND_NOT_ALLOWLISTED:{" ".join(argv[:2])}'); continue
                        r=subprocess.run(argv,cwd=ROOT,capture_output=True,text=True,timeout=30,check=False)
                        if r.returncode: errors.append(f'{path.relative_to(ROOT)}:{i+1}:RUN_COMMAND_FAILED:{r.returncode}')
                inside=False; marker=None; lang=''; body=[]
        elif inside: body.append(line)
    if inside: errors.append(f'{path.relative_to(ROOT)}:UNCLOSED_FENCE')
    return errors

def main()->int:
    errors=[]
    for path in docs():
        rel=path.relative_to(ROOT); text=path.read_text(encoding='utf-8')
        version=(ROOT/'VERSION').read_text(encoding='utf-8').strip(); major_minor='.'.join(version.split('.')[:2])
        if str(rel) in CORE and f'v{major_minor}' not in text and version not in text: errors.append(f'{rel}:VERSION_NOT_CURRENT')
        for target in LINK.findall(text):
            clean=target.split('#',1)[0]
            if not clean or '://' in clean or clean.startswith(('mailto:','#')): continue
            if not (path.parent/clean).resolve().exists(): errors.append(f'{rel}:BROKEN_LINK:{clean}')
        errors.extend(fence_contracts(path))
    install=(ROOT/'INSTALL.md').read_text(encoding='utf-8')
    if re.search(r'(обязат|требу)[^\n]{0,80}self-hosted',install,re.I): errors.append('INSTALL:MANDATORY_SELF_HOSTED_STALE')
    if 'FREE_PUBLIC_GITHUB' not in install: errors.append('INSTALL:PUBLIC_PROFILE_MISSING')
    quick=ROOT/'docs/QUICKSTART_V1_6.md'
    if not quick.is_file(): errors.append('QUICKSTART_EXECUTABLE_MISSING')
    if errors:
        print('DOC CONTRACTS: FAIL'); [print('-',e) for e in errors]; return 1
    print('DOC CONTRACTS: PASS'); return 0
if __name__=='__main__': raise SystemExit(main())
