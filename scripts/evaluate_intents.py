"""Evaluate intent classification against a small hospital-specific query set.

Run from project root:
    python scripts/evaluate_intents.py --file eval/intent_eval_set.json

This checks the planner intent only; it does not execute SQL.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.agent import SearchAgent
from app.schemas import QueryRequest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="eval/intent_eval_set.json")
    parser.add_argument("--hospital-id", type=int, default=1)
    args = parser.parse_args()

    rows = json.loads(Path(args.file).read_text())
    agent = SearchAgent()
    passed = 0

    for row in rows:
        payload = QueryRequest(query=row["query"], hospital_id=args.hospital_id, today=row.get("today"))
        plan = agent._build_plan(payload)  # intentional planner-only evaluation
        ok = plan.intent == row["expected_intent"]
        passed += int(ok)
        mark = "PASS" if ok else "FAIL"
        print(f"{mark}: {row['query']} -> got={plan.intent} expected={row['expected_intent']}")

    total = len(rows)
    print(f"\nAccuracy: {passed}/{total} = {(passed / total * 100):.1f}%")


if __name__ == "__main__":
    main()
