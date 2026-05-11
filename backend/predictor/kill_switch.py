"""Predictor feature flags (mirrors DECISION_LEDGER_ENABLE pattern)."""

from __future__ import annotations

import os


def predictor_enabled() -> bool:
    if (os.environ.get("PREDICTOR_ENABLE", "1") or "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False
    backend = (os.environ.get("PREDICTOR_BACKEND", "") or "").strip().lower()
    return backend not in ("none", "off", "disabled")


def predictor_baselines_only() -> bool:
    return (os.environ.get("PREDICTOR_BACKEND", "") or "").strip().lower() == "baselines_only"
