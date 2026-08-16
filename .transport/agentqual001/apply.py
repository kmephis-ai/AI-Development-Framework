#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import base64, copy, hashlib, json, re, sys, zlib

if len(sys.argv) != 2:
    raise SystemExit("usage: apply.py <candidate-root>")
root = Path(sys.argv[1]).resolve()
if not (root / ".git").exists():
    # git worktree .git is a file, not directory
    if not (root / ".git").is_file():
        raise SystemExit("candidate is not a git worktree")

PAYLOADS = {".adwf/lib/creative_agent_qualification.py": "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", ".adwf/schemas/creative-agent-adapters.schema.json": "eNqdV8tu2zAQ/BVC55zYK2z5UARpUGDAh64FBFq0QOxVokBKXASB/33XUk7tQ5vmB1Ka4Uw8O7Q8wMwreui0VuDKXeNbV9hRdb5A47dK6eSrh0ZF4VXh6aJ3V+HFHGUJt3OwN7Kb8vzYRnA37KKaSFD4mzkHCswUUtGIrSDZewbncOy4s+yOcOcHg1P+cMfYcGuar96lu1LUdl3T9eNwDVwY91ChIBDHAPhNy0XGpRe2W9aJIyZopnSqB9iSkMwjHGqM7qd5LI1Rpxzq6kt7hVCSZp8T3xJGVdOyXPl+vR7NxF6QVrfiGIHLSPjVLbuGbGF+7PP6Wy7sH1D8gcTCGrhbe+GzO8Ghi0O5BcTMgNvDxM2U5iJ2Dkm+NL9Oxx+ZzMbkQ09deFz+Vbrwg5+1pJaRzdfR6enZeF5XLAefMl1AdOk4ynS1L5a7zxfvtKIMLBK7NvuCVQsGfQg86UPutD68igYn/OPf+AhuFEfMr55GdEAExUCOnSvVhu7Hvf1Uv6uoWBUkLOvR9UywJWyk0quA2VYva3TattHlQG9w01UAYX8HkWmvrlUCJLsiPm34Qo7ImMd9W0vC8E1KaXMmjdoCQwd53iieCMeMNMaMSIW79YaJL/o+WXbGRZ0pAT/1L3qcG5gZM4v4Ikf00HlR+hALYRVD4EpJA3YXpTrHqpQo1IYOOmgFy88rAIvGUnPCqkMfszM0WYv6oWWaA+W44AvUyhDxGl2i9w9ziY2Z7c5qUic/1EcKZ9GvJ/2Ymv+dALov/rYWisUkGD8BHsu8KNw3HtCBreKFmBaYTq5+bVOO1GcEpVFZzJ8mhJ3kVdBeBsnNAHYVrERFD2VSjtduT7IQkLoxBsxzGiHegZOje3eVSsiZJKfIeFHJBmrTDwjIjUrA+CxxOFjd8qbTzlvL9SHaK+5WdOwEpYEipU5K0aX3svjDHJnCm/iCuOtOq6k8r12XgOyh+Q4y/0vPoZZkZn3h8Qe49cTrIpUxHuXCjHtpgsZ7YiMg9Xd6jzv1p1yv20Q3clcJOcfej1TgqoBnXYwgvZUcRfyQro3kMtSBulGsBlPfHWReMKib/tS4Po9fuCuImulMk7BaZRyKncj9yP+6IUrnf9hyCdRcIDdQs69YPAq+fvK1ji2aVB7TKFsnzBpR2kKp4jx8Qb4yWAPwP42GkF9g8jtCg/Ga8D9a1TdWFVxWGFoGe3ghC56knFaZYDm+bCoVzyfU7E+UuNH7/xr98Lr+naAkVfi5LYU19adpbh+vJ2wXvL/IJfC6cfjHf0DSUPRybr3QGFwhXO4nP9dgPwgLbJm+Jk5f98s/D79CH9BUK27Bg==", ".adwf/schemas/creative-agent-qualification-report.schema.json": "eNqtVktu4zAM/ZWCz7nsRjkHLJAiRYq2YwMUamM5ImhJUcfIf59UlhN7nGqAvBQpihRPOMp5Pe2laB80IbMNwsXVXNiQ1BBMX04b3VJc9pOl/dqtGrUtM6/BEqt4zx4PZwNw41TdVW3FTtzjfPGAnQd1kdDhiOAAmhtF01pToaU4hn9SgbxB87xyXFn3Rjh/lOCuQN06zZKwG1L/2vWofw2P7MPKEmkyKhDdzczSfGPsr1YM4hWKNNMnISfaVsG5pTpxDHTQWO1EGNrNXRBvCAmP+cwRn0fE9k0qxtkSz19OuVvaFjr6YUeSQ3raYyTFd0Bhs6rKFtqnWjlvLAtwYxIoGwkOnAkgnvg/qxrAUyRlszPc+Po6obLhBH5Pz+FNMbsqPIL8AJHKeF9l5dPjMyXmssayNhHiZU+SwBUXEpU65Pc8Ui/LArIiFFQaC/LVo9/3UG6djCFHoKwzriGZBvbZoyB0IDQ2i1VuDcKMZlQeS4eB7DbfQH0HzYEgTAjpLcLjohaQuab2CIj74y0c2LwwRXN0iW5rjELuDiSjqlSe9epNYtxUIatdMF9PLKrmaEP/aubAjZkIZqKBG3zdE9BkCEHOdLuXaYw3hTiJW6I/A2/qQvRAAijySn/m25IEVAArLTdqYPYvo7NsldJ/LMn1mLBAglYNBgGGIvvtVrFkbiV5WZT2w2VjvraD0jeOCurEhdOdE+AmEfDtjJTw7mYkFCk5mJIp9dn54ia6XuTuFHssKrFoMleRiVccwOJLL2rqKROFp2zoLJt3Xk1fdG3PRDo7gwQT9tcLWATwT1VSQtKtq5MOBdTc/FNiZh/jo9n/YLLc19m7+6cvp1eD08MfpxODz/gqZvSt01DSU1eCn7B+8v8gl8Lpx+IN/QtFQ5HRzt7QgOLyDMfq7HvlBWWDMt/fE+N88/Hz0EfwD0LkHbg==", ".adwf/scripts/qualify_creative_agent.py": "eNp9UctOwzAQvOcrtlILJOHBBRIpQnwUhI4oEReYxnaM40SHtaMq/47T0pVw4cJ6dmd2bCU/Y0JClK+ZN59SsFyogWdCRxaRMZPAEGziHBRzKMK5Ec6fSK7nXccgQs17CU7rNDF3Bqr4jEjKm/wE17FU0aJiLws6LnObpCbCr7uLHYOuBVDEjeLX0KwbAJbPiYoLQt50hsMfoCfTAUgXDMC0v+D9DYsEI7kJVdx/mCnDsbgEz5weWKALbuFLzGPfa4VlLKdeGktWdvfpZIJQKhz8y8c25rboX1UPFVP2rKPnwVv3ZSS5CWoAvZMlQfaY2t3/m5AMFNwo/eRtKHNKJh2o3VN/WXJiVX+nZe3xzXIGpfPknflBh+5u/yxpZkkNiY6gHwJMqp6bTAt5p7NnFx3oevNwpfkf8pvfB5eH8WLVyYOOT/ABa1iqs=", ".adwf/scripts/reference_agent_adapter.py": "eNqtWMtu20gQvfcrVM3aKsYDJHFi2XBs2TYGxEDiBkEaKgxSM5hdkMGNXTXtv2+pshUrSlT6dEJhgvZ0VfXVq7tVxkbwS86VoKmj5gcVRRMCX6jEIi1e9rsOtk3OLa4cMeRpdaKUfF4tXcbeGqwyp4mEyROyba1DzTHxjcZsHL4c1vWHT+It5CrZz/Od1jvbgmT2i/rvPhGzb+S0elwo1OzciQFP/Tpmxu3Nai0CfnGmsqIrrwdxTiBQGIJor/aNPwjfq2XD2w0gx6wte81sepcgXS3wE7p8RzdP714p37zfhaNjHiZX7/4vL2dffx8FV2xH7JBXZRA8RYx40rf5V8deHCZWYQjNRS2OprSl2s3ieHuFaVe1lA/8+9h7/4Jr1jyqDrXPmiKPQ7WVfHJDSKGPXuRoqaegZEYliXdaFKBLvlcGOORlt3/LPet0C2BuONraBdIwcF+23DbK/iAgJkZMoCJ7ot6vDhJ4yFYYoIsT1C4xLvUdzsfzEhns1juxRv0d7q21GXWFHBcRmvldIxytkSi0T0wMu51Ck5ydEfrNfJUvsYkbiiadekz0Q9KvYDNj1Pf0OzWHBkVDlf0PiN+lZyYtAZkppPscEx7gDrPypaxpVwUJB/tHrWoijiaPs8qiTUwInQfOfqcg9oiFrOlEb8Xl9UX1qUcoLWLU+doabTaYiQYDEswoPSJvsbUwmnDVtFF0A5HYrXOrXiRPAFJsbi7ZrZp3/Neqgzr8wV5DqGnoxvMnsPVNpNiPDZU0Mf77PwHNvhoAvs1e4Pu33NdsvOnBkGEhQSzwGiDtKAOs0qm6QV61udCoHzuGW8pLt+XMtIuygBMgtBWDY1COuLMX/HFbBtFKHlgTZ0H2T4lqwKi8jrRdQC+aUJYIHoBHGJ/lGY/AKnyN/WS9CAQVkud02kiZHWWO2c6rXKdknc0LcWPZOuZaQijIgVlAC9saMnVNXSGzp5MH+ujUQu7RBOPCr4UfGZnwc5gFzb6+7kHhN05boL0zvD/T4k6A4tu4iUnR4o7M41moAaXOYu5feIzpKLV2TwsmvqI49Dy+z5JuScX1JAro/YDYCja6QSyB10J6wMWZoEhQOHUWI+mK6FGk2dhnCCvDoDNE9LlhR2z4W5WZ5iV0VjqAT2o2PVefziSwETON2vf+Zv/UOBFGU6gvu+yCsPW41iF1RTAvSeLtqIrgLuYnPWk6v+cWiy1MR8mvPzTAr+G+chTPcmf5x/f3X0GBfBv9XT7/Rjb1j5Jpc4t6SPhf7R+0v8xv0xWn6gnxB32AVxcUb90JiiWgmZnwsVuBKClX+0m4mQ4YV4OR6eb3//5yO/PtE/6BQ=", ".adwf/tests/test_creative_agent_qualification.py": "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", "docs/governance/CREATIVE_AGENT_QUALIFICATION.md": "eNp1VEtv2zAMvvdX6JixUwK5ehkCRYiibYVuHXYIiVmLmihiJEc2K/3vI7dlOzCFhCT6+OgjuTYbiTOX7z/CyCuRc3GZvq9EAXJoj6p3lJFHQkRgd3f1iwrH6+3ZtKXiFF7jOme5Mok3hHfLXw8hcjsfmJwTY0lbfnusbfuxUjc9cPCY0H48BCWNzsIFLvn8poNKa4NjeCpdm30+AsQTyurBxXeQGEYDTZIISjMIumA/n6hZPmo8O0I42Sf8yHVkjsTdOi/FasUYfoc5d0K/VhcSrTzfWD88bw6P72skYPUAVUUqe18Jn0H/GIdLFQNRWb4tMbk5PnuU5JzWxD/9l4j6OmneWcIliOlBPqKPI/gEUhM72Zb6CR/SHyTlTF85i+HrLqe/eMsUlGUDlDEEhvlh4m74Rqx1QjQFzk92PFDXxqJKNErKiIcKR/7RNq4nHEs8XT4arKprYEtFoXB81DP6tdJWeTnCPD9eF4CXNbMlDW0yFUxgm9OBggRHgcGaHskekInXPjZpRHc28MIjEfanommgIkDgU4I4Qbd+9rVEuhkBwzyPRdPW1z6SQboCQOh7BSOaY4/2ySxeOnG29kZrprXBE/3DsdxC4NZpTnIeTUm7hO0MZZL0WnrTQ7Zu0a7eDfRrXhXyjJ4BrZmqpXOOxNRaJD6rOoGv+EyGEWxXSxu+ku61BzfwoVgSUTv6gmUJcqeruqKO/DQPFDEnksQseDrGTzf/VzTIvSk8szvXV2d5dIf9+XUldpjqDqVSm6h4yvfHo6msltW/Xy8/76sTwYXET0tMykLQM81o+RGdh7h+ZQSXEAtBNfDoRSKA/UxDI16le9XV3N/2k0S/vRVrDMXi6VeKNKeBY9zeEi5PQC9CFvkPyNfGwzxlIWAsxfKninN34LHldwFoKPBaQcQkiuo+vE0LiOejpCXLpRLKcgxFtJQrCbqwLDtAkGWuf7oJgYrcyVKbX9ZEYemMvBV0jjaKdXmDCytZhKNB3VBxZtu6W1QxjDy1rQWHCEja8ldKeZGiNQuWm9Q2aSY5Y5rK2BV3QnM1DXg17b12yw6elYIeFi6YnLpCU52muuLY5FB+ZvzDDyvbLTlOyq0Q+1DtsT5Ry9uUDV+L3cDF2d3dfGhyXb3fJfdRiWJSIb8OoG8hOEbsWaMRe8og0F5QPRSvvdKlUvZAOFgpxIWaUDHbJ5ai+RwhBRtyNMqlnHC/qYEHe8k7cqq5pFWvNFzhbkFBPHi4VFixr9v4w2WhrE8pXbsiXS8JPcsWdTcRSOB+gGyflmtQo7GN0mIjxokLOQPZINfTRMTO/lNPWMESoiQXfaOqqjq+IL9CQ/Bk4LUgXuYmCUUSw+Li7OKDJRQh2DugZYuwbxh+Rh4LAh73IhQWqrSMlXawZSiN+nFpDZOpQWIUHE3esrPSbtUgJ1nrhbRGNpXoeVkRo1rYcxPW/K+XhXH06lmNaMaHNk/tUTSwiE0xu2wJYq2Y6p+E/kt8fgLfN4ZLXShDZwrho0cK/bTHJ1Nmr3wLfaZzg6sgmMF5lR7M7QNfbWMkzZ65xCEx/iDlE5MVtEu0YiHPe97KDsV/i4XD4prKyvuHmAQHWSCMAcbFyLgZ7hP3pv8cmKUIGt+tIqN/iFVxuJM1cRI5M81yLAOC0qreDOIomNStokmaGB9JbYKv9WQLOxwJKjM9oOf6CvbV1kfO6xeGnaJs82J8j9O1pHfzPZjzc7nuL91mYSusCOoWwv7or/BrXmecXugLcW9tCBJ6X+ZHrwNCHXbIn2+OSlh5uQ9KL4jO20W54hPK/Lm9h3/u3jfjwQNRylTGtjT4KTlwBWoOHy1jffkLY0W9uZCaTzIAT4tn0rSdMwbtouYp5cU0M+uyMiYQUOk47BfSAWBGrpaMPHpSfcofzA5iWn9Jv/4Z8ZjD8o+R/4v/tsPLO+j/CEfsiz4=", ".adwf/schemas/creative-agent-qualification-report.schema.json": "eNqtVktu4zAM/ZWCz7nsRjkHLJAiRYq2YwMUamM5ImhJUcfIf59UlhN7nGqAvBQpihRPOMp5Pe2laB80IbMNwsXVXNiQ1BBMX04b3VJc9pOl/dqtGrUtM6/BEqt4zx4PZwNw41TdVW3FTtzjfPGAnQd1kdDhiOAAmhtF01pToaU4hn9SgbxB87xyXFn3Rjh/lOCuQN06zZKwG1L/2vWofw2P7MPKEmkyKhDdzczSfGPsr1YM4hWKNNMnISfaVsG5pTpxDHTQWO1EGNrNXRBvCAmP+cwRn0fE9k0qxtkSz19OuVvaFjr6YUeSQ3raYyTFd0Bhs6rKFtqnWjlvLAtwYxIoGwkOnAkgnvg/qxrAUyRlszPc+Po6obLhBH5Pz+FNMbsqPIL8AJHKeF9l5dPjMyXmssayNhHiZU+SwBUXEpU65Pc8Ui/LArIiFFQaC/LVo9/3UG6djCFHoKwzriGZBvbZoyB0IDQ2i1VuDcKMZlQeS4eB7DbfQH0HzYEgTAjpLcLjohaQuab2CIj74y0c2LwwRXN0iW5rjELuDiSjqlSe9epNYtxUIatdMF9PLKrmaEP/aubAjZkIZqKBG3zdE9BkCEHOdLuXaYw3hTiJW6I/A2/qQvRAAijySn/m25IEVAArLTdqYPYvo7NsldJ/LMn1mLBAglYNBgGGIvvtVrFkbiV5WZT2w2VjvraD0jeOCurEhdOdE+AmEfDtjJTw7mYkFCk5mJIp9dn54ia6XuTuFHssKrFoMleRiVccwOJLL2rqKROFp2zoLJt3Xk1fdG3PRDo7gwQT9tcLWATwT1VSQtKtq5MOBdTc/FNiZh/jo9n/YLLc19m7+6cvp1eD08MfpxODz/gqZvSt01DSU1eCn7B+8v8gl8Lpx+IN/QtFQ5HRzt7QgOLyDMfq7HvlBWWDMt/fE+N88/Hz0EfwD0LkHbg=="}
for rel, encoded in PAYLOADS.items():
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zlib.decompress(base64.b64decode(encoded)))

