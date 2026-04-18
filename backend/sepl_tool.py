"""
SEPL for TOOL resources — Phase C1 PR 2 (Autogenesis §3.2 applied to TOOL kind).

Parallel to ``backend/sepl.py`` which evolves PROMPT resources, this module
evolves the NUMERIC CONFIG of registered tier-0 TOOL resources. The critical
safety differences from prompt evolution:

  * Improve is NOT an LLM call. It's a deterministic numeric random walk
    within parameter ranges declared in the tool's YAML. This eliminates
    prompt-injection and unbounded-drift risks.
  * Evaluate is a pure shadow-mode scorer over offline JSON fixtures — never
    touches live traffic and never calls an LLM.
  * Commit writes through ``backend/tool_configs.py::update_tool_config``
    which (a) respects ``learnable=False`` pinning and (b) validates that no
    unknown keys can appear in the new config.
  * All operators are side-effect-free except Commit, which is the only path
    to mutate registry state, and only fires when ``SEPL_TOOL_ENABLE=1``,
    not ``SEPL_TOOL_DRY_RUN``, the candidate wins by ``SEPL_TOOL_MIN_MARGIN``,
    and the 24-hour rate limit is not exhausted.

Feature flags (all default OFF / dry):
    SEPL_TOOL_ENABLE              0 — master switch
    SEPL_TOOL_DRY_RUN             1 — stop before Commit even when enabled
    SEPL_TOOL_MIN_MARGIN          0.05 — candidate must beat active by >=5 pp
    SEPL_TOOL_MAX_PER_DAY         2 — hard rate limit per tool per 24h
    SEPL_TOOL_MAX_PERTURB_STEPS   4 — Improve never moves more than N*step per call
    SEPL_TOOL_CANDIDATES_PER_CYCLE 4 — Improve proposes up to N candidates
    SEPL_TOOL_SEED                — optional int for deterministic tests
"""
from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from .resource_registry import (
    ResourceKind,
    ResourceNotFoundError,
    ResourcePinnedError,
    ResourceRecord,
    get_resource_registry,
)
from .tool_configs import update_tool_config
from .tool_handlers import TOOL_HANDLERS

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_TOOL_FIXTURES_DIR = BACKEND_DIR / "resources" / "sepl_eval_fixtures_tools"


# ── Feature flags / tunables ─────────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name, "") or "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.environ.get(name, "") or "").strip() or default)
    except ValueError:
        return default


def tool_sepl_enabled() -> bool:
    return (os.environ.get("SEPL_TOOL_ENABLE", "0").strip() or "0") == "1"


def tool_sepl_dry_run() -> bool:
    return (os.environ.get("SEPL_TOOL_DRY_RUN", "1").strip() or "1") == "1"


def tool_sepl_min_margin() -> float:
    return max(0.0, min(1.0, _env_float("SEPL_TOOL_MIN_MARGIN", 0.05)))


def tool_sepl_max_per_day() -> int:
    return max(1, _env_int("SEPL_TOOL_MAX_PER_DAY", 2))


# Tier-aware budget gate (Phase C2). Higher-tier tools are allowed strictly
# FEWER commits per 24h than tier-0. Absence of a tier-specific env falls
# back to the global ``SEPL_TOOL_MAX_PER_DAY``.
_TIER_DEFAULT_CAPS: Dict[int, int] = {
    # tier 0: pure, internal, zero cost → relaxed
    0: 2,
    # tier 1: external read, free but observable → 1/day
    1: 1,
    # tier 2+: writes / cost — never evolvable by SEPL today. We still
    # expose a cap so the gate is defined; Commit also blocks them via the
    # learnable=False flag that tier-2+ YAMLs will ship with.
    2: 0,
    3: 0,
}


def tool_sepl_max_per_day_for_tier(tier: int) -> int:
    """Effective per-day cap for a tool of the given tier.

    Env overrides (each an int, ≥0): ``SEPL_TOOL_MAX_PER_DAY_TIER_<N>``.
    Unknown tiers are treated as tier 3 (most restrictive). A value of 0
    blocks ALL SEPL commits for that tier regardless of other knobs.
    """
    try:
        t = int(tier)
    except (TypeError, ValueError):
        t = 3
    env_key = f"SEPL_TOOL_MAX_PER_DAY_TIER_{t}"
    raw = os.environ.get(env_key)
    if raw is not None and raw.strip():
        try:
            return max(0, int(raw.strip()))
        except ValueError:
            pass
    return _TIER_DEFAULT_CAPS.get(t, 0)


