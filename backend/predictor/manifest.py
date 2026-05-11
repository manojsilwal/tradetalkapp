"""Evidence manifest builder for predictor cycles."""

from __future__ import annotations

from typing import Any, Dict, List

from backend.swarm_reliability.schemas import EvidenceArtifact, EvidenceManifest


def build_manifest(
    *,
    cycle_id: str,
    price_artifacts: List[EvidenceArtifact],
    macro_artifacts: List[EvidenceArtifact] | None = None,
) -> EvidenceManifest:
    inputs: Dict[str, List[EvidenceArtifact]] = {"prices": price_artifacts}
    if macro_artifacts:
        inputs["macro"] = macro_artifacts
    return EvidenceManifest(cycle_id=cycle_id, inputs=inputs, agents={})
