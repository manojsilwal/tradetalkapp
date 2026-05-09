from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .schemas import EvidenceArtifact, EvidenceManifest, stable_json_hash


def _base_dir() -> Optional[Path]:
    raw = (os.environ.get("SWARM_RUN_ARTIFACTS_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def write_chat_cycle_artifacts(
    *,
    cycle_id: str,
    meta: dict[str, Any],
    evidence: dict[str, Any],
    tool_trace: list[dict[str, Any]],
    stale_data_report: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    base = _base_dir()
    if base is None:
        return None
    run_dir = base / "runs" / cycle_id
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "agent_outputs").mkdir(parents=True, exist_ok=True)
    (run_dir / "reviewer_outputs").mkdir(parents=True, exist_ok=True)

    manifest = EvidenceManifest(cycle_id=cycle_id)
    refs = list((evidence or {}).get("rag_chunk_refs") or [])
    rag_rows = [
        EvidenceArtifact(
            artifact_id=str(r.get("chunk_id") or f"rag-{idx}"),
            source=str(r.get("collection") or "rag"),
            as_of=None,
            hash=None,
            metadata={
                "rank": int(r.get("rank", 0)),
                "distance": float(r.get("distance", 1.0)),
                "ticker": str(r.get("ticker") or ""),
            },
        )
        for idx, r in enumerate(refs)
    ]
    tool_rows = [
        EvidenceArtifact(
            artifact_id=f"tool-{idx}-{str(t.get('name') or 'unknown')}",
            source="chat_tool_trace",
            as_of=None,
            hash=stable_json_hash(t),
            metadata={"name": str(t.get("name") or ""), "outcome": str(t.get("outcome") or "")},
        )
        for idx, t in enumerate(tool_trace or [])
    ]
    manifest.inputs = {
        "rag": rag_rows,
        "tools": tool_rows,
    }

    manifest_path = run_dir / "evidence_manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        f.write(manifest.model_dump_json(indent=2))

    if stale_data_report:
        with (run_dir / "stale_data_report.json").open("w", encoding="utf-8") as f:
            json.dump(stale_data_report, f, indent=2, sort_keys=True, default=str)

    with (run_dir / "cycle_config.json").open("w", encoding="utf-8") as f:
        json.dump(
            {
                "cycle_id": cycle_id,
                "session_id": str(meta.get("session_id") or ""),
                "l1_updated_at": meta.get("l1_updated_at"),
                "stale_session": bool(meta.get("stale_session")),
            },
            f,
            indent=2,
            sort_keys=True,
            default=str,
        )

    with (run_dir / "final_signal.json").open("w", encoding="utf-8") as f:
        json.dump({"status": evidence.get("status") or "OK", "evidence_contract": evidence}, f, indent=2, default=str)
    return str(run_dir)