def tool_sepl_max_perturb_steps() -> int:
    return max(1, _env_int("SEPL_TOOL_MAX_PERTURB_STEPS", 4))


def tool_sepl_candidates_per_cycle() -> int:
    return max(1, _env_int("SEPL_TOOL_CANDIDATES_PER_CYCLE", 4))


# ── Kill-switch tunables ─────────────────────────────────────────────────────


def tool_sepl_autocommit() -> bool:
    """When 0 (default), the kill switch reports but never calls ``restore``.
    When 1, it actively rolls back regressions. Same gate semantics as the
    prompt-side ``SEPL_AUTOCOMMIT``.
    """
    return (os.environ.get("SEPL_TOOL_AUTOCOMMIT", "0").strip() or "0") == "1"


def tool_sepl_rollback_margin() -> float:
    """Prior config must beat committed by this margin (fraction 0..1) on the
    fixture set to trigger a rollback. Default 0.05 = 5 pp."""
    return max(0.0, min(1.0, _env_float("SEPL_TOOL_ROLLBACK_MARGIN", 0.05)))


def tool_sepl_rollback_window_hours() -> int:
    """Only inspect SEPL commits made within the last N hours."""
    return max(1, _env_int("SEPL_TOOL_ROLLBACK_WINDOW_HOURS", 168))  # 7 days


# ── Domain types ─────────────────────────────────────────────────────────────


class ToolSEPLOutcome(str, Enum):
    COMMITTED = "committed"
    REJECTED_LOW_MARGIN = "rejected_low_margin"
    REJECTED_UNCHANGED = "rejected_unchanged"
    REJECTED_RATE_LIMIT = "rejected_rate_limit"
    ABORTED_PINNED = "aborted_pinned"
    ABORTED_NO_RANGES = "aborted_no_ranges"
    ABORTED_NO_FIXTURES = "aborted_no_fixtures"
    ABORTED_NO_HANDLER = "aborted_no_handler"
    ABORTED_NOT_FOUND = "aborted_not_found"
    ABORTED_DISABLED = "aborted_disabled"
    DRY_RUN = "dry_run"


class ToolRollbackOutcome(str, Enum):
    ROLLED_BACK = "rolled_back"
    DRY_RUN = "dry_run"
    OK_WITHIN_TOLERANCE = "ok_within_tolerance"
    NO_RECENT_SEPL_COMMIT = "no_recent_sepl_commit"
    NO_PRIOR_VERSION_AVAILABLE = "no_prior_version_available"
    NO_FIXTURES = "no_fixtures"
    NO_HANDLER = "no_handler"
    ERROR = "error"


@dataclass(frozen=True)
class ToolEvalResult:
    tool_name: str
    active_version: str
    active_score: float             # 0..1, weighted hit-rate of active config
    candidate_score: float
    margin: float                   # candidate_score - active_score
    fixtures_used: int
    active_hits: int
    candidate_hits: int
    per_fixture: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ToolCandidate:
    config: Dict[str, float]
    rationale: str


@dataclass(frozen=True)
class ToolRollbackReport:
    run_id: str
    tool_name: str
    outcome: ToolRollbackOutcome
    committed_version: Optional[str]
    prior_version: Optional[str]
    committed_score: Optional[float]
    prior_score: Optional[float]
    delta: Optional[float]       # committed - prior (negative => regression)
    margin: float
    fixtures_used: int
    restored_to_version: Optional[str]
    dry_run: bool
    timestamp: float


@dataclass(frozen=True)
class ToolCycleReport:
    run_id: str
    tool_name: str
    outcome: ToolSEPLOutcome
    active_config_before: Dict[str, float]
    candidate_config: Optional[Dict[str, float]]
    eval: Optional[ToolEvalResult]
    committed_version: Optional[str]
    dry_run: bool
    elapsed_sec: float
    timestamp: float


# ── Collaborator protocols ───────────────────────────────────────────────────


class RegistryLike(Protocol):
    def get(self, name: str, version: str = "latest") -> Optional[ResourceRecord]:
        ...

    def active_version(self, name: str) -> Optional[str]:
        ...

    def lineage(self, name: str, limit: int = 50) -> List[Dict[str, Any]]:
        ...

    def list(self, kind=None) -> List[ResourceRecord]:
        ...

    def restore(
        self, name: str, version: str, *, reason: str, actor: str
    ) -> ResourceRecord:
        ...


# ── Helpers ──────────────────────────────────────────────────────────────────


