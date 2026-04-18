"""
SEPL — Self-Evolution Protocol Layer (Autogenesis §3.2, arXiv:2604.15034v1).

Closed-loop control-theoretic algebra for evolving learnable ``PROMPT`` resources
stored in the Phase A registry. Five atomic operators:

    Reflect (rho)    — aggregate recent failures for a target prompt
    Select  (sigma)  — pick the prompt that would benefit most from improvement
    Improve (iota)   — propose a candidate body via the `sepl_improver` meta-prompt
    Evaluate (eps)   — score candidate against active on a held-out fixture set
    Commit  (kappa)  — promote candidate iff margin >= SEPL_MIN_MARGIN, else log proposal

Safety invariants (enforced, tested):
  * Never operates on ``learnable=False`` resources (registry rejects in ``update``).
  * Every cycle ends with a lineage row, regardless of commit/reject/abort.
  * Dry-run mode short-circuits before ``commit`` — all earlier ops still log.
  * Minimum ``SEPL_MIN_SAMPLES`` reflections required to even start a cycle.
  * Hard cap on committed updates per prompt per 24h window (``SEPL_MAX_PER_DAY``).
  * Output schema shape of candidate body is validated against target's schema.
  * Feature-flagged OFF: ``SEPL_ENABLE=0`` is the default; only an explicit human
    config change enables autonomous mutation.

The module is side-effect-free on import. All collaborators (LLMClient,
ResourceRegistry, KnowledgeStore, eval fixtures) are injected so tests can
substitute deterministic fakes.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURES_DIR = BACKEND_DIR / "resources" / "sepl_eval_fixtures"


# ── Feature flag / tunables ──────────────────────────────────────────────────


def sepl_enabled() -> bool:
    """Master flag. Default OFF — nothing mutates prompts unless explicitly enabled."""
    return (os.environ.get("SEPL_ENABLE", "0").strip() or "0") == "1"


def sepl_dry_run() -> bool:
    """When 1, run every operator but stop before ``commit``. Default 1 (safer)."""
    return (os.environ.get("SEPL_DRY_RUN", "1").strip() or "1") == "1"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


# Minimum number of reflections to consider a cycle.
def sepl_min_samples() -> int:
    return max(1, _env_int("SEPL_MIN_SAMPLES", 10))


# Candidate must beat active by this margin to be committed (fraction, 0..1).
def sepl_min_margin() -> float:
    return max(0.0, min(1.0, _env_float("SEPL_MIN_MARGIN", 0.05)))


# How many committed updates per prompt per 24h window.
def sepl_max_commits_per_day() -> int:
    return max(1, _env_int("SEPL_MAX_PER_DAY", 1))


# Effectiveness ceiling for Select — a prompt whose recent effectiveness exceeds
# this is considered "healthy" and not a candidate for improvement.
def sepl_effectiveness_ceiling() -> float:
    return max(0.0, min(1.0, _env_float("SEPL_EFFECTIVENESS_CEILING", 0.6)))


# Max number of reflections to send into the improver's context (cost cap).
def sepl_context_reflections() -> int:
    return max(1, _env_int("SEPL_CONTEXT_REFLECTIONS", 6))


# ── Kill-switch tunables (PR 6) ──────────────────────────────────────────────


# Post-commit effectiveness must be this many percentage points WORSE than
# pre-commit before we roll back (fraction 0..1, default 0.10 = 10 pp).
def sepl_rollback_margin() -> float:
    return max(0.0, min(1.0, _env_float("SEPL_ROLLBACK_MARGIN", 0.10)))


# Minimum number of post-commit reflections before the kill switch will act.
# Guards against knee-jerk rollbacks on 1–2 unlucky trades.
def sepl_rollback_min_samples() -> int:
    return max(1, _env_int("SEPL_ROLLBACK_MIN_SAMPLES", 5))


# Look-back window for "recent" SEPL commits that the kill switch examines.
def sepl_rollback_window_hours() -> int:
    return max(1, _env_int("SEPL_ROLLBACK_WINDOW_HOURS", 168))  # 7 days


# ── Domain types ─────────────────────────────────────────────────────────────


class SEPLOutcome(str, Enum):
    COMMITTED = "committed"
    REJECTED_LOW_MARGIN = "rejected_low_margin"
    REJECTED_INVALID_SCHEMA = "rejected_invalid_schema"
    REJECTED_EMPTY_BODY = "rejected_empty_body"
    REJECTED_UNCHANGED = "rejected_unchanged"
    REJECTED_RATE_LIMIT = "rejected_rate_limit"
    ABORTED_PINNED = "aborted_pinned"
    ABORTED_INSUFFICIENT_DATA = "aborted_insufficient_data"
    ABORTED_NO_CANDIDATE = "aborted_no_candidate"
    DRY_RUN = "dry_run"


class RollbackOutcome(str, Enum):
    ROLLED_BACK = "rolled_back"
    DRY_RUN = "dry_run"
    OK_WITHIN_TOLERANCE = "ok_within_tolerance"
    INSUFFICIENT_POST_COMMIT_DATA = "insufficient_post_commit_data"
    NO_RECENT_SEPL_COMMIT = "no_recent_sepl_commit"
    NO_PRIOR_VERSION_AVAILABLE = "no_prior_version_available"
    ERROR = "error"


@dataclass(frozen=True)
class ReflectReport:
    """Output of the Reflect operator — aggregated failures for one target."""

    target_name: str
    sample_size: int
    effectiveness_mean: float
    failure_lessons: List[str]  # truncated, safe to send to LLM
    regime_breakdown: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SelectDecision:
    """Output of the Select operator."""

    target_name: Optional[str]
    reason: str
    candidates_considered: List[Tuple[str, float]]  # (name, effectiveness_mean)


@dataclass(frozen=True)
class ImproveProposal:
    """Output of the Improve operator."""

    target_name: str
    current_version: str
    new_body: str
    rationale: str
    confidence: float
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalResult:
    """Output of the Evaluate operator."""

    target_name: str
    fixtures_used: int
    active_score: float       # 0..1 — fraction of fixtures where active "won"
    candidate_score: float    # 0..1
    margin: float             # candidate_score - active_score
    invalid_candidate_outputs: int
    invalid_active_outputs: int
    details: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RollbackReport:
    """Trace of one kill-switch evaluation."""

    run_id: str
    target_name: str
    outcome: RollbackOutcome
    committed_version: Optional[str]           # the version SEPL committed
    prior_version: Optional[str]               # the version just before the commit
    post_commit_effectiveness: Optional[float]
    pre_commit_effectiveness: Optional[float]
    delta: Optional[float]                     # post - pre (negative = regression)
    post_commit_samples: int
    pre_commit_samples: int
    restored_to_version: Optional[str]
    dry_run: bool
    timestamp: float


@dataclass(frozen=True)
class CycleReport:
    """Full trace of one run_cycle invocation."""

    run_id: str
    outcome: SEPLOutcome
    select: Optional[SelectDecision]
    reflect: Optional[ReflectReport]
    proposal: Optional[ImproveProposal]
    evaluation: Optional[EvalResult]
    committed_version: Optional[str]
    elapsed_sec: float
    dry_run: bool
    timestamp: float


# ── Collaborator protocols (for injection-friendly testing) ─────────────────


class LLMLike(Protocol):
    """Minimum LLM surface SEPL needs. Satisfied by ``LLMClient``.

    The optional ``generate_with_body_override`` path is preferred by Evaluate
    when present; fakes without it fall through to the ``__sepl_candidate__``
    pseudo-role pathway (see :meth:`SEPL._call_candidate`).
    """

    async def generate_with_meta(
        self, role: str, prompt: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        ...


class RegistryLike(Protocol):
    """Minimum registry surface. Satisfied by ``ResourceRegistry``."""

    def get(self, name: str, version: str = "latest"):
        ...

    def list(self, kind=None):
        ...

    def active_version(self, name: str) -> Optional[str]:
        ...

    def update(
        self,
        name: str,
        new_body: str,
        *,
        bump,
        reason: str,
        actor: str,
        new_description: Optional[str] = None,
        new_metadata: Optional[Dict[str, Any]] = None,
    ):
        ...

    def lineage(self, name: str, limit: int = 50) -> List[Dict[str, Any]]:
        ...

    def _write_lineage_external(  # type: ignore[no-untyped-def]
        self,
        *,
        name: str,
        kind,
        from_version: Optional[str],
        to_version: str,
        operation: str,
        reason: str,
        actor: str,
    ):  # pragma: no cover — protocol stub, not required when not logging proposals
        ...


class ReflectionSourceLike(Protocol):
    """Minimum knowledge-store surface for Reflect/Select.

    Must return a list of ``{doc: str, meta: dict}`` rows sorted most-recent-first.
    """

    def fetch_recent_reflections(
        self, limit: int = 200, *, only_with_prompt_versions: bool = True
    ) -> List[Dict[str, Any]]:
        ...


# ── Helpers ──────────────────────────────────────────────────────────────────


_DANGEROUS_TOKENS = (
    "```",             # markdown fences
    "ignore previous", # jailbreak
    "ignore earlier",
    "you are now",     # persona hijack
    "system prompt",   # meta leakage
)


def _looks_safe(body: str) -> Tuple[bool, str]:
    """Cheap syntactic guard against obviously unsafe improver outputs."""
    if not isinstance(body, str) or not body.strip():
        return False, "empty body"
    lower = body.lower()
    for token in _DANGEROUS_TOKENS:
        if token in lower:
            return False, f"contains forbidden token: {token!r}"
    return True, ""


def _length_reasonable(current: str, candidate: str, *, max_ratio: float = 1.25) -> Tuple[bool, str]:
    """Candidate must stay within ``max_ratio`` of current length (per improver's own spec)."""
    if not current:
        return True, ""
    lc, ln = max(len(current), 1), len(candidate)
    if ln > lc * max_ratio or ln < lc / max_ratio:
        return False, f"length ratio out of bounds: {ln}/{lc}"
    return True, ""


def _stable_run_id() -> str:
    return uuid.uuid4().hex[:12]


def _aggregate_reflections_by_prompt(
    rows: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group reflection rows by the prompt-name that produced them.

    Phase A stamps ``prompt_versions`` (JSON string) onto each reflection. We
    associate a reflection with every prompt name that appears in that dict.
    Unstamped reflections are skipped (Phase A→B transition period).
    """
    by_prompt: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        meta = row.get("meta") or {}
        raw_versions = meta.get("prompt_versions") or ""
        if not raw_versions:
            continue
        try:
            versions = json.loads(raw_versions) if isinstance(raw_versions, str) else raw_versions
        except Exception:
            continue
        if not isinstance(versions, dict) or not versions:
            continue
        for prompt_name in versions.keys():
            by_prompt.setdefault(prompt_name, []).append(row)
    return by_prompt


def _mean_effectiveness(rows: List[Dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    vals = []
    for r in rows:
        meta = r.get("meta") or {}
        try:
            vals.append(float(meta.get("effectiveness_score", 0.5)))
        except Exception:
            continue
    return sum(vals) / len(vals) if vals else 0.0


# ── SEPL ─────────────────────────────────────────────────────────────────────


class SEPL:
    """
    Orchestrates the five-operator evolution cycle.

    Parameters
    ----------
    llm_client
        Anything satisfying :class:`LLMLike`. Used by Improve and Evaluate.
    registry
        Anything satisfying :class:`RegistryLike`. Used by Select/Commit.
    reflection_source
        Anything satisfying :class:`ReflectionSourceLike`. Used by Reflect.
    fixtures_dir
        Directory containing ``<prompt_name>.json`` files with eval fixtures.
        See ``_load_fixtures`` for format.
    now_fn
        Injectable clock so rate-limit tests can drive time.
    """

    def __init__(
        self,
        *,
        llm_client: LLMLike,
        registry: RegistryLike,
        reflection_source: ReflectionSourceLike,
        fixtures_dir: Optional[Path] = None,
        now_fn=time.time,
    ) -> None:
        self._llm = llm_client
        self._reg = registry
        self._refl = reflection_source
        self._fixtures_dir = Path(fixtures_dir) if fixtures_dir else DEFAULT_FIXTURES_DIR
        self._now = now_fn

    # ── Operators ────────────────────────────────────────────────────────

    def reflect(self, target_name: str, rows: List[Dict[str, Any]]) -> ReflectReport:
        """
        Aggregate past failures for ``target_name``. Pure function of inputs.

        A row is considered a FAILURE when ``effectiveness_score <= 0.5``. We
        keep at most ``sepl_context_reflections()`` lesson strings, truncated
        to 500 chars each to cap the improver's context size.
        """
        failures = [r for r in rows if float((r.get("meta") or {}).get("effectiveness_score", 0.5)) <= 0.5]
        lessons: List[str] = []
        regimes: Dict[str, int] = {}
        for r in failures[: sepl_context_reflections()]:
            doc = (r.get("doc") or "").strip()
            if doc:
                lessons.append(doc[:500])
            regime = (r.get("meta") or {}).get("regime", "UNKNOWN")
            regimes[regime] = regimes.get(regime, 0) + 1
        return ReflectReport(
            target_name=target_name,
            sample_size=len(rows),
            effectiveness_mean=_mean_effectiveness(rows),
            failure_lessons=lessons,
            regime_breakdown=regimes,
        )

    def select(self) -> SelectDecision:
        """
        Pick the learnable prompt with the LOWEST mean effectiveness over
        recent reflections (below the ceiling). Skips pinned resources and
        prompts with fewer than ``sepl_min_samples()`` rows.
        """
        rows = self._refl.fetch_recent_reflections(limit=500)
        if not rows:
            return SelectDecision(
                target_name=None,
                reason="no reflections available",
                candidates_considered=[],
            )

        by_prompt = _aggregate_reflections_by_prompt(rows)
        considered: List[Tuple[str, float]] = []

        # Only consider prompts the registry knows about AND are learnable.
        from .resource_registry import ResourceKind  # local import to ease cycles
        learnable_names = {
            r.name for r in self._reg.list(ResourceKind.PROMPT) if r.learnable  # type: ignore[union-attr]
        }

        for name, subset in by_prompt.items():
            if name not in learnable_names:
                continue
            if len(subset) < sepl_min_samples():
                continue
            mean = _mean_effectiveness(subset)
            considered.append((name, mean))

        if not considered:
            return SelectDecision(
                target_name=None,
                reason="no learnable prompt met minimum sample size",
                candidates_considered=[],
            )

        # Lowest effectiveness first; ignore "healthy" prompts above the ceiling.
        considered.sort(key=lambda x: x[1])
        worst_name, worst_score = considered[0]
        if worst_score >= sepl_effectiveness_ceiling():
            return SelectDecision(
                target_name=None,
                reason=f"all learnable prompts exceed effectiveness ceiling {sepl_effectiveness_ceiling()}",
                candidates_considered=considered,
            )
        return SelectDecision(
            target_name=worst_name,
            reason=f"lowest effectiveness ({worst_score:.3f}) over {len(by_prompt[worst_name])} samples",
            candidates_considered=considered,
        )

    async def improve(self, report: ReflectReport) -> Optional[ImproveProposal]:
        """Call the pinned ``sepl_improver`` meta-prompt to draft a candidate body."""
        target_rec = self._reg.get(report.target_name)
        if target_rec is None:
            logger.warning("[SEPL] improve: target %s not in registry", report.target_name)
            return None

        context = {
            "current_body": target_rec.body,
            "output_schema": target_rec.schema or {},
            "failure_lessons": report.failure_lessons,
            "effectiveness_mean": round(report.effectiveness_mean, 3),
            "sample_size": report.sample_size,
            "regime_breakdown": report.regime_breakdown,
        }
        prompt = (
            "Target prompt: " + report.target_name + "\n"
            "CURRENT_BODY:\n" + target_rec.body + "\n\n"
            "OUTPUT_SCHEMA (must be preserved):\n"
            + json.dumps(target_rec.schema or {}, indent=2)
            + "\n\nFAILURE_LESSONS (most recent):\n"
            + ("\n- " + "\n- ".join(report.failure_lessons) if report.failure_lessons else "(none)")
            + "\n\nPropose a NEW_BODY. Respond as instructed."
        )

        result, _meta = await self._llm.generate_with_meta("sepl_improver", prompt)
        if not isinstance(result, dict):
            return None

        new_body = str(result.get("new_body") or "").strip()
        rationale = str(result.get("rationale") or "").strip()
        try:
            confidence = float(result.get("confidence_0_1") or 0.0)
        except Exception:
            confidence = 0.0

        return ImproveProposal(
            target_name=report.target_name,
            current_version=target_rec.version,
            new_body=new_body,
            rationale=rationale,
            confidence=max(0.0, min(1.0, confidence)),
            meta={"context": context},
        )

    def _load_fixtures(self, target_name: str) -> List[Dict[str, Any]]:
        """
        Fixture JSON format (one file per prompt, optional):
            [
              {"input": "...", "reference_verdict": "BUY"}, ...
            ]
        """
        path = self._fixtures_dir / f"{target_name}.json"
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[SEPL] fixture parse failed for %s: %s", target_name, e)
            return []
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict) and "input" in d]

    async def evaluate(
        self, proposal: ImproveProposal, target_schema: Optional[Dict[str, Any]]
    ) -> EvalResult:
        """
        Score candidate vs active on a per-prompt fixture set.

        The scoring rule is intentionally conservative for Phase B: a run
        "wins" a fixture when its output is a JSON object whose keys are a
        superset of the schema's ``required`` list AND, if the fixture
        provides a ``reference_verdict``, the output's ``verdict`` matches.
        Absent fixtures → returns a zero-margin result (Commit will reject).
        """
        fixtures = self._load_fixtures(proposal.target_name)
        if not fixtures:
            return EvalResult(
                target_name=proposal.target_name,
                fixtures_used=0,
                active_score=0.0,
                candidate_score=0.0,
                margin=0.0,
                invalid_active_outputs=0,
                invalid_candidate_outputs=0,
            )

        required_keys = set()
        if isinstance(target_schema, dict):
            req = target_schema.get("required") or []
            if isinstance(req, list):
                required_keys = {str(k) for k in req}

        active_wins = 0
        candidate_wins = 0
        invalid_active = 0
        invalid_candidate = 0
        details: List[Dict[str, Any]] = []

        for fx in fixtures:
            reference = fx.get("reference_verdict")
            # We don't actually swap prompts via the LLM client here (would
            # require reaching into its internals); instead we call generate
            # for the real role (active) and simulate candidate via a
            # separate role key ``__sepl_candidate__`` that the test fake
            # understands. In production the LLMClient has a
            # ``generate_with_body_override`` extension added in PR 5.
            active_out, _ = await self._llm.generate_with_meta(
                proposal.target_name, str(fx.get("input", ""))
            )
            candidate_out = await self._call_candidate(proposal, fx)

            a_ok = self._passes(active_out, required_keys, reference)
            c_ok = self._passes(candidate_out, required_keys, reference)

            if not isinstance(active_out, dict) or not active_out:
                invalid_active += 1
            if not isinstance(candidate_out, dict) or not candidate_out:
                invalid_candidate += 1

            if a_ok:
                active_wins += 1
            if c_ok:
                candidate_wins += 1
            details.append(
                {
                    "input_preview": str(fx.get("input", ""))[:80],
                    "active_win": a_ok,
                    "candidate_win": c_ok,
                }
            )

        n = float(len(fixtures))
        a_score = active_wins / n
        c_score = candidate_wins / n
        return EvalResult(
            target_name=proposal.target_name,
            fixtures_used=len(fixtures),
            active_score=a_score,
            candidate_score=c_score,
            margin=c_score - a_score,
            invalid_active_outputs=invalid_active,
            invalid_candidate_outputs=invalid_candidate,
            details=details,
        )

    async def _call_candidate(
        self, proposal: ImproveProposal, fixture: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run the candidate body against a fixture input.

        Prefers the production override path (``generate_with_body_override``)
        where the candidate body is used verbatim as the system prompt and the
        registry is never touched. Falls back to the ``__sepl_candidate__:*``
        pseudo-role for tests/fakes that don't implement the override path.
        """
        user_prompt = str(fixture.get("input", ""))
        override = getattr(self._llm, "generate_with_body_override", None)
        if callable(override):
            out, _meta = await override(
                proposal.target_name,
                user_prompt,
                body=proposal.new_body,
                version_label="candidate",
            )
            return out if isinstance(out, dict) else {}

        pseudo_prompt = self._render_candidate_prompt(proposal, fixture)
        out, _ = await self._llm.generate_with_meta(
            f"__sepl_candidate__:{proposal.target_name}", pseudo_prompt
        )
        return out if isinstance(out, dict) else {}

    @staticmethod
    def _render_candidate_prompt(proposal: ImproveProposal, fixture: Dict[str, Any]) -> str:
        """Render a deterministic envelope for the pseudo-role fallback used by tests."""
        return json.dumps(
            {
                "candidate_body": proposal.new_body,
                "fixture_input": fixture.get("input"),
                "fixture_reference": fixture.get("reference_verdict"),
            },
            sort_keys=True,
        )

    @staticmethod
    def _passes(
        output: Any, required_keys: set, reference_verdict: Optional[str]
    ) -> bool:
        if not isinstance(output, dict) or not output:
            return False
        if required_keys and not required_keys.issubset(output.keys()):
            return False
        if reference_verdict is not None:
            got = output.get("verdict") or output.get("signal")
            if got is None:
                return False
            if isinstance(got, int):
                # "signal" semantic: -1/0/1 — map common verdicts
                ref_map = {
                    "STRONG BUY": 1, "BUY": 1,
                    "NEUTRAL": 0,
                    "SELL": -1, "STRONG SELL": -1,
                }
                if int(got) != ref_map.get(str(reference_verdict).upper(), -99):
                    return False
            else:
                if str(got).upper() != str(reference_verdict).upper():
                    return False
        return True

    # ── Rate limit / lineage guard ──────────────────────────────────────

    def _recent_commit_count(self, target_name: str) -> int:
        """How many SEPL commits for this target in the last 24h."""
        cutoff = self._now() - 24 * 3600
        try:
            events = self._reg.lineage(target_name, limit=50)
        except Exception:
            return 0
        return sum(
            1
            for e in events
            if e.get("operation") == "update"
            and str(e.get("actor", "")).startswith("sepl:")
            and float(e.get("created_at", 0)) >= cutoff
        )

    # ── Orchestrator ─────────────────────────────────────────────────────

    async def run_cycle(
        self,
        *,
        dry_run: Optional[bool] = None,
        force_target: Optional[str] = None,
    ) -> CycleReport:
        """
        Full Reflect → Select → Improve → Evaluate → Commit cycle.

        ``dry_run`` defaults to :func:`sepl_dry_run`. ``force_target`` bypasses
        the Select operator — used by manual triggers and tests.
        """
        start = self._now()
        run_id = _stable_run_id()
        effective_dry = sepl_dry_run() if dry_run is None else bool(dry_run)

        # SELECT
        if force_target:
            from .resource_registry import ResourceKind  # local import

            learnable_names = {
                r.name for r in self._reg.list(ResourceKind.PROMPT) if r.learnable  # type: ignore[union-attr]
            }
            if force_target not in learnable_names:
                return CycleReport(
                    run_id=run_id,
                    outcome=SEPLOutcome.ABORTED_PINNED,
                    select=SelectDecision(None, f"force_target {force_target!r} not learnable", []),
                    reflect=None,
                    proposal=None,
                    evaluation=None,
                    committed_version=None,
                    elapsed_sec=self._now() - start,
                    dry_run=effective_dry,
                    timestamp=start,
                )
            selection = SelectDecision(
                target_name=force_target,
                reason="force_target override",
                candidates_considered=[],
            )
        else:
            selection = self.select()
            if selection.target_name is None:
                return CycleReport(
                    run_id=run_id,
                    outcome=SEPLOutcome.ABORTED_INSUFFICIENT_DATA,
                    select=selection,
                    reflect=None,
                    proposal=None,
                    evaluation=None,
                    committed_version=None,
                    elapsed_sec=self._now() - start,
                    dry_run=effective_dry,
                    timestamp=start,
                )

        # Rate limit check
        if self._recent_commit_count(selection.target_name) >= sepl_max_commits_per_day():
            return CycleReport(
                run_id=run_id,
                outcome=SEPLOutcome.REJECTED_RATE_LIMIT,
                select=selection,
                reflect=None,
                proposal=None,
                evaluation=None,
                committed_version=None,
                elapsed_sec=self._now() - start,
                dry_run=effective_dry,
                timestamp=start,
            )

        # REFLECT — reuse rows filtered to the selected prompt
        all_rows = self._refl.fetch_recent_reflections(limit=500)
        by_prompt = _aggregate_reflections_by_prompt(all_rows)
        target_rows = by_prompt.get(selection.target_name, [])
        reflect_report = self.reflect(selection.target_name, target_rows)

        # IMPROVE
        proposal = await self.improve(reflect_report)
        if proposal is None or not proposal.new_body:
            return CycleReport(
                run_id=run_id,
                outcome=SEPLOutcome.ABORTED_NO_CANDIDATE,
                select=selection,
                reflect=reflect_report,
                proposal=proposal,
                evaluation=None,
                committed_version=None,
                elapsed_sec=self._now() - start,
                dry_run=effective_dry,
                timestamp=start,
            )

        # Candidate syntactic safety guards
        safe_ok, safe_reason = _looks_safe(proposal.new_body)
        if not safe_ok:
            logger.warning("[SEPL] candidate rejected: %s", safe_reason)
            return CycleReport(
                run_id=run_id,
                outcome=SEPLOutcome.REJECTED_INVALID_SCHEMA,
                select=selection,
                reflect=reflect_report,
                proposal=proposal,
                evaluation=None,
                committed_version=None,
                elapsed_sec=self._now() - start,
                dry_run=effective_dry,
                timestamp=start,
            )

        current_rec = self._reg.get(selection.target_name)
        if current_rec is None:
            return CycleReport(
                run_id=run_id,
                outcome=SEPLOutcome.ABORTED_NO_CANDIDATE,
                select=selection,
                reflect=reflect_report,
                proposal=proposal,
                evaluation=None,
                committed_version=None,
                elapsed_sec=self._now() - start,
                dry_run=effective_dry,
                timestamp=start,
            )

        if proposal.new_body.strip() == current_rec.body.strip():
            return CycleReport(
                run_id=run_id,
                outcome=SEPLOutcome.REJECTED_UNCHANGED,
                select=selection,
                reflect=reflect_report,
                proposal=proposal,
                evaluation=None,
                committed_version=None,
                elapsed_sec=self._now() - start,
                dry_run=effective_dry,
                timestamp=start,
            )

        length_ok, length_reason = _length_reasonable(current_rec.body, proposal.new_body)
        if not length_ok:
            logger.warning("[SEPL] candidate rejected: %s", length_reason)
            return CycleReport(
                run_id=run_id,
                outcome=SEPLOutcome.REJECTED_INVALID_SCHEMA,
                select=selection,
                reflect=reflect_report,
                proposal=proposal,
                evaluation=None,
                committed_version=None,
                elapsed_sec=self._now() - start,
                dry_run=effective_dry,
                timestamp=start,
            )

        # EVALUATE
        eval_result = await self.evaluate(proposal, current_rec.schema)

        if eval_result.fixtures_used == 0:
            # No held-out evidence — never commit on faith.
            return CycleReport(
                run_id=run_id,
                outcome=SEPLOutcome.REJECTED_LOW_MARGIN,
                select=selection,
                reflect=reflect_report,
                proposal=proposal,
                evaluation=eval_result,
                committed_version=None,
                elapsed_sec=self._now() - start,
                dry_run=effective_dry,
                timestamp=start,
            )

        if eval_result.margin < sepl_min_margin():
            return CycleReport(
                run_id=run_id,
                outcome=SEPLOutcome.REJECTED_LOW_MARGIN,
                select=selection,
                reflect=reflect_report,
                proposal=proposal,
                evaluation=eval_result,
                committed_version=None,
                elapsed_sec=self._now() - start,
                dry_run=effective_dry,
                timestamp=start,
            )

        # COMMIT (unless dry-run)
        if effective_dry:
            return CycleReport(
                run_id=run_id,
                outcome=SEPLOutcome.DRY_RUN,
                select=selection,
                reflect=reflect_report,
                proposal=proposal,
                evaluation=eval_result,
                committed_version=None,
                elapsed_sec=self._now() - start,
                dry_run=True,
                timestamp=start,
            )

        try:
            updated = self._reg.update(
                selection.target_name,
                proposal.new_body,
                bump="patch",
                reason=(
                    f"SEPL autoupdate run={run_id} "
                    f"margin={eval_result.margin:.3f} "
                    f"samples={reflect_report.sample_size} "
                    f"rationale={proposal.rationale[:120]!r}"
                )[:500],
                actor=f"sepl:{run_id}",
                new_metadata={
                    "sepl": {
                        "run_id": run_id,
                        "active_score": eval_result.active_score,
                        "candidate_score": eval_result.candidate_score,
                        "margin": eval_result.margin,
                        "fixtures_used": eval_result.fixtures_used,
                    }
                },
            )
        except Exception as e:
            # Registry may reject pinned/missing — surface cleanly
            logger.exception("[SEPL] commit raised: %s", e)
            return CycleReport(
                run_id=run_id,
                outcome=SEPLOutcome.ABORTED_PINNED,
                select=selection,
                reflect=reflect_report,
                proposal=proposal,
                evaluation=eval_result,
                committed_version=None,
                elapsed_sec=self._now() - start,
                dry_run=False,
                timestamp=start,
            )

        return CycleReport(
            run_id=run_id,
            outcome=SEPLOutcome.COMMITTED,
            select=selection,
            reflect=reflect_report,
            proposal=proposal,
            evaluation=eval_result,
            committed_version=updated.version,
            elapsed_sec=self._now() - start,
            dry_run=False,
            timestamp=start,
        )


# ── Concrete ReflectionSource adapter over KnowledgeStore ────────────────────


# ── Kill switch (PR 6) ───────────────────────────────────────────────────────


class SEPLKillSwitch:
    """
    Auto-rollback loop — the other half of the control-theoretic contract.

    SEPL.commit promotes candidates it THINKS will help. This class confirms
    against real outcomes and restores the previous version when post-commit
    reflections show measurable regression.

    Flow (once per scheduled tick, or on manual ``/sepl/kill-switch/run``):

      1. For each prompt that has ``sepl:*`` lineage in the last
         ``SEPL_ROLLBACK_WINDOW_HOURS``:
         - Identify the committed version V_new and its predecessor V_prev
           (by reading lineage).
         - Partition reflections stamped with that prompt into two cohorts:
           ``pre`` (stamped with V_prev) and ``post`` (stamped with V_new).
         - Require ``len(post) >= SEPL_ROLLBACK_MIN_SAMPLES`` — refuse to act
           on thin evidence.
         - If ``mean(post.effectiveness) < mean(pre.effectiveness) - margin``,
           call ``registry.restore(prompt, V_prev, actor='sepl:rollback:<id>')``
           and emit a ``RollbackReport`` with outcome=ROLLED_BACK.

    Dry-run mode (``dry_run=True``) performs the analysis and returns the
    report without calling restore. The scheduled tick uses ``dry_run=True``
    unless ``SEPL_AUTOCOMMIT=1`` — same gate as :class:`SEPL`.
    """

    def __init__(
        self,
        *,
        registry: RegistryLike,
        reflection_source: ReflectionSourceLike,
        now_fn=time.time,
    ) -> None:
        self._reg = registry
        self._refl = reflection_source
        self._now = now_fn

    # ── Public API ──────────────────────────────────────────────────────

    def check_all(self, *, dry_run: bool = True) -> List[RollbackReport]:
        """
        Evaluate every prompt with a recent SEPL commit. Returns a list of
        reports (one per prompt inspected). Never raises — errors are
        surfaced in the report outcome.
        """
        reports: List[RollbackReport] = []
        from .resource_registry import ResourceKind

        try:
            candidates = [r for r in self._reg.list(ResourceKind.PROMPT) if r.learnable]
        except Exception as e:
            logger.exception("[KillSwitch] registry list failed: %s", e)
            return reports

        for rec in candidates:
            try:
                reports.append(self.check(rec.name, dry_run=dry_run))
            except Exception as e:
                logger.exception("[KillSwitch] check failed for %s: %s", rec.name, e)
                reports.append(
                    RollbackReport(
                        run_id=_stable_run_id(),
                        target_name=rec.name,
                        outcome=RollbackOutcome.ERROR,
                        committed_version=None,
                        prior_version=None,
                        post_commit_effectiveness=None,
                        pre_commit_effectiveness=None,
                        delta=None,
                        post_commit_samples=0,
                        pre_commit_samples=0,
                        restored_to_version=None,
                        dry_run=dry_run,
                        timestamp=self._now(),
                    )
                )
        return reports

    def check(self, target_name: str, *, dry_run: bool = True) -> RollbackReport:
        """Evaluate a single prompt. Pure read + optional restore call."""
        run_id = _stable_run_id()
        start = self._now()

        # 1. Find the most recent SEPL commit in the look-back window.
        cutoff = start - sepl_rollback_window_hours() * 3600
        try:
            events = self._reg.lineage(target_name, limit=200)
        except Exception as e:
            logger.warning("[KillSwitch] lineage fetch failed: %s", e)
            events = []

        sepl_commits = [
            e
            for e in events
            if e.get("operation") == "update"
            and str(e.get("actor", "")).startswith("sepl:")
            and not str(e.get("actor", "")).startswith("sepl:rollback:")
            and float(e.get("created_at", 0)) >= cutoff
        ]
        if not sepl_commits:
            return RollbackReport(
                run_id=run_id,
                target_name=target_name,
                outcome=RollbackOutcome.NO_RECENT_SEPL_COMMIT,
                committed_version=None,
                prior_version=None,
                post_commit_effectiveness=None,
                pre_commit_effectiveness=None,
                delta=None,
                post_commit_samples=0,
                pre_commit_samples=0,
                restored_to_version=None,
                dry_run=dry_run,
                timestamp=start,
            )

        # Pick the most recent one.
        latest = max(sepl_commits, key=lambda e: float(e.get("created_at", 0)))
        v_new = str(latest.get("to_version") or "")
        v_prev = latest.get("from_version")
        commit_time = float(latest.get("created_at", 0))

        if not v_prev:
            return RollbackReport(
                run_id=run_id,
                target_name=target_name,
                outcome=RollbackOutcome.NO_PRIOR_VERSION_AVAILABLE,
                committed_version=v_new or None,
                prior_version=None,
                post_commit_effectiveness=None,
                pre_commit_effectiveness=None,
                delta=None,
                post_commit_samples=0,
                pre_commit_samples=0,
                restored_to_version=None,
                dry_run=dry_run,
                timestamp=start,
            )

        # 2. Partition reflections into pre/post cohorts based on which version
        #    of this prompt they were stamped with.
        rows = self._refl.fetch_recent_reflections(limit=500)
        pre_rows: List[Dict[str, Any]] = []
        post_rows: List[Dict[str, Any]] = []
        for row in rows:
            versions = _extract_versions(row)
            stamped = versions.get(target_name)
            if stamped == v_new:
                post_rows.append(row)
            elif stamped == str(v_prev):
                pre_rows.append(row)

        # 3. Guardrail: need enough post-commit samples to act.
        if len(post_rows) < sepl_rollback_min_samples():
            return RollbackReport(
                run_id=run_id,
                target_name=target_name,
                outcome=RollbackOutcome.INSUFFICIENT_POST_COMMIT_DATA,
                committed_version=v_new,
                prior_version=str(v_prev),
                post_commit_effectiveness=(
                    _mean_effectiveness(post_rows) if post_rows else None
                ),
                pre_commit_effectiveness=(
                    _mean_effectiveness(pre_rows) if pre_rows else None
                ),
                delta=None,
                post_commit_samples=len(post_rows),
                pre_commit_samples=len(pre_rows),
                restored_to_version=None,
                dry_run=dry_run,
                timestamp=start,
            )

        post_score = _mean_effectiveness(post_rows)
        pre_score = _mean_effectiveness(pre_rows) if pre_rows else 0.5  # neutral baseline
        delta = post_score - pre_score
        margin = sepl_rollback_margin()

        # 4. Decide.
        if delta >= -margin:
            return RollbackReport(
                run_id=run_id,
                target_name=target_name,
                outcome=RollbackOutcome.OK_WITHIN_TOLERANCE,
                committed_version=v_new,
                prior_version=str(v_prev),
                post_commit_effectiveness=post_score,
                pre_commit_effectiveness=pre_score,
                delta=delta,
                post_commit_samples=len(post_rows),
                pre_commit_samples=len(pre_rows),
                restored_to_version=None,
                dry_run=dry_run,
                timestamp=start,
            )

        # Regression detected.
        if dry_run:
            return RollbackReport(
                run_id=run_id,
                target_name=target_name,
                outcome=RollbackOutcome.DRY_RUN,
                committed_version=v_new,
                prior_version=str(v_prev),
                post_commit_effectiveness=post_score,
                pre_commit_effectiveness=pre_score,
                delta=delta,
                post_commit_samples=len(post_rows),
                pre_commit_samples=len(pre_rows),
                restored_to_version=None,
                dry_run=True,
                timestamp=start,
            )

        # Live restore.
        try:
            self._reg.restore(
                target_name,
                str(v_prev),
                reason=(
                    f"SEPL kill-switch rollback run={run_id} "
                    f"delta={delta:.3f} post_n={len(post_rows)} pre_n={len(pre_rows)} "
                    f"committed_at={commit_time}"
                )[:500],
                actor=f"sepl:rollback:{run_id}",
            )
            return RollbackReport(
                run_id=run_id,
                target_name=target_name,
                outcome=RollbackOutcome.ROLLED_BACK,
                committed_version=v_new,
                prior_version=str(v_prev),
                post_commit_effectiveness=post_score,
                pre_commit_effectiveness=pre_score,
                delta=delta,
                post_commit_samples=len(post_rows),
                pre_commit_samples=len(pre_rows),
                restored_to_version=str(v_prev),
                dry_run=False,
                timestamp=start,
            )
        except Exception as e:
            logger.exception("[KillSwitch] restore failed: %s", e)
            return RollbackReport(
                run_id=run_id,
                target_name=target_name,
                outcome=RollbackOutcome.ERROR,
                committed_version=v_new,
                prior_version=str(v_prev),
                post_commit_effectiveness=post_score,
                pre_commit_effectiveness=pre_score,
                delta=delta,
                post_commit_samples=len(post_rows),
                pre_commit_samples=len(pre_rows),
                restored_to_version=None,
                dry_run=False,
                timestamp=start,
            )


def _extract_versions(row: Dict[str, Any]) -> Dict[str, str]:
    """Normalize the ``prompt_versions`` metadata back into a dict."""
    raw = (row.get("meta") or {}).get("prompt_versions")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


class KnowledgeStoreReflectionSource:
    """Pulls reflections via Chroma for production use."""

    def __init__(self, knowledge_store) -> None:
        self._ks = knowledge_store

    def fetch_recent_reflections(
        self, limit: int = 200, *, only_with_prompt_versions: bool = True
    ) -> List[Dict[str, Any]]:
        col = self._ks._safe_col("swarm_reflections")
        if col is None:
            return []
        try:
            payload = col.get(include=["documents", "metadatas"])
        except Exception as e:
            logger.warning("[SEPL] reflection fetch failed: %s", e)
            return []
        docs = payload.get("documents") or []
        metas = payload.get("metadatas") or []
        rows: List[Dict[str, Any]] = []
        for doc, meta in zip(docs, metas):
            if only_with_prompt_versions and not (meta or {}).get("prompt_versions"):
                continue
            rows.append({"doc": doc, "meta": meta or {}})
        # Sort by date desc if present
        rows.sort(key=lambda r: (r["meta"].get("date", ""),), reverse=True)
        return rows[: max(1, int(limit))]


class DecisionLedgerReflectionSource:
    """Pulls SEPL reflections from the Decision-Outcome Ledger.

    The existing :class:`KnowledgeStoreReflectionSource` is limited to the
    ``swarm_reflections`` Chroma collection, so only factor agents can drive
    prompt evolution. This source generalises Reflect to every
    ``decision_events`` row that has at least one graded
    ``outcome_observations`` row — meaning debate moderators, chat turns, and
    any future producer can feed SEPL the moment they emit a decision and the
    nightly grader rules on it.

    Output shape mirrors :class:`ReflectionSourceLike` so SEPL's internals
    (``_aggregate_reflections_by_prompt`` + ``_mean_effectiveness``) keep
    working unchanged:

    * ``doc``   — one-line human-readable summary of the decision + outcome
    * ``meta``  — must carry:
        - ``prompt_versions``     JSON string, from ``decision_events.prompt_versions``
        - ``effectiveness_score`` 1.0 on correct, 0.0 on incorrect, 0.5 on unlabelled
        - ``decision_id``, ``decision_type``, ``symbol``, ``horizon``, ``market_regime``
          so downstream ledger-based analytics can join back without re-parsing.
    """

    def __init__(
        self,
        ledger=None,
        *,
        horizon: str = "5d",
        decision_types: Optional[Sequence[str]] = None,
    ) -> None:
        # Lazy-bound — mirror the other source so we don't force a ledger init
        # just for importing the module (important in unit tests).
        from . import decision_ledger as _dl

        self._dl = _dl
        self._ledger = ledger
        self._horizon = horizon
        self._decision_types = tuple(decision_types) if decision_types else None

    def _ledger_ref(self):
        if self._ledger is not None:
            return self._ledger
        return self._dl.get_ledger()

    def fetch_recent_reflections(
        self, limit: int = 200, *, only_with_prompt_versions: bool = True
    ) -> List[Dict[str, Any]]:
        ledger = self._ledger_ref()
        # We only support the SQLite backend's raw-connection path for now;
        # Supabase falls through to an empty list (it will get its own path
        # once the materialised view lands in ``feature_correlations``).
        conn = None
        try:
            conn = ledger._conn()  # type: ignore[attr-defined]
        except Exception:
            return []
        if conn is None:
            return []
        try:
            rows = conn.execute(
                """
                SELECT
                    d.decision_id            AS decision_id,
                    d.decision_type          AS decision_type,
                    d.symbol                 AS symbol,
                    d.verdict                AS verdict,
                    d.confidence             AS confidence,
                    d.model                  AS model,
                    d.source_route           AS source_route,
                    d.prompt_versions_json   AS prompt_versions,
                    d.output_json            AS output_json,
                    d.created_at             AS created_at,
                    o.horizon          AS horizon,
                    o.metric           AS metric,
                    o.value            AS value,
                    o.excess_return    AS excess_return,
                    o.correct_bool     AS correct_bool
                FROM decision_events d
                JOIN outcome_observations o
                  ON o.decision_id = d.decision_id
                WHERE o.horizon = ?
                  AND o.metric  = 'excess_return'
                ORDER BY d.created_at DESC
                LIMIT ?
                """,
                (self._horizon, int(max(1, limit))),
            ).fetchall()
        except Exception as e:
            logger.warning("[SEPL] DecisionLedgerReflectionSource query failed: %s", e)
            return []

        out: List[Dict[str, Any]] = []
        for r in rows:
            dt = str(r["decision_type"] or "")
            if self._decision_types and dt not in self._decision_types:
                continue
            prompt_versions = str(r["prompt_versions"] or "")
            if only_with_prompt_versions:
                # Empty "{}" is the default INSERT value — treat it as "no
                # versions recorded" so Reflect doesn't aggregate on phantom
                # prompt names produced by the column default.
                try:
                    pv_parsed = json.loads(prompt_versions) if prompt_versions else {}
                except Exception:
                    pv_parsed = {}
                if not isinstance(pv_parsed, dict) or not pv_parsed:
                    continue
            correct = r["correct_bool"]
            if correct is None:
                score = 0.5
                corr_txt = "unlabelled"
            elif int(correct) == 1:
                score = 1.0
                corr_txt = "correct"
            else:
                score = 0.0
                corr_txt = "incorrect"

            excess = r["excess_return"]
            try:
                excess_f = float(excess) if excess is not None else 0.0
            except Exception:
                excess_f = 0.0

            symbol = str(r["symbol"] or "")
            verdict = str(r["verdict"] or "")
            doc = (
                f"[{dt}] {symbol} verdict={verdict} "
                f"horizon={self._horizon} excess_return={excess_f:+.3%} "
                f"→ {corr_txt}"
            )

            # Best-effort regime from feature_snapshots (single extra SELECT per
            # row — bounded by ``limit`` and the decision_id index).
            regime = ""
            try:
                rr = conn.execute(
                    "SELECT value_str FROM feature_snapshots "
                    "WHERE decision_id = ? AND feature_name = 'market_regime' LIMIT 1",
                    (r["decision_id"],),
                ).fetchone()
                if rr and rr["value_str"]:
                    regime = str(rr["value_str"])
            except Exception:
                regime = ""

            out.append(
                {
                    "doc": doc,
                    "meta": {
                        "decision_id": str(r["decision_id"] or ""),
                        "decision_type": dt,
                        "symbol": symbol,
                        "verdict": verdict,
                        "confidence": r["confidence"],
                        "model": str(r["model"] or ""),
                        "source_route": str(r["source_route"] or ""),
                        "prompt_versions": prompt_versions,
                        "effectiveness_score": score,
                        "excess_return": excess_f,
                        "horizon": self._horizon,
                        "market_regime": regime,
                        "date": _iso_date_from_ts(r["created_at"]),
                    },
                }
            )
        return out


def _iso_date_from_ts(ts: Any) -> str:
    """Helper — ``outcome_observations.created_at`` → ISO date string (UTC)."""
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
    except Exception:
        return ""
