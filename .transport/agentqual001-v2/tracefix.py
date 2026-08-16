#!/usr/bin/env python3
from pathlib import Path
import json
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: tracefix.py <candidate-root>")
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / ".adwf"))
from lib.decision_traceability import seal_graph

path = root / ".adwf/decision-requirement-traceability.json"
graph = json.loads(path.read_text(encoding="utf-8"))
edge_id = "EDGE-INTENT-REQ-AGENTQUAL-001"
if any(edge.get("id") == edge_id for edge in graph.get("edges", [])):
    raise SystemExit("AGENTQUAL_INTENT_EDGE_ALREADY_PRESENT")
if not any(item.get("id") == "REQ-AGENTQUAL-001" for item in graph.get("records", [])):
    raise SystemExit("AGENTQUAL_REQUIREMENT_MISSING")
if not any(item.get("id") == "INTENT-FOUNDATION-20260815" for item in graph.get("records", [])):
    raise SystemExit("FOUNDATION_INTENT_MISSING")
graph["edges"].append({
    "id": edge_id,
    "type": "INTENT_TO_REQUIREMENT",
    "from": "INTENT-FOUNDATION-20260815",
    "to": "REQ-AGENTQUAL-001",
    "edge_sha256": ""
})
path.write_text(json.dumps(seal_graph(graph), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print("AGENTQUAL_TRACEFIX: PASS")
