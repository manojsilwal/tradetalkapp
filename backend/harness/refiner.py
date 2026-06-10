"""LLM-backed (or heuristic) refiner proposing harness CRUD edits."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, List, Optional

from .failure_detector import FailureSignature
from .guards.capability_floor import CapabilityFloorChecker
from .state import CRUDOperation, HarnessCRUDEdit, HarnessState, new_id
from .trajectory import TrajectoryEvent

logger = logging.getLogger(__name__)

REFINER_SYSTEM = """
You are the Continual Harness Refiner for a gold trading swarm (XAU/USD).
Respond ONLY with JSON: { "edits": [ { "target", "operation", "target_id", "payload", "rationale", "confidence", "requires_capability" } ] }
"""


class RefinerAgent:
    def __init__(
        self,
        model_client: Any = None,
        capability_floor: Optional[CapabilityFloorChecker] = None,
        *,
        model_tier: str = "pro",
    ) -> None:
        self._client = model_client
        self._floor = capability_floor or CapabilityFloorChecker()
        self._model_tier = model_tier

    async def propose_edits(
        self,
        failure_signatures: List[FailureSignature],
        trajectory_window: List[TrajectoryEvent],
        current_harness_state: HarnessState,
    ) -> tuple[List[HarnessCRUDEdit], List[HarnessCRUDEdit]]:
        proposed = await self._llm_propose(failure_signatures, trajectory_window, current_harness_state)
        if not proposed:
            proposed = self._heuristic_propose(failure_signatures, current_harness_state)

        accepted: List[HarnessCRUDEdit] = []
        deferred: List[HarnessCRUDEdit] = []
        for edit in proposed:
            ok, reason = self._floor.check(edit, self._model_tier)
            if ok:
                accepted.append(edit)
            else:
                deferred.append(
                    edit.model_copy(
                        update={
                            "rationale": f"{edit.rationale} [deferred: {reason}]",
                            "requires_capability": reason,
                        }
                    )
                )
        return accepted, deferred

    async def _llm_propose(
        self,
        failure_signatures: List[FailureSignature],
        trajectory_window: List[TrajectoryEvent],
        state: HarnessState,
    ) -> List[HarnessCRUDEdit]:
        prompt = {
            "signatures": [s.model_dump(mode="json") for s in failure_signatures],
            "trajectory": [e.model_dump(mode="json") for e in trajectory_window[-40:]],
            "harness_version": state.version,
        }

        # Primary route: NVIDIA-backed model client if available.
        if self._client is not None:
            try:
                if hasattr(self._client, "generate"):
                    raw = await self._client.generate("harness_refiner", json.dumps(prompt))
                else:
                    raw = None
                if raw is not None:
                    data = raw if isinstance(raw, dict) else json.loads(str(raw))
                    return self._parse_edits(data.get("edits") or [])
            except Exception as e:
                logger.warning("[Harness] NVIDIA path failed, falling back to Gemini Flash-low: %s", e)

        # Hard fallback route: Google Gemini 3.5 Flash (low)
        try:
            from ..gemini_llm import gemini_simple_completion_sync

            from ..model_defaults import DEFAULT_GEMINI_MODEL

            fallback_model = (
                os.environ.get("HARNESS_GEMINI_LOW_MODEL", "").strip()
                or os.environ.get("GEMINI_MODEL_LIGHT", "").strip()
                or DEFAULT_GEMINI_MODEL
            )
            resp = await asyncio.to_thread(
                gemini_simple_completion_sync,
                REFINER_SYSTEM + "\n" + json.dumps(prompt),
                model=fallback_model,
            )
            text = resp if isinstance(resp, str) else str(resp)
            data = json.loads(text)
            return self._parse_edits(data.get("edits") or [])
        except Exception as e:
            logger.warning("[Harness] Gemini fallback failed: %s", e)
            return []

    def _heuristic_propose(
        self,
        failure_signatures: List[FailureSignature],
        state: HarnessState,
    ) -> List[HarnessCRUDEdit]:
        edits: List[HarnessCRUDEdit] = []
        for sig in failure_signatures:
            if sig.signature_id == "PRICE_HALLUCINATION":
                for aid in sig.affected_agent_ids or ["gold_advisor"]:
                    current = state.system_prompts.get(aid, "")
                    guard = (
                        "You must NEVER state a price, percentage change, or numeric market value "
                        "unless it is sourced directly from a tool call result in this conversation."
                    )
                    if guard.lower() not in current.lower():
                        body = (current + "\n\n" + guard).strip()
                    else:
                        body = current
                    edits.append(
                        HarnessCRUDEdit(
                            target="prompt",
                            operation=CRUDOperation.UPDATE,
                            target_id=aid,
                            payload={"system_prompt": body},
                            rationale=sig.description,
                            confidence=0.75,
                        )
                    )
            elif sig.signature_id == "MEMORY_RETRIEVAL_MISS":
                edits.append(
                    HarnessCRUDEdit(
                        target="memory",
                        operation=CRUDOperation.UPDATE,
                        target_id="harness:retrieval_hint",
                        payload={
                            "value": "Prefer verified tool outputs when RRF scores are below floor."
                        },
                        rationale=sig.description,
                        confidence=0.6,
                    )
                )
        return edits

    def _parse_edits(self, rows: List[Any]) -> List[HarnessCRUDEdit]:
        out: List[HarnessCRUDEdit] = []
        for row in rows:
            try:
                if not isinstance(row, dict):
                    continue
                out.append(
                    HarnessCRUDEdit(
                        edit_id=str(row.get("edit_id") or new_id()),
                        target=row["target"],
                        operation=row["operation"],
                        target_id=str(row.get("target_id") or ""),
                        payload=dict(row.get("payload") or {}),
                        rationale=str(row.get("rationale") or ""),
                        confidence=float(row.get("confidence", 0.5)),
                        requires_capability=str(row.get("requires_capability") or ""),
                    )
                )
            except Exception as e:
                logger.debug("[Harness] skip invalid edit row: %s", e)
        return out
