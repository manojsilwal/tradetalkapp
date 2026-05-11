"""Predictor-specific errors."""


class PredictorDisabled(RuntimeError):
    """Kill-switch or backend=none."""


class PredictorDegraded(RuntimeError):
    """Service unavailable, stale data, or cost cap — caller may fall back."""
