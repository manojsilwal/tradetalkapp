"""
Build a JSON snapshot of the last N days: handoff events, swarm attempts, CORAL stats, claim store.

No LLM calls — suitable for CI, cron, or human review before coding-agent harness proposals (v2).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any


def build_meta_harness_report(since_days: float = 7.0) -> dict[str, Any]:
    since = time.time() - float(since_days) * 86400.0
    from backend import coral_hub

    events = coral_hub.list_handoff_events_since(since)
    attempts = coral_hub.list_attempts_since(since)
    by_type = Counter(e.get("event_type") or "" for e in events)

    claim_info: dict[str, Any] = {}
    try:
        from backend import claim_store

        claim_info = claim_store.stats()
    except Exception as e:
        claim_info = {"error": str(e)}

    return {
        "schema_version": 1,
        "since_days": since_days,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "since_epoch": since,
        "handoff_events": {"count": len(events), "by_type": dict(by_type)},
        "coral_attempts": {"count": len(attempts)},
        "claim_store": claim_info,
        "samples": {
            "last_handoff_event": events[0] if events else None,
            "last_attempt": attempts[0] if attempts else None,
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Meta-harness weekly snapshot (JSON)")
    p.add_argument("--days", type=float, default=7.0)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    rep = build_meta_harness_report(since_days=args.days)
    print(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
