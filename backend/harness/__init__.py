"""Continual harness refinement loop — mid-session observe/refine without resets.

Also hosts the model-agnostic inference harness (Super Investor phases):

* ``backend_protocol``  — ``VerdictBackend`` / ``ForecastBackend`` protocols +
  adapters for the LLM client, the TimesFM service, and the baseline ensemble.
* ``replay_service``    — named-candidate replay runs persisted to the ledger
  DB + the champion/challenger promotion gate.
* ``model_backtest``    — deterministic walk-forward backtest of the numeric
  forecaster over data-lake history (model-as-strategy vs equal-weight hold).

Those modules are imported lazily by their consumers (``routers/harness.py``,
``decision_ledger_registry.py``) and are intentionally NOT re-exported here so
this package's import cost stays unchanged.
"""

from .config import HarnessConfig, harness_config_from_env
from .loop import ContinualHarnessLoop, get_session_loop
from .state import HarnessCRUDEdit, HarnessState, RefinementCycle

__all__ = [
    "ContinualHarnessLoop",
    "HarnessConfig",
    "HarnessCRUDEdit",
    "HarnessState",
    "RefinementCycle",
    "get_session_loop",
    "harness_config_from_env",
]
