from __future__ import annotations

import os
import time
from typing import Any, Optional

from .schemas import StaleDataReport, StaleSourceRecord


def _tier_l1_threshold_minutes(skill_tier: str) -> int:
    t = (skill_tier or "").strip().upper()
    if t == "DEEP":
        return int(os.environ.get("CHAT_STALE_L1_MAX_MIN_DEEP", "60"))
    if t == "ADVANCED":
        return int(os.environ.get("CHAT_STALE_L1_MAX_MIN_ADVANCED", "120"))
    return int(os.environ.get("CHAT_STALE_L1_MAX_MIN_SIMPLE", "240"))


def evaluate_chat_staleness(
    *,
    cycle_id: str,
    meta: dict[str, Any],
    skill_tier: str,
) -> Optional[StaleDataReport]:
    """
    Chat-first stale gate. Starts with L1 freshness and session freshness.
    Additional sources can be added as richer source timestamps become available.
    """
    affected: list[StaleSourceRecord] = []
    tier = (skill_tier or "").strip().upper()

    if bool(meta.get("stale_session")):
        affected.append(
            StaleSourceRecord(
                source="session",
                as_of=None,
                threshold="active_session",
                signal_type="chat_turn",
            )
        )

    l1_updated_at = meta.get("l1_updated_at")
    now_epoch = time.time()
    max_min = _tier_l1_threshold_minutes(tier)
    if l1_updated_at is None:
        # Advanced and deep flows require explicit freshness metadata.
        if tier in {"ADVANCED", "DEEP"}:
            affected.append(
                StaleSourceRecord(
                    source="l1_snapshot",
                    as_of=None,
                    threshold=f"{max_min} minutes",
                    signal_type="chat_turn",
                )
            )
    else:
        try:
            age_min = max(0.0, (now_epoch - float(l1_updated_at)) / 60.0)
            if age_min > float(max_min):
                affected.append(
                    StaleSourceRecord(
                        source="l1_snapshot",
                        as_of=str(l1_updated_at),
                        threshold=f"{max_min} minutes",
                        signal_type="chat_turn",
                    )
                )
        except (ValueError, TypeError):
            pass

    if not affected:
        return None
    return StaleDataReport(
        cycle_id=cycle_id,
        status="STALE_DATA",
        summoner_executed=False,
        affected_sources=affected,
        message="Assistant synthesis blocked because required evidence is stale.",
    )