def _stable_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _load_parameter_ranges(record: ResourceRecord) -> Dict[str, Dict[str, float]]:
    """Return the ``metadata.parameter_ranges`` map or empty dict.

    Expected shape per key::

        {"min": <number>, "max": <number>, "step": <number>}

    Any entry missing a required field is dropped (we refuse to invent bounds).
    """
    raw = (record.metadata or {}).get("parameter_ranges")
    if not isinstance(raw, dict):
        return {}
    ranges: Dict[str, Dict[str, float]] = {}
    for key, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        try:
            lo = float(spec["min"])
            hi = float(spec["max"])
            step = float(spec.get("step", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        if step <= 0.0 or hi <= lo:
            continue
        ranges[key] = {"min": lo, "max": hi, "step": step}
    return ranges


def _load_fixtures(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("[sepl_tool] failed to load fixtures %s: %s", path, e)
        return []
    rows = data.get("fixtures") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    clean: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "input" not in row or "expected" not in row:
            continue
        if not isinstance(row["input"], dict):
            continue
        try:
            weight = float(row.get("weight", 1.0))
        except (TypeError, ValueError):
            weight = 1.0
        if weight <= 0.0:
            continue
        clean.append({
            "id": row.get("id") or f"row_{len(clean)}",
            "input": dict(row["input"]),
            "expected": row["expected"],
            "weight": weight,
        })
    return clean


def _active_record_and_cfg(
    registry: RegistryLike, name: str
) -> Tuple[Optional[ResourceRecord], Dict[str, float]]:
    rec = registry.get(name)
    if rec is None or rec.kind != ResourceKind.TOOL:
        return None, {}
    cfg = dict((rec.metadata or {}).get("config") or rec.fallback or {})
    return rec, cfg


# ── Operators ────────────────────────────────────────────────────────────────


class SEPLTool:
    """TOOL-kind evolution cycle.

    Parameters
    ----------
    registry
        Anything satisfying :class:`RegistryLike`. Defaults to the process
        singleton.
    fixtures_dir
        Override path for shadow-mode fixtures. Defaults to
        ``backend/resources/sepl_eval_fixtures_tools``.
    now_fn
        Injectable clock for rate-limit tests.
    rng
        Injectable RNG for deterministic tests.
    handlers
        Override the ``name -> {fn, output_kind}`` lookup used by Evaluate.
        Default pulls from :data:`backend.tool_handlers.TOOL_HANDLERS`.
    """

    def __init__(
        self,
        *,
        registry: Optional[RegistryLike] = None,
        fixtures_dir: Optional[Path] = None,
        now_fn: Callable[[], float] = time.time,
        rng: Optional[random.Random] = None,
        handlers: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._reg: RegistryLike = registry or get_resource_registry()
        self._fixtures_dir = Path(fixtures_dir) if fixtures_dir else DEFAULT_TOOL_FIXTURES_DIR
        self._now = now_fn
        seed_env = os.environ.get("SEPL_TOOL_SEED", "").strip()
        if rng is not None:
            self._rng = rng
        elif seed_env:
            try:
                self._rng = random.Random(int(seed_env))
            except ValueError:
                self._rng = random.Random()
        else:
            self._rng = random.Random()
        self._handlers = handlers if handlers is not None else TOOL_HANDLERS

    # ── Select ───────────────────────────────────────────────────────────

    def select(self, tool_names: List[str]) -> Optional[str]:
        """Pick the learnable TOOL that was least-recently updated.

        Phase C1 does not use reflections here (Phase C2+ may). We just round-
        robin through the learnable tools so that each one gets a fair shot at
        evolution over time. Ties are broken by alphabetical order for
        determinism.
        """
        candidates: List[Tuple[float, str]] = []
        for name in tool_names:
            rec = self._reg.get(name)
            if rec is None or rec.kind != ResourceKind.TOOL or not rec.learnable:
                continue
            lineage = self._reg.lineage(name, limit=50)
            last_update = 0.0
            for row in lineage:
                if row.get("operation") in ("update", "restore"):
                    try:
                        last_update = max(last_update, float(row.get("created_at") or 0.0))
                    except (TypeError, ValueError):
                        pass
            candidates.append((last_update, name))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][1]

    # ── Improve ──────────────────────────────────────────────────────────

    def improve(
        self,
        record: ResourceRecord,
        *,
        count: Optional[int] = None,
    ) -> List[ToolCandidate]:
        """Propose bounded-perturbation candidates for ``record.metadata.config``.

        The algorithm:
            1. Pick 1–3 parameters uniformly at random (capped at number of
               available parameters).
            2. For each picked parameter, step by +/- k*step where k is uniform
               on [1, SEPL_TOOL_MAX_PERTURB_STEPS]. Clamp to [min, max].
            3. Reject candidates that produce an unchanged config (can happen
               when already at a bound).
        """
        ranges = _load_parameter_ranges(record)
        active_cfg = dict((record.metadata or {}).get("config") or record.fallback or {})
        if not ranges or not active_cfg:
            return []

        max_steps = tool_sepl_max_perturb_steps()
        n_candidates = count if count is not None else tool_sepl_candidates_per_cycle()
        proposals: List[ToolCandidate] = []
        tried: set = set()

        keys_in_range = [k for k in active_cfg.keys() if k in ranges]
        if not keys_in_range:
            return []

        attempts = 0
        while len(proposals) < n_candidates and attempts < n_candidates * 6:
            attempts += 1
            n_pick = self._rng.randint(1, min(3, len(keys_in_range)))
            picked = self._rng.sample(keys_in_range, n_pick)
            cand = dict(active_cfg)
            perturbed: List[str] = []
            for key in picked:
                spec = ranges[key]
                direction = self._rng.choice((-1.0, 1.0))
                k = self._rng.randint(1, max_steps)
                new_val = float(cand[key]) + direction * k * spec["step"]
                new_val = max(spec["min"], min(spec["max"], new_val))
                if abs(new_val - float(cand[key])) < 1e-9:
                    continue
                cand[key] = round(new_val, 6)
                perturbed.append(f"{key}:{direction*k:+.0f}step")
            signature = tuple(sorted(cand.items()))
            if signature in tried or not perturbed:
                continue
            tried.add(signature)
            proposals.append(ToolCandidate(
                config=cand,
                rationale=f"perturb[{','.join(perturbed)}]",
            ))
        return proposals

    # ── Evaluate ─────────────────────────────────────────────────────────

    def evaluate(
        self,
        tool_name: str,
        candidate_cfg: Dict[str, float],
    ) -> Optional[ToolEvalResult]:
        """Shadow-score ``candidate_cfg`` vs the active config over fixtures.

        Returns ``None`` if prerequisites are missing (handler, fixtures, or
        the tool itself). Never mutates state; never calls live connectors.
        """
        active, active_cfg = _active_record_and_cfg(self._reg, tool_name)
        if active is None:
            return None

        handler_entry = self._handlers.get(tool_name)
        if handler_entry is None:
            return None
        fn = handler_entry["fn"]

        fixtures = _load_fixtures(self._fixtures_dir / f"{tool_name}.json")
        if not fixtures:
            return None

        total_w = 0.0
        active_correct = 0.0
        candidate_correct = 0.0
        active_hits = 0
        candidate_hits = 0
        per_row: List[Dict[str, Any]] = []
        for fx in fixtures:
            w = float(fx["weight"])
            total_w += w
            expected = fx["expected"]
            try:
                active_out = fn(dict(fx["input"]), dict(active_cfg))
            except Exception as e:
                logger.warning("[sepl_tool] active handler error on %s: %s", fx["id"], e)
                active_out = None
            try:
                candidate_out = fn(dict(fx["input"]), dict(candidate_cfg))
            except Exception as e:
                logger.warning("[sepl_tool] candidate handler error on %s: %s", fx["id"], e)
                candidate_out = None
            a_ok = active_out == expected
            c_ok = candidate_out == expected
            if a_ok:
                active_correct += w
                active_hits += 1
            if c_ok:
                candidate_correct += w
                candidate_hits += 1
            per_row.append({
                "id": fx["id"],
                "expected": expected,
                "active_out": active_out,
                "candidate_out": candidate_out,
                "active_ok": a_ok,
                "candidate_ok": c_ok,
            })

        if total_w <= 0.0:
            return None

        return ToolEvalResult(
            tool_name=tool_name,
            active_version=active.version,
            active_score=active_correct / total_w,
            candidate_score=candidate_correct / total_w,
            margin=(candidate_correct - active_correct) / total_w,
            fixtures_used=len(fixtures),
            active_hits=active_hits,
            candidate_hits=candidate_hits,
            per_fixture=per_row,
        )

    # ── Commit ───────────────────────────────────────────────────────────

    def _recent_commits(self, tool_name: str, *, within_sec: float) -> int:
        now = self._now()
        rows = self._reg.lineage(tool_name, limit=200)
        count = 0
        for row in rows:
            if row.get("operation") != "update":
                continue
            if str(row.get("actor", "")).startswith("sepl:tool"):
                try:
                    ts = float(row.get("created_at") or 0.0)
                except (TypeError, ValueError):
                    ts = 0.0
                if ts and (now - ts) <= within_sec:
                    count += 1
        return count

    def commit(
        self,
        tool_name: str,
        eval_result: ToolEvalResult,
        candidate_cfg: Dict[str, float],
        *,
        dry_run: Optional[bool] = None,
        run_id: Optional[str] = None,
    ) -> Tuple[ToolSEPLOutcome, Optional[str]]:
        """Write a new version of ``tool_name`` iff margin and rate limits allow."""
        dry = bool(tool_sepl_dry_run() if dry_run is None else dry_run)
        margin = eval_result.margin
        if margin < tool_sepl_min_margin():
            return ToolSEPLOutcome.REJECTED_LOW_MARGIN, None

        active, active_cfg = _active_record_and_cfg(self._reg, tool_name)
        if active is None:
            return ToolSEPLOutcome.ABORTED_NOT_FOUND, None
        if not active.learnable:
            return ToolSEPLOutcome.ABORTED_PINNED, None
        if active_cfg == candidate_cfg:
            return ToolSEPLOutcome.REJECTED_UNCHANGED, None

        recent = self._recent_commits(tool_name, within_sec=24 * 3600)
        # Enforce the STRICTER of (global cap, tier-specific cap). Tier-2+
        # are effectively blocked by a tier cap of 0.
        tier = int((active.metadata or {}).get("tier", 0) or 0)
        effective_cap = min(tool_sepl_max_per_day(), tool_sepl_max_per_day_for_tier(tier))
        if recent >= effective_cap:
            return ToolSEPLOutcome.REJECTED_RATE_LIMIT, None

        if dry:
            return ToolSEPLOutcome.DRY_RUN, None

        try:
            updated = update_tool_config(
                tool_name,
                candidate_cfg,
                reason=(
                    f"sepl_tool: margin={margin:+.3f} active={eval_result.active_score:.3f} "
                    f"candidate={eval_result.candidate_score:.3f} "
                    f"fixtures={eval_result.fixtures_used} run={run_id or 'adhoc'}"
                ),
                actor="sepl:tool",
            )
            return ToolSEPLOutcome.COMMITTED, updated.version
        except ResourcePinnedError:
            return ToolSEPLOutcome.ABORTED_PINNED, None
        except ResourceNotFoundError:
            return ToolSEPLOutcome.ABORTED_NOT_FOUND, None
        except Exception as e:
            logger.exception("[sepl_tool] commit failed for %s: %s", tool_name, e)
            return ToolSEPLOutcome.REJECTED_UNCHANGED, None

    # ── run_cycle ────────────────────────────────────────────────────────

    def run_cycle(
        self,
        tool_names: List[str],
        *,
        force_target: Optional[str] = None,
        force_enable: bool = False,
    ) -> ToolCycleReport:
        """Run Select → Improve → Evaluate → Commit for one target.

        ``force_enable`` overrides the master ``SEPL_TOOL_ENABLE`` flag for
        direct test calls; ``force_target`` bypasses Select.
        """
        start = self._now()
        run_id = _stable_run_id()

        if not force_enable and not tool_sepl_enabled():
            return ToolCycleReport(
                run_id=run_id,
                tool_name=force_target or (tool_names[0] if tool_names else ""),
                outcome=ToolSEPLOutcome.ABORTED_DISABLED,
                active_config_before={},
                candidate_config=None,
                eval=None,
                committed_version=None,
                dry_run=tool_sepl_dry_run(),
                elapsed_sec=self._now() - start,
                timestamp=start,
            )

        target = force_target or self.select(tool_names)
        if not target:
            return ToolCycleReport(
                run_id=run_id,
                tool_name="",
                outcome=ToolSEPLOutcome.ABORTED_NOT_FOUND,
                active_config_before={},
                candidate_config=None,
                eval=None,
                committed_version=None,
                dry_run=tool_sepl_dry_run(),
                elapsed_sec=self._now() - start,
                timestamp=start,
            )

        record = self._reg.get(target)
        if record is None or record.kind != ResourceKind.TOOL:
            return ToolCycleReport(
                run_id=run_id,
                tool_name=target,
                outcome=ToolSEPLOutcome.ABORTED_NOT_FOUND,
                active_config_before={},
                candidate_config=None,
                eval=None,
                committed_version=None,
                dry_run=tool_sepl_dry_run(),
                elapsed_sec=self._now() - start,
                timestamp=start,
            )
        if not record.learnable:
            return ToolCycleReport(
                run_id=run_id, tool_name=target,
                outcome=ToolSEPLOutcome.ABORTED_PINNED,
                active_config_before=dict((record.metadata or {}).get("config") or {}),
                candidate_config=None, eval=None, committed_version=None,
                dry_run=tool_sepl_dry_run(),
                elapsed_sec=self._now() - start, timestamp=start,
            )
        active_cfg = dict((record.metadata or {}).get("config") or record.fallback or {})

        if target not in self._handlers:
            return ToolCycleReport(
                run_id=run_id, tool_name=target,
                outcome=ToolSEPLOutcome.ABORTED_NO_HANDLER,
                active_config_before=active_cfg, candidate_config=None,
                eval=None, committed_version=None,
                dry_run=tool_sepl_dry_run(),
                elapsed_sec=self._now() - start, timestamp=start,
            )
        if not _load_parameter_ranges(record):
            return ToolCycleReport(
                run_id=run_id, tool_name=target,
                outcome=ToolSEPLOutcome.ABORTED_NO_RANGES,
                active_config_before=active_cfg, candidate_config=None,
                eval=None, committed_version=None,
                dry_run=tool_sepl_dry_run(),
                elapsed_sec=self._now() - start, timestamp=start,
            )
        fixtures_path = self._fixtures_dir / f"{target}.json"
        if not fixtures_path.is_file():
            return ToolCycleReport(
                run_id=run_id, tool_name=target,
                outcome=ToolSEPLOutcome.ABORTED_NO_FIXTURES,
                active_config_before=active_cfg, candidate_config=None,
                eval=None, committed_version=None,
                dry_run=tool_sepl_dry_run(),
                elapsed_sec=self._now() - start, timestamp=start,
            )

        proposals = self.improve(record)
        best_eval: Optional[ToolEvalResult] = None
        best_cand: Optional[ToolCandidate] = None
        for cand in proposals:
            res = self.evaluate(target, cand.config)
            if res is None:
                continue
            if best_eval is None or res.margin > best_eval.margin:
                best_eval = res
                best_cand = cand

        if best_eval is None or best_cand is None:
            return ToolCycleReport(
                run_id=run_id, tool_name=target,
                outcome=ToolSEPLOutcome.REJECTED_UNCHANGED,
                active_config_before=active_cfg, candidate_config=None,
                eval=None, committed_version=None,
                dry_run=tool_sepl_dry_run(),
                elapsed_sec=self._now() - start, timestamp=start,
            )

        outcome, new_version = self.commit(
            target, best_eval, best_cand.config, run_id=run_id,
        )
        return ToolCycleReport(
            run_id=run_id, tool_name=target, outcome=outcome,
            active_config_before=active_cfg,
            candidate_config=best_cand.config,
            eval=best_eval, committed_version=new_version,
            dry_run=tool_sepl_dry_run(),
            elapsed_sec=self._now() - start, timestamp=start,
        )


class SEPLToolKillSwitch:
    """Fixture-based auto-rollback for committed TOOL versions.

    The prompt-side kill switch partitions live reflections into pre/post
    cohorts. Tools don't (yet) have that infrastructure — reflections are
    stamped with ``prompt_versions`` but not ``tool_versions``. So for Phase
    C1 we use a STRICTLY OFFLINE check:

        For each TOOL with a recent ``sepl:tool`` commit inside the look-back
        window:
          * Read the committed config from the active record.
          * Read the prior config from the ``from_version`` named in lineage.
          * Run BOTH configs through the shadow-mode evaluator against the
            same fixtures SEPL used at commit time.
          * If ``prior_score - committed_score >= SEPL_TOOL_ROLLBACK_MARGIN``,
            call ``registry.restore(name, prior_version)`` when ``dry_run``
            is False AND ``SEPL_TOOL_AUTOCOMMIT=1``.

    Guarantees:
        * NEVER triggers on a non-SEPL commit (actor filter).
        * NEVER acts without at least one fixture — if the fixture file is
          missing, we report and bail.
        * Default dry-run yields a report only, never mutates. You need both
          ``dry_run=False`` AND the autocommit flag on to actually restore.
    """

    def __init__(
        self,
        *,
        registry: Optional[RegistryLike] = None,
        fixtures_dir: Optional[Path] = None,
        now_fn: Callable[[], float] = time.time,
        handlers: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._reg: RegistryLike = registry or get_resource_registry()
        self._fixtures_dir = Path(fixtures_dir) if fixtures_dir else DEFAULT_TOOL_FIXTURES_DIR
        self._now = now_fn
        self._handlers = handlers if handlers is not None else TOOL_HANDLERS

    # ── Public API ───────────────────────────────────────────────────────

    def check_all(self, *, dry_run: Optional[bool] = None) -> List[ToolRollbackReport]:
        """Evaluate every learnable TOOL. Never raises — per-tool errors are
        surfaced as ``ERROR`` rollback reports."""
        effective_dry = (not tool_sepl_autocommit()) if dry_run is None else dry_run
        reports: List[ToolRollbackReport] = []
        try:
            candidates = [r for r in self._reg.list(ResourceKind.TOOL) if r.learnable]
        except Exception as e:
            logger.exception("[sepl_tool/kill] registry list failed: %s", e)
            return reports

        for rec in candidates:
            try:
                reports.append(self.check(rec.name, dry_run=effective_dry))
            except Exception as e:
                logger.exception("[sepl_tool/kill] check failed for %s: %s", rec.name, e)
                reports.append(ToolRollbackReport(
                    run_id=_stable_run_id(),
                    tool_name=rec.name,
                    outcome=ToolRollbackOutcome.ERROR,
                    committed_version=None, prior_version=None,
                    committed_score=None, prior_score=None,
                    delta=None, margin=tool_sepl_rollback_margin(),
                    fixtures_used=0, restored_to_version=None,
                    dry_run=effective_dry, timestamp=self._now(),
                ))
        return reports

    def check(self, tool_name: str, *, dry_run: Optional[bool] = None) -> ToolRollbackReport:
        effective_dry = (not tool_sepl_autocommit()) if dry_run is None else dry_run
        run_id = _stable_run_id()
        start = self._now()
        margin = tool_sepl_rollback_margin()

        # 1. Find the most recent sepl:tool commit inside the window.
        cutoff = start - tool_sepl_rollback_window_hours() * 3600
        try:
            events = self._reg.lineage(tool_name, limit=200)
        except Exception as e:
            logger.warning("[sepl_tool/kill] lineage fetch failed: %s", e)
            events = []

        sepl_commits = [
            e for e in events
            if e.get("operation") == "update"
            and str(e.get("actor", "")) == "sepl:tool"
            and float(e.get("created_at") or 0.0) >= cutoff
        ]
        # Drop any SEPL commit that has ALREADY been rolled back after the
        # fact — prevents double-rollback loops on repeated check() calls.
        rollback_events = [
            e for e in events
            if e.get("operation") == "restore"
            and str(e.get("actor", "")).startswith("sepl:tool:rollback")
        ]
        if rollback_events and sepl_commits:
            sepl_commits = [
                c for c in sepl_commits
                if not any(
                    float(r.get("created_at") or 0.0) > float(c.get("created_at") or 0.0)
                    and str(r.get("from_version") or "") == str(c.get("to_version") or "")
                    for r in rollback_events
                )
            ]
        if not sepl_commits:
            return ToolRollbackReport(
                run_id=run_id, tool_name=tool_name,
                outcome=ToolRollbackOutcome.NO_RECENT_SEPL_COMMIT,
                committed_version=None, prior_version=None,
                committed_score=None, prior_score=None, delta=None,
                margin=margin, fixtures_used=0,
                restored_to_version=None, dry_run=effective_dry, timestamp=start,
            )

        latest = max(sepl_commits, key=lambda e: float(e.get("created_at") or 0.0))
        v_new = str(latest.get("to_version") or "")
        v_prev = latest.get("from_version")
        if not v_prev or not v_new:
            return ToolRollbackReport(
                run_id=run_id, tool_name=tool_name,
                outcome=ToolRollbackOutcome.NO_PRIOR_VERSION_AVAILABLE,
                committed_version=v_new or None, prior_version=None,
                committed_score=None, prior_score=None, delta=None,
                margin=margin, fixtures_used=0,
                restored_to_version=None, dry_run=effective_dry, timestamp=start,
            )

        # 2. Pull both records' configs.
        committed_rec = self._reg.get(tool_name, version=v_new)
        prior_rec = self._reg.get(tool_name, version=str(v_prev))
        if not committed_rec or not prior_rec:
            return ToolRollbackReport(
                run_id=run_id, tool_name=tool_name,
                outcome=ToolRollbackOutcome.NO_PRIOR_VERSION_AVAILABLE,
                committed_version=v_new, prior_version=str(v_prev),
                committed_score=None, prior_score=None, delta=None,
                margin=margin, fixtures_used=0,
                restored_to_version=None, dry_run=effective_dry, timestamp=start,
            )
        committed_cfg = dict((committed_rec.metadata or {}).get("config") or committed_rec.fallback or {})
        prior_cfg = dict((prior_rec.metadata or {}).get("config") or prior_rec.fallback or {})

        # 3. Handler + fixtures.
        handler_entry = self._handlers.get(tool_name)
        if handler_entry is None:
            return ToolRollbackReport(
                run_id=run_id, tool_name=tool_name,
                outcome=ToolRollbackOutcome.NO_HANDLER,
                committed_version=v_new, prior_version=str(v_prev),
                committed_score=None, prior_score=None, delta=None,
                margin=margin, fixtures_used=0,
                restored_to_version=None, dry_run=effective_dry, timestamp=start,
            )
        fn = handler_entry["fn"]
        fixtures = _load_fixtures(self._fixtures_dir / f"{tool_name}.json")
        if not fixtures:
            return ToolRollbackReport(
                run_id=run_id, tool_name=tool_name,
                outcome=ToolRollbackOutcome.NO_FIXTURES,
                committed_version=v_new, prior_version=str(v_prev),
                committed_score=None, prior_score=None, delta=None,
                margin=margin, fixtures_used=0,
                restored_to_version=None, dry_run=effective_dry, timestamp=start,
            )

        # 4. Score both configs.
        total_w = 0.0
        committed_correct = 0.0
        prior_correct = 0.0
        for fx in fixtures:
            w = float(fx["weight"])
            total_w += w
            try:
                c_out = fn(dict(fx["input"]), dict(committed_cfg))
            except Exception:
                c_out = None
            try:
                p_out = fn(dict(fx["input"]), dict(prior_cfg))
            except Exception:
                p_out = None
            if c_out == fx["expected"]:
                committed_correct += w
            if p_out == fx["expected"]:
                prior_correct += w
        committed_score = committed_correct / total_w if total_w else 0.0
        prior_score = prior_correct / total_w if total_w else 0.0
        delta = committed_score - prior_score  # negative => regression

        # 5. Decide.
        if -delta < margin:
            return ToolRollbackReport(
                run_id=run_id, tool_name=tool_name,
                outcome=ToolRollbackOutcome.OK_WITHIN_TOLERANCE,
                committed_version=v_new, prior_version=str(v_prev),
                committed_score=committed_score, prior_score=prior_score,
                delta=delta, margin=margin,
                fixtures_used=len(fixtures),
                restored_to_version=None, dry_run=effective_dry, timestamp=start,
            )

        # Regression large enough to act on.
        if effective_dry:
            return ToolRollbackReport(
                run_id=run_id, tool_name=tool_name,
                outcome=ToolRollbackOutcome.DRY_RUN,
                committed_version=v_new, prior_version=str(v_prev),
                committed_score=committed_score, prior_score=prior_score,
                delta=delta, margin=margin,
                fixtures_used=len(fixtures),
                restored_to_version=None, dry_run=True, timestamp=start,
            )

        try:
            restored = self._reg.restore(
                tool_name, str(v_prev),
                reason=(
                    f"sepl_tool_rollback: committed={committed_score:.3f} prior={prior_score:.3f} "
                    f"delta={delta:+.3f} margin={margin:.2f} run={run_id}"
                ),
                actor=f"sepl:tool:rollback:{run_id}",
            )
            return ToolRollbackReport(
                run_id=run_id, tool_name=tool_name,
                outcome=ToolRollbackOutcome.ROLLED_BACK,
                committed_version=v_new, prior_version=str(v_prev),
                committed_score=committed_score, prior_score=prior_score,
                delta=delta, margin=margin,
                fixtures_used=len(fixtures),
                restored_to_version=restored.version,
                dry_run=False, timestamp=start,
            )
        except Exception as e:
            logger.exception("[sepl_tool/kill] restore failed for %s: %s", tool_name, e)
            return ToolRollbackReport(
                run_id=run_id, tool_name=tool_name,
                outcome=ToolRollbackOutcome.ERROR,
                committed_version=v_new, prior_version=str(v_prev),
                committed_score=committed_score, prior_score=prior_score,
                delta=delta, margin=margin,
                fixtures_used=len(fixtures),
                restored_to_version=None, dry_run=effective_dry, timestamp=start,
            )


__all__ = [
    "SEPLTool",
    "SEPLToolKillSwitch",
    "ToolCycleReport",
    "ToolEvalResult",
    "ToolCandidate",
    "ToolSEPLOutcome",
    "ToolRollbackReport",
    "ToolRollbackOutcome",
    "tool_sepl_enabled",
    "tool_sepl_dry_run",
    "tool_sepl_min_margin",
    "tool_sepl_max_per_day",
    "tool_sepl_max_per_day_for_tier",
    "tool_sepl_autocommit",
    "tool_sepl_rollback_margin",
    "tool_sepl_rollback_window_hours",
    "DEFAULT_TOOL_FIXTURES_DIR",
]