# Patch canonical command executor from raw env command authority to qualified adapter authority.
path = root / ".adwf/lib/action_executors.py"
text = path.read_text(encoding="utf-8")
old_import = "import json,os,re,shlex,subprocess,sys"
if old_import not in text:
    raise SystemExit("ACTION_EXECUTOR_IMPORT_BASE_MISMATCH")
text = text.replace(old_import, "import json,os,re,subprocess,sys", 1)
old_ai = "from .ai_work_contracts import canonicalize_low_trust_claim\n"
new_ai = old_ai + "from .creative_agent_qualification import command_argv,load_qualified_command_adapter,sanitized_agent_environment,verify_local_command_result\n"
if text.count(old_ai) != 1:
    raise SystemExit("ACTION_EXECUTOR_AI_IMPORT_BASE_MISMATCH")
text = text.replace(old_ai, new_ai, 1)
replacement = "def _run_agent_command(root:Path,state:dict[str,Any],key:str,envelope:dict[str,Any])->dict[str,Any]|None:\n    raw_command=os.environ.get('ADWF_AGENT_COMMAND','').strip()\n    adapter_id=os.environ.get('ADWF_AGENT_ADAPTER_ID','').strip()\n    if not raw_command and not adapter_id:return None\n    if raw_command and not adapter_id:return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_UNQUALIFIED'])\n    if raw_command and adapter_id:return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_OVERRIDE_FORBIDDEN'])\n    try:\n        adapter=load_qualified_command_adapter(root,adapter_id,state['phase'])\n    except (OSError,ValueError,json.JSONDecodeError) as exc:\n        return _result(state,key,'FAIL',reason_codes=['AGENT_ADAPTER_UNQUALIFIED'],metadata={'contract_error':str(exc)[:300]})\n    if adapter.get('kind')=='REFERENCE_DETERMINISTIC' and os.environ.get('ADWF_ALLOW_REFERENCE_AGENT')!='1':\n        return _result(state,key,'FAIL',reason_codes=['REFERENCE_AGENT_RUNTIME_FORBIDDEN'])\n    package=envelope.get('work_package')\n    if not isinstance(package,dict):return _result(state,key,'FAIL',reason_codes=['AI_WORK_PACKAGE_MISSING'])\n    if envelope.get('work_package_digest') not in {None,package.get('package_digest')}:\n        return _result(state,key,'FAIL',reason_codes=['AI_WORK_PACKAGE_DIGEST_MISMATCH'])\n    request=root/'.adwf-runtime/supervisor/requests'/f'{key}.json';result=root/'.adwf-runtime/supervisor/results'/f'{key}.json'\n    try:\n        argv=command_argv(root,adapter)\n        env=sanitized_agent_environment(os.environ,request=request,result=result,state=state,adapter=adapter)\n        proc=subprocess.run(argv,cwd=root,env=env,text=True,capture_output=True,check=False,timeout=int(adapter['timeout_seconds']))\n    except subprocess.TimeoutExpired:\n        return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_TIMEOUT'])\n    except (OSError,ValueError) as exc:\n        return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_START_FAILED'],metadata={'contract_error':str(exc)[:300]})\n    if proc.returncode:return _result(state,key,'FAIL',reason_codes=['AGENT_COMMAND_FAILED'],metadata={'exit_code':proc.returncode,'stderr_tail':proc.stderr[-500:]})\n    if not result.is_file():return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_MISSING'])\n    try:value=strict_loads(result.read_text(encoding='utf-8'))\n    except (OSError,ValueError,json.JSONDecodeError) as exc:\n        return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_INVALID'],metadata={'contract_error':str(exc)[:300]})\n    if not isinstance(value,dict):return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_INVALID'])\n    try:work_result=canonicalize_low_trust_claim(value,package=package)\n    except ValueError as exc:return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_CONTRACT_INVALID'],metadata={'contract_error':str(exc)[:300]})\n    local_errors=verify_local_command_result(root,package,work_result)\n    if local_errors:return _result(state,key,'FAIL',reason_codes=['AGENT_RESULT_LOCAL_BINDING_INVALID'],metadata={'binding_errors':local_errors})\n    return {'phase':state['phase'],'outcome':work_result['outcome'],'idempotency_key':key,'subject_sha':work_result.get('head_sha'),\n            'preview_digest':state.get('preview_digest'),'evidence_refs':[],'reason_codes':work_result['reason_codes'],\n            'transient':work_result['outcome']=='RETRY','cost_usd':0,'metadata':{'source':'LOW_TRUST_AGENT_COMMAND','adapter_id':adapter['id'],'adapter_version':adapter['version'],'ai_work_result':work_result}}\n"
pattern = re.compile(r"def _run_agent_command\(.*?\n\ndef creative_executor", re.S)
matches = list(pattern.finditer(text))
if len(matches) != 1:
    raise SystemExit("ACTION_EXECUTOR_FUNCTION_BASE_MISMATCH")
