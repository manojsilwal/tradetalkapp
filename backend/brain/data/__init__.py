"""Multi-source data layer: provider fallback + completeness gating + paced backfill.

See docs/finance-brain-architecture.html Section 04 (Hot/Warm/Cold pipeline) and
the Completeness Reviewer + rate-limit-aware loader.
"""
from .provider import (
    BackfillResult,
    CompletenessReviewer,
    ProviderAdapter,
    ProviderError,
    ProviderRouter,
    TokenBucket,
    run_backfill,
)

__all__ = [
    "BackfillResult",
    "CompletenessReviewer",
    "ProviderAdapter",
    "ProviderError",
    "ProviderRouter",
    "TokenBucket",
    "run_backfill",
]
