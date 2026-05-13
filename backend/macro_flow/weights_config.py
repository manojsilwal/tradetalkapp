"""Load optional blend weights from resources/prompts/macro_flow_weights.yaml."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_DEFAULT = {"cmf_weight": 1.2, "rs_weight": 0.8, "regime_memory_confidence_min": 0.65}


def get_macro_flow_blend_weights() -> Dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "resources" / "macro_flow_weights.yaml"
    cfg = dict(_DEFAULT)
    if not path.is_file():
        return cfg
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            for k in _DEFAULT:
                if k in raw and raw[k] is not None:
                    try:
                        cfg[k] = float(raw[k])
                    except (TypeError, ValueError):
                        pass
    except Exception as e:
        logger.warning("[macro_flow] weights yaml: %s", e)
    return cfg
