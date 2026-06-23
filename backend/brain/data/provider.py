"""Provider adapters, rate limiting, completeness review, fallback router, backfill.

The free-tier strategy (docs Section 04): many free providers, each rate-limited
and sometimes incomplete. We (1) pace each provider with a TokenBucket, (2) try
providers in priority order, (3) accept only records that pass the Completeness
Reviewer, and (4) run a long, resumable, paced backfill with progress tracking.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set


class ProviderError(Exception):
    """Raised by an adapter when a fetch fails (timeout, 4xx/5xx, parse error)."""


class TokenBucket:
    """Rate limiter. ``time_fn`` is injectable so tests are deterministic."""

    def __init__(self, rate_per_sec: float, capacity: float,
                 time_fn: Callable[[], float] = time.monotonic):
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity)
        self.time_fn = time_fn
        self._tokens = float(capacity)
        self._last = time_fn()

    def _refill(self) -> None:
        now = self.time_fn()
        elapsed = max(0.0, now - self._last)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last = now

    def try_acquire(self, n: float = 1.0) -> bool:
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def time_until_available(self, n: float = 1.0) -> float:
        self._refill()
        if self._tokens >= n:
            return 0.0
        return (n - self._tokens) / self.rate if self.rate > 0 else float("inf")


class ProviderAdapter:
    """Base adapter. Subclasses implement ``_fetch`` and declare capabilities."""

    name: str = "base"
    capabilities: Set[str] = set()

    def __init__(self, bucket: Optional[TokenBucket] = None):
        # Default: effectively unlimited (tests override with strict buckets).
        self.bucket = bucket or TokenBucket(rate_per_sec=1e9, capacity=1e9)

    def supports(self, data_type: str) -> bool:
        return data_type in self.capabilities

    def fetch(self, ticker: str, data_type: str) -> Dict:
        if not self.supports(data_type):
            raise ProviderError(f"{self.name} does not support {data_type!r}")
        return self._fetch(ticker, data_type)

    def _fetch(self, ticker: str, data_type: str) -> Dict:  # pragma: no cover - abstract
        raise NotImplementedError


# Canonical required fields + sanity bounds per data type (extend as needed).
DEFAULT_REQUIRED_FIELDS = {
    "price": ["ticker", "date", "close", "volume"],
    "fundamentals": ["ticker", "period", "revenue", "net_income", "shares_outstanding"],
    "news_sentiment": ["ticker", "date", "sentiment_score"],
}

DEFAULT_SANITY_BOUNDS = {
    "close": (0.0, 1_000_000.0),
    "volume": (0.0, 1e15),
    "sentiment_score": (-1.0, 1.0),
    "shares_outstanding": (0.0, 1e13),
}


class CompletenessReviewer:
    """Gate that rejects records with missing required fields or out-of-bounds values."""

    def __init__(self, required_fields: Optional[Dict[str, List[str]]] = None,
                 sanity_bounds: Optional[Dict[str, tuple]] = None):
        self.required_fields = required_fields or DEFAULT_REQUIRED_FIELDS
        self.sanity_bounds = sanity_bounds or DEFAULT_SANITY_BOUNDS

    def review(self, record: Optional[Dict], data_type: str) -> Dict:
        required = self.required_fields.get(data_type, [])
        if not record:
            return {"complete": False, "missing": list(required), "issues": ["empty record"]}
        missing = [f for f in required if record.get(f) in (None, "")]
        issues: List[str] = []
        for field_name, (lo, hi) in self.sanity_bounds.items():
            v = record.get(field_name)
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                issues.append(f"{field_name} not numeric")
                continue
            if fv < lo or fv > hi:
                issues.append(f"{field_name}={fv} out of bounds [{lo}, {hi}]")
        return {"complete": not missing and not issues, "missing": missing, "issues": issues}

    def reconcile(self, records: Sequence[Dict], field_name: str, rel_tol: float = 0.05) -> Dict:
        """Cross-provider agreement check on a numeric field (e.g. close price)."""
        vals = [float(r[field_name]) for r in records if r.get(field_name) is not None]
        if len(vals) < 2:
            return {"agree": True, "values": vals}
        lo, hi = min(vals), max(vals)
        base = abs(lo) if lo != 0 else 1.0
        return {"agree": (hi - lo) / base <= rel_tol, "values": vals,
                "spread": (hi - lo) / base}


class ProviderRouter:
    """Try providers in priority order until one returns a *complete* record."""

    def __init__(self, providers_by_type: Dict[str, List[ProviderAdapter]],
                 reviewer: Optional[CompletenessReviewer] = None):
        self.providers_by_type = providers_by_type
        self.reviewer = reviewer or CompletenessReviewer()

    def get(self, ticker: str, data_type: str) -> Dict:
        attempts: List[Dict] = []
        providers = self.providers_by_type.get(data_type, [])
        for prov in providers:
            if not prov.bucket.try_acquire():
                attempts.append({"provider": prov.name, "status": "rate_limited",
                                 "wait_s": round(prov.bucket.time_until_available(), 3)})
                continue
            try:
                rec = prov.fetch(ticker, data_type)
            except ProviderError as e:
                attempts.append({"provider": prov.name, "status": "error", "detail": str(e)})
                continue
            review = self.reviewer.review(rec, data_type)
            attempts.append({"provider": prov.name,
                             "status": "complete" if review["complete"] else "incomplete",
                             "missing": review["missing"], "issues": review["issues"]})
            if review["complete"]:
                return {"complete": True, "record": rec, "provider": prov.name, "attempts": attempts}
        return {"complete": False, "record": None, "provider": None, "attempts": attempts}


@dataclass
class BackfillResult:
    total: int
    done: int
    failures: List[str] = field(default_factory=list)
    provider_used: Dict[str, str] = field(default_factory=dict)
    checkpoint: Set[str] = field(default_factory=set)

    @property
    def pct(self) -> float:
        return round(100.0 * self.done / self.total, 2) if self.total else 100.0

    @property
    def complete(self) -> bool:
        return self.done >= self.total


def run_backfill(tickers: Sequence[str], data_types: Sequence[str], router: ProviderRouter,
                 checkpoint: Optional[Set[str]] = None,
                 progress_cb: Optional[Callable[[Dict], None]] = None) -> BackfillResult:
    """Paced, resumable backfill. Already-done ``checkpoint`` keys are skipped.

    Designed to run for hours/days on an always-free VM; emit ``progress_cb`` so a
    UI page can show accurate progress (docs P2.5 progress tracker).
    """
    checkpoint = set(checkpoint or set())
    units = [(t, dt) for t in tickers for dt in data_types]
    total = len(units)
    result = BackfillResult(total=total, done=0, checkpoint=checkpoint)
    result.done = len(checkpoint & {f"{t}:{dt}" for t, dt in units})

    for ticker, dt in units:
        key = f"{ticker}:{dt}"
        if key in checkpoint:
            continue
        res = router.get(ticker, dt)
        if res["complete"]:
            checkpoint.add(key)
            result.done += 1
            result.provider_used[key] = res["provider"]
        else:
            result.failures.append(key)
        if progress_cb:
            progress_cb({"done": result.done, "total": total, "pct": result.pct,
                         "current": key, "complete": res["complete"]})
    result.checkpoint = checkpoint
    return result
