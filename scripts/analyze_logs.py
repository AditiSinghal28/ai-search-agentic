"""Analyze real AI search logs and suggest new intent examples.

Run from the project root:
    python scripts/analyze_logs.py --hospital-id 1 --status error

This does not auto-edit intent_catalog.py. It prints grouped candidates so you can
review them before adding clean examples to the catalog/evaluation set.
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict

from app.repositories.analytics_repository import AnalyticsRepository
from app.services.intent_matcher import IntentMatcher


def signature(text: str) -> str:
    matcher = IntentMatcher()
    normalized = matcher.normalize(text)
    normalized = re.sub(r"\b\d+\b", "<num>", normalized)
    normalized = re.sub(r"\b(today|yesterday|this|last|current)\b", "<dateword>", normalized)
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hospital-id", type=int, required=True)
    parser.add_argument("--status", default=None, help="success, error, or omit for all")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    repo = AnalyticsRepository()
    logs = repo.list_recent_logs(hospital_id=args.hospital_id, status=args.status, limit=args.limit)

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in logs:
        groups[signature(row["query_text"])].append(row)

    ranked = sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)
    for sig, rows in ranked[:30]:
        print("\n===", len(rows), "similar queries ===")
        print("signature:", sig)
        for row in rows[:5]:
            print(f"- [{row.get('status')}] intent={row.get('intent')} query={row.get('query_text')}")
            if row.get("error_text"):
                print("  error:", row["error_text"][:250])
        print("suggestion: add one clean example like:")
        print("   ", rows[0]["query_text"])


if __name__ == "__main__":
    main()