text = text[:matches[0].start()] + replacement + "\n\ndef creative_executor" + text[matches[0].end():]
path.write_text(text, encoding="utf-8", newline="\n")

# Extend AI Work Contracts human-facing truth.
ai_doc = root / "docs/governance/AI_WORK_CONTRACTS.md"
ai_text = ai_doc.read_text(encoding="utf-8")
marker = "## Qualified Creative Agent invocation boundary"
if marker not in ai_text:
    ai_text = ai_text.rstrip() + """\n\n## Qualified Creative Agent invocation boundary\n\n`AGENTQUAL-001` не повышает trust creative output. Command executor принимается только через versioned Creative Agent qualification registry/report; raw `ADWF_AGENT_COMMAND` без qualified adapter блокируется. Qualified command получает secret-filtered environment и exact `AIWorkPackage`, а возвращаемый `AIWorkResult` остаётся `LOW_TRUST` до downstream trusted/provider verification. `reference-local` является deterministic offline qualification fixture, а не внешним AI/provider evidence.\n"""
    ai_doc.write_text(ai_text, encoding="utf-8", newline="\n")

# Load newly materialized qualification module and create its canonical registry/report.
sys.path.insert(0, str(root / ".adwf"))
from lib.creative_agent_qualification import (
    PROFILE_ID, PROFILE_VERSION, qualification_profile_digest,
    reference_qualification_report, seal_registry,
)
profile_digest = qualification_profile_digest()
registry_raw = {
    "$schema": ".adwf/schemas/creative-agent-adapters.schema.json",
    "schema_version": 1,
    "qualification_profile": {"id": PROFILE_ID, "version": PROFILE_VERSION, "digest": profile_digest},
    "adapters": [
        {
            "id": "reference-local",
            "version": "1.0.0",
            "kind": "REFERENCE_DETERMINISTIC",
            "invocation_mode": "COMMAND",
            "supported_phases": ["EXECUTE", "RECOVERY"],
            "command": {"runner": "PYTHON", "path": ".adwf/scripts/reference_agent_adapter.py"},
            "authority": {"network": "NONE", "secrets": "FORBIDDEN", "filesystem": "PACKAGE_SCOPED"},
            "monetary_budget_usd": 0,
            "timeout_seconds": 60,
            "result_channel": "ADWF_ACTION_RESULT_JSON",
            "package_schema": ".adwf/schemas/ai-work-package.schema.json",
            "result_schema": ".adwf/schemas/ai-work-result.schema.json",
            "qualification_report": ".adwf/creative-agent-qualification.json",
            "qualification_profile_id": PROFILE_ID,
            "qualification_profile_version": PROFILE_VERSION,
            "qualification_profile_digest": profile_digest,
        }
    ],
}
registry = seal_registry(registry_raw)
(root / ".adwf/creative-agent-adapters.json").write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
report = reference_qualification_report(registry["adapters"][0])
(root / ".adwf/creative-agent-qualification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

# Roadmap: one rolling-wave unit after EDGEREF-001.
roadmap_path = root / ".adwf/roadmap.json"
roadmap = json.loads(roadmap_path.read_text(encoding="utf-8"))
tasks = next(goal["tasks"] for goal in roadmap["goals"] if goal["id"] == "FOUNDATION-ENGINEERING-OS")
if any(item.get("roadmap_id") == "AGENTQUAL-001" for item in tasks):
    raise SystemExit("ROADMAP_ALREADY_HAS_AGENTQUAL")
if tasks[-1].get("roadmap_id") != "EDGEREF-001":
    raise SystemExit("ROADMAP_TAIL_DRIFT")
tasks.append({
    "roadmap_id": "AGENTQUAL-001",
    "title_ru": "Replaceable Creative Agent Qualification Contract + Reference Adapter v1",
    "dependencies": ["EDGEREF-001"],
    "product_impact": False,
})
roadmap_path.write_text(json.dumps(roadmap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

# Capability Truth stays conservative: synthetic reference qualification is not external-agent live evidence.
cap_path = root / ".adwf/capability-traceability.json"
cap = json.loads(cap_path.read_text(encoding="utf-8"))
if any(item.get("id") == "CREATIVE_AGENT_QUALIFICATION" for item in cap["capabilities"]):
    raise SystemExit("CAPABILITY_ALREADY_HAS_AGENTQUAL")
cap["capabilities"].append({
    "id": "CREATIVE_AGENT_QUALIFICATION",
    "status": "LIVE_NOT_VERIFIED",
    "execution_mode": "OPTIONAL_ADAPTER",
    "owner_claim_ru": "ADWF принимает заменяемый Creative Agent command adapter только через строгую versioned qualification declaration/report; invocation authority ограничена exact work package, а creative result остаётся low-trust до trusted/provider verification.",
    "entrypoints": [".adwf/scripts/qualify_creative_agent.py"],
    "production_paths": [
        ".adwf/lib/creative_agent_qualification.py",
        ".adwf/creative-agent-adapters.json",
        ".adwf/creative-agent-qualification.json",
        ".adwf/schemas/creative-agent-adapters.schema.json",
        ".adwf/schemas/creative-agent-qualification-report.schema.json",
        ".adwf/lib/action_executors.py",
    ],
    "verification": [
        ".adwf/tests/test_creative_agent_qualification.py",
        ".adwf/tests/test_ai_work_contracts.py",
    ],
    "live_boundary": "Synthetic reference-local qualification доказывает fail-closed invocation contract, exact package/result binding и zero-cost offline boundary. LIVE_VERIFIED требует реальный внешний creative adapter/agent result, привязанный к exact AIWorkPackage, плюс downstream trusted/provider exact-SHA evidence; deterministic reference adapter не является AI/provider/live proof.",
    "live_evidence": [],
})
cap_path.write_text(json.dumps(cap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

# Append-only durable traceability, sealed by the existing canonical primitive.
trace_path = root / ".adwf/decision-requirement-traceability.json"
trace = json.loads(trace_path.read_text(encoding="utf-8"))
if trace.get("revision") != 18:
    raise SystemExit("TRACE_REVISION_DRIFT")
if any(item.get("id") == "REQ-AGENTQUAL-001" for item in trace["records"]):
    raise SystemExit("TRACE_ALREADY_HAS_AGENTQUAL")
trace["revision"] = 19
trace["records"].extend([
    {
        "id": "REQ-AGENTQUAL-001",
        "kind": "REQUIREMENT",
        "version": 1,
        "status": "ACTIVE",
        "title_ru": "Creative Agent invocation должна иметь квалифицированный versioned boundary",
        "statement_ru": "Raw replaceable creative executor не получает authority только из env command: adapter обязан декларировать phase/invocation/cost/network/secrets/filesystem/result-channel semantics и быть exact-package/result bound; synthetic qualification не становится trusted или live creative evidence.",
        "source_path": None,
        "source_sha256": None,
        "record_sha256": "",
    },
    {
        "id": "DEC-AGENTQUAL-001",
        "kind": "DECISION",
        "version": 1,
        "status": "ACCEPTED",
        "title_ru": "Квалификация ограничивает invocation authority, но не повышает trust creative output",
        "statement_ru": "Добавить provider-neutral versioned adapter registry + qualification report; mandatory reference adapter остаётся local/offline/zero-cost/secret-filtered/package-scoped. Low-trust AIWorkResult по-прежнему требует существующую trusted/provider verification, а GitHub Agent Inbox остаётся low-trust channel.",
        "source_path": None,
        "source_sha256": None,
        "record_sha256": "",
    },
])
trace["capability_refs"].append({
    "id": "CAPREF-CREATIVE-AGENT-QUALIFICATION",
    "capability_id": "CREATIVE_AGENT_QUALIFICATION",
    "ref_sha256": "",
})
trace["work_unit_refs"].append({
    "id": "WORKREF-AGENTQUAL-001",
    "roadmap_id": "AGENTQUAL-001",
    "issue_number": 99,
    "ai_work_package_id": None,
    "ref_sha256": "",
})
trace["edges"].extend([
    {"id": "EDGE-REQ-DEC-AGENTQUAL-001", "type": "REQUIREMENT_TO_DECISION", "from": "REQ-AGENTQUAL-001", "to": "DEC-AGENTQUAL-001", "edge_sha256": ""},
    {"id": "EDGE-DEC-CAP-AGENTQUAL-001", "type": "DECISION_TO_CAPABILITY", "from": "DEC-AGENTQUAL-001", "to": "CAPREF-CREATIVE-AGENT-QUALIFICATION", "edge_sha256": ""},
    {"id": "EDGE-CAP-WORK-AGENTQUAL-001", "type": "CAPABILITY_TO_WORK", "from": "CAPREF-CREATIVE-AGENT-QUALIFICATION", "to": "WORKREF-AGENTQUAL-001", "edge_sha256": ""},
])
from lib.decision_traceability import seal_graph
trace = seal_graph(trace)
trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

# Register the new governance contract for deterministic source-digest freshness.
docs_path = root / ".adwf/docs-registry.json"
docs = json.loads(docs_path.read_text(encoding="utf-8"))
if any(item.get("path") == "docs/governance/CREATIVE_AGENT_QUALIFICATION.md" for item in docs["documents"]):
    raise SystemExit("DOCS_ALREADY_HAS_AGENTQUAL")
docs["documents"].append({
    "path": "docs/governance/CREATIVE_AGENT_QUALIFICATION.md",
    "watched": [
        ".adwf/lib/creative_agent_qualification.py",
        ".adwf/lib/action_executors.py",
        ".adwf/creative-agent-adapters.json",
        ".adwf/creative-agent-qualification.json",
        ".adwf/schemas/creative-agent-adapters.schema.json",
        ".adwf/schemas/creative-agent-qualification-report.schema.json",
        ".adwf/scripts/reference_agent_adapter.py",
        ".adwf/scripts/qualify_creative_agent.py",
        ".adwf/tests/test_creative_agent_qualification.py",
        ".adwf/roadmap.json",
        ".adwf/capability-traceability.json",
        ".adwf/decision-requirement-traceability.json",
    ],
    "mode": "governance-contract",
    "source_digest": "0" * 64,
    "reviewed_at": "2026-08-16T20:30:00Z",
    "valid_until": "2026-11-16T20:30:00Z",
})
docs_path.write_text(json.dumps(docs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print("AGENTQUAL_APPLY: PASS")
