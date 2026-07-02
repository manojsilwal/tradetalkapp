from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from enum import Enum

class VerificationStatus(str, Enum):
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    VERIFIED = "VERIFIED"


class FreshnessTier(str, Enum):
    """How fresh a value is *expected* to be for its data class."""
    LIVE = "live"            # real-time quote during an open session
    DELAYED = "delayed"      # intraday but lagged (e.g. ~15 min)
    EOD = "eod"              # end-of-day close for the last session
    HISTORICAL = "historical"  # historical series / derived analytics
    REFERENCE = "reference"  # static or slowly-changing reference data


class DataFreshness(BaseModel):
    """Provenance + freshness envelope attached to any data-bearing payload.

    Extends the truthful-data contract from "missing data" (InsufficientDataError)
    to "stale data": a value is either accompanied by this envelope proving it
    meets its data class's SLA, or it must not be rendered as a live number.
    See backend/freshness.py for the policy registry and ``assess()``.
    """
    data_class: str = Field(description="Policy key, e.g. live_quote, eod_movers, macro_fred.")
    source: str = Field(description="Origin, e.g. yfinance_live, yfinance_eod, fred, snapshot, heuristic.")
    tier: FreshnessTier = Field(default=FreshnessTier.EOD)
    as_of: Optional[str] = Field(default=None, description="ISO time/date the data is effective as of.")
    captured_at: Optional[str] = Field(default=None, description="ISO time the data was fetched.")
    expected_as_of: Optional[str] = Field(default=None, description="ISO anchor the value is compared against (e.g. last session).")
    is_stale: bool = Field(default=False, description="True if the value violates its data class SLA.")
    staleness_seconds: Optional[float] = Field(default=None, description="Age beyond expected, in seconds (or None if unknown).")
    degraded: bool = Field(default=False, description="True if a fallback/lower-confidence source was used.")
    policy_max_age_s: Optional[float] = Field(default=None, description="Max allowed age for this data class, in seconds.")
    note: Optional[str] = Field(default=None, description="Optional human-readable provenance note.")

class MarketRegime(str, Enum):
    BULL_NORMAL = "BULL_NORMAL"
    BULL_EXCESS = "BULL_EXCESS"
    BEAR_NORMAL = "BEAR_NORMAL"
    BEAR_STRESS = "BEAR_STRESS"
    K_SHAPE_DIVERGENCE = "K_SHAPE_DIVERGENCE"

class MarketState(BaseModel):
    """
    Represents the broader market environment, heavily weighting 2026 macro indicators.
    """
    credit_stress_index: float = Field(default=1.0, description="Credit stress. > 1.1 = Bearish")
    k_shape_spending_divergence: float = Field(default=0.0, description="Measures divergence between high and low income spending")
    polymarket_event_probabilities: Dict[str, float] = Field(default_factory=dict, description="Probabilities of macro events from Prediction Markets")
    market_regime: MarketRegime = Field(default=MarketRegime.BULL_NORMAL, description="Current market regime")

    def is_bearish(self) -> bool:
        """
        Constraint: Strictly follow the 2026 macro-economic indicators (Credit stress > 1.1 = Bearish).
        """
        return self.credit_stress_index > 1.1
class FactorResult(BaseModel):
    """
    Standard output from an Analyst-QA Pair representing a specific factor evaluation.
    """
    factor_name: str = Field(description="Name of the factor, e.g., 'Short Interest'")
    status: VerificationStatus = Field(default=VerificationStatus.PENDING, description="Verification status by QA")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0")
    rationale: str = Field(description="Detailed explanation of the findings and QA debate summary")
    trading_signal: int = Field(ge=-1, le=1, description="-1 = Bearish/Sell, 0 = Neutral, 1 = Bullish/Buy")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional debug or trace info")
    history: List[Dict[str, str]] = Field(default_factory=list, description="Debate trace between Analyst and QA")

class BrainVerdict(BaseModel):
    """Slim brain block surfaced on trace/debate/decision-terminal for UI display.

    Populated only when BRAIN_SERVE_ENABLE=1 and a snapshot exists.
    All fields are optional so the schema is forward-compatible; consumers
    must treat None as 'brain not available for this request'.
    """

    outperform_probability: Optional[float] = Field(
        default=None,
        description="0-1 probability the ticker outperforms the equal-weight market index.",
    )
    composite_score: Optional[float] = Field(
        default=None,
        description="0-1 aggregate brain model score.",
    )
    recommendation: Optional[str] = Field(
        default=None,
        description="Strong Buy / Buy / Hold / Sell / Strong Sell",
    )
    confidence_score: Optional[float] = Field(
        default=None,
        description="Live-adjusted model confidence (0-1).",
    )
    live_price: Optional[float] = Field(
        default=None,
        description="Live spot price used for Reflex re-inference.",
    )
    price_source: Optional[str] = Field(
        default=None,
        description="Source of the live price: 'spot', 'snapshot_base', 'unavailable', etc.",
    )
    signal_scores: Optional[Dict[str, float]] = Field(
        default=None,
        description="Per-signal sub-scores from the brain model.",
    )
    status: Optional[str] = Field(
        default=None,
        description="LIVE | BASE | STALE",
    )
    waterfall: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Base-vs-live explanation rows for UI waterfall chart.",
    )


class OptionsFlow(BaseModel):
    """EOD-style options flow aggregates from free multi-provider chain data."""

    total_call_volume: Optional[int] = None
    total_put_volume: Optional[int] = None
    total_call_oi: Optional[int] = None
    total_put_oi: Optional[int] = None
    put_call_volume_ratio: Optional[float] = None
    put_call_oi_ratio: Optional[float] = None
    iv_atm_call: Optional[float] = None
    iv_atm_put: Optional[float] = None
    iv_skew: Optional[float] = None
    unusual_contracts: List[Dict[str, Any]] = Field(default_factory=list)
    unusual_activity_score: Optional[float] = None
    net_premium_bias: Optional[str] = None
    source: Optional[str] = None
    as_of: Optional[str] = None
    partial: bool = False
    # Options intelligence (Google-parity layer)
    call_oi_pct: Optional[float] = None
    put_oi_pct: Optional[float] = None
    call_volume_pct: Optional[float] = None
    put_volume_pct: Optional[float] = None
    expected_move_usd: Optional[float] = None
    expected_move_pct: Optional[float] = None
    nearest_expiry: Optional[str] = None
    top_call_strikes: List[Dict[str, Any]] = Field(default_factory=list)
    top_put_strikes: List[Dict[str, Any]] = Field(default_factory=list)
    near_expiry_oi_pct: Optional[float] = None
    near_expiry_flag: bool = False
    iv_rank_proxy: Optional[float] = None
    oi_sentiment: Optional[str] = None
    volume_sentiment: Optional[str] = None
    narrative_summary: Optional[str] = None
    spot_price_usd: Optional[float] = None


class ShortInterestPanel(BaseModel):
    """Short positioning snapshot for Decision Terminal."""

    short_percent_float: Optional[float] = None
    days_to_cover: Optional[float] = None
    interpretation: Optional[str] = None
    source: str = "yfinance"


class SwarmConsensus(BaseModel):
    """
    The aggregated result from the Swarm Backend containing individual Factor traces
    and an overarching Swarm verdict.
    """
    ticker: str
    macro_state: MarketState
    global_signal: int
    global_verdict: str
    confidence: float
    consensus_rationale: str = ""
    factors: Dict[str, FactorResult]
    brain: Optional["BrainVerdict"] = Field(
        default=None,
        description="Brain live-blend block; None when brain serving is disabled.",
    )
    options: Optional["OptionsFlow"] = Field(
        default=None,
        description="Free multi-provider options flow aggregates; None when unavailable.",
    )

class SectorData(BaseModel):
    symbol: str
    name: str
    daily_change_pct: float
    
class ConsumerSpendingDataPoint(BaseModel):
    month: str
    value: float

class CashReserveDataPoint(BaseModel):
    month: str
    institutional_cash: float
    retail_cash: float

class CapitalFlowData(BaseModel):
    asset: str
    name: str
    category: str
    daily_change_pct: float


class CapitalFlowBucket(BaseModel):
    """Enriched per-bucket capital flow with reconciliation data and historical returns."""
    bucket_id: str
    proxy_symbol: str
    display_name: str
    stance: str  # 'risk_on' | 'safe_haven' | 'cash'
    region: str  # 'US' | 'INTL_COUNTERPARTY'
    is_us_destination: bool
    price_change_pct: float
    notional_base_usd: float
    component_flow_usd: float
    flow_direction: str  # 'inflow_to_us' | 'outflow_from_us' | 'intl_counterparty' | 'non_us_safe_haven' | 'intra_us'
    historical_returns: Dict[str, float] = Field(default_factory=dict, description="Period returns: 1d, 1w, 1m, 1y, 5y")


class CapitalFlowReconciliation(BaseModel):
    """Reconciliation summary proving components add up."""
    opening_capital_total_usd: float
    closing_capital_total_usd: float
    net_capital_change_usd: float
    components_sum_usd: float
    reconciliation_gap_usd: float
    is_reconciled: bool
    us_net_increased: bool
    tolerance_usd: float = 1.0


class CapitalFlowDriver(BaseModel):
    """A driver of capital inflow or outflow."""
    bucket_id: str
    proxy_symbol: str
    display_name: str
    component_flow_usd: float
    price_change_pct: float
    intl_counterparty_symbol: Optional[str] = None
    intl_index_change_pct: Optional[float] = None


class CapitalFlowExplanation(BaseModel):
    """Two-channel explanation of US net capital change."""
    us_net_increased: bool
    net_capital_change_usd: float
    drivers_inflow_to_us: List[CapitalFlowDriver] = Field(default_factory=list)
    drivers_outflow_from_us: List[CapitalFlowDriver] = Field(default_factory=list)
    reconciles_to: float
    is_reconciled: bool


class ReconciledCapitalFlows(BaseModel):
    """Full reconciled capital-flow picture for one day."""
    flow_date: str
    buckets: List[CapitalFlowBucket]
    reconciliation: CapitalFlowReconciliation
    explanation: CapitalFlowExplanation


class MacroDataResponse(BaseModel):
    """
    Dedicated global macroeconomic payload for the Macro Dashboard.
    """
    vix_level: float
    credit_stress_index: float
    market_regime: str
    sectors: List[SectorData]
    consumer_spending: List[ConsumerSpendingDataPoint]
    capital_flows: List[CapitalFlowData]
    reconciled_capital_flows: Optional[ReconciledCapitalFlows] = None
    cash_reserves: List[CashReserveDataPoint]
    usd_broad_index: Optional[float] = None
    usd_index_change_5d_pct: Optional[float] = None
    usd_strength_label: str = "unknown"
    dxy_level: Optional[float] = None
    dxy_change_5d_pct: Optional[float] = None
    dxy_strength_label: str = "unknown"
    treasury_2y: Optional[float] = None
    treasury_10y: Optional[float] = None
    yield_curve_spread_10y_2y: Optional[float] = None
    fed_funds_rate: Optional[float] = None
    cpi_yoy: Optional[float] = None
    unemployment_rate: Optional[float] = None
    macro_narrative: str = ""
    fred_fetched_at: Optional[str] = None
    data_freshness: Optional["DataFreshness"] = Field(
        default=None,
        description="Freshness/provenance envelope for the live VIX-derived indicators.",
    )

class MetricDataPoint(BaseModel):
    current: str
    historical: str
    trend: str
    history: List[float] = []

class InvestorMetricsResponse(BaseModel):
    ticker: str
    metrics: Dict[str, MetricDataPoint]
    market_cap: Optional[float] = Field(
        default=None,
        description="Latest market capitalization in USD when available from yfinance.",
    )
    cap_bucket: Optional[str] = Field(
        default=None,
        description="Mega/Large/Mid/Small/Micro cap classification for dashboard routing.",
    )
    data_freshness: Optional["DataFreshness"] = Field(
        default=None,
        description="Freshness envelope for yfinance fundamental metrics (slow-moving fields).",
    )


class SmallCapSignal(BaseModel):
    label: str = Field(description="Signal name, e.g. Profitability Runway")
    score: str = Field(description="green | yellow | red")
    headline: str = Field(description="One-sentence verdict")
    detail: str = Field(description="2-3 sentence elaboration")


class SmallCapStreamYear(BaseModel):
    year: str
    revenue_usd: Optional[float] = None
    gross_margin_pct: Optional[float] = None
    operating_margin_pct: Optional[float] = None


class SmallCapRevenueStream(BaseModel):
    name: str
    latest_share_pct: Optional[float] = Field(
        default=None,
        description="Approximate mix of total revenue in the latest year, when known.",
    )
    years: List[SmallCapStreamYear] = Field(default_factory=list)
    source: Optional[str] = Field(
        default=None,
        description="Where this stream's figures came from, when known.",
    )


class SmallCapMajorDeal(BaseModel):
    partner: str
    deal_type: str = Field(default="partnership")
    amount_usd: Optional[float] = None
    amount_label: str = Field(default="Undisclosed")
    year: Optional[int] = None
    summary: str = ""
    predictability_note: str = Field(
        default="",
        description="Why this deal improves revenue visibility or predictability.",
    )
    source: Optional[str] = Field(
        default=None,
        description="Where this deal was found, when known.",
    )


class SmallCapAssessment(BaseModel):
    ticker: str
    cap_bucket: str
    signals: List[SmallCapSignal]
    overall_verdict: str = Field(description="Compelling | Watch | Avoid")
    overall_rationale: str
    revenue_streams: List[SmallCapRevenueStream] = Field(default_factory=list)
    major_deals: List[SmallCapMajorDeal] = Field(default_factory=list)

class MacroAlert(BaseModel):
    id: str
    title: str
    summary: str
    urgency: int = Field(ge=1, le=10)
    urgency_label: str
    affected_sectors: List[str]
    source: str
    source_reliability: str
    source_reliability_score: float
    link: str = ""
    timestamp: float
    is_read: bool = False

class AlertResponse(BaseModel):
    alerts: List[MacroAlert]
    total: int
    unread: int


# ── AI Debate Models ──────────────────────────────────────────────────────────

class AgentStance(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class DebateArgument(BaseModel):
    agent_role: str
    agent_icon: str
    stance: AgentStance
    headline: str
    key_points: List[str]
    supporting_data: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    degraded: bool = False


class DebateResult(BaseModel):
    ticker: str
    arguments: List[DebateArgument]
    verdict: str
    consensus_confidence: float
    moderator_summary: str
    bull_score: int
    bear_score: int
    neutral_score: int
    quality_warning: Optional[str] = None
    degraded_roles: List[str] = Field(default_factory=list)


# ── Strategy Backtesting Models ───────────────────────────────────────────────

class FilterRule(BaseModel):
    metric: str                     # e.g. "forward_pe", "revenue_growth_yoy"
    op: str                         # ">", "<", ">=", "<="
    value: float


class StrategyRules(BaseModel):
    name: str
    description: str
    filters: List[FilterRule]                                         # BUY conditions
    sell_filters: List[FilterRule] = Field(default_factory=list)     # SELL conditions (event-driven)
    holding_period_months: int = 12
    rebalance_months: int = 12
    universe: List[str] = Field(default_factory=list)
    start_date: str
    end_date: str
    strategy_type: str = "fundamental"   # "fundamental" | "momentum" | "mixed"
    # Optional ranking (Fama-French, momentum, low-vol, etc.) — when set, picks top-N by metric after filters
    rank_by_metric: Optional[str] = None
    rank_higher_is_better: bool = True
    select_top_n: int = 30
    strategy_category: str = "custom"    # Factor, Macro, Value, Momentum, Blended, Income, Quality
    preset_id: Optional[str] = None
    survivorship_note: str = (
        "Universe uses a liquid S&P 500 subset (~40 names) for Yahoo rate limits; "
        "results are illustrative, not full-index reproduction."
    )


class BacktestAction(BaseModel):
    action: str                      # "BUY" | "SELL" | "HOLD_CASH"
    ticker: str
    date: str
    price: float
    shares: float = 0.0              # number of shares bought/sold
    position_value: float = 0.0      # shares × price
    profit_loss_dollars: float = 0.0 # realised P&L in dollars (SELL only)
    reason: str
    return_pct: float = 0.0          # % return on the position (SELL only)
    portfolio_value_after: float = 0.0  # total portfolio value after this action


class BacktestReflection(BaseModel):
    hypothesis: str
    outcome: str
    market_regime: str
    drawdown_bucket: str
    adjustment: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    effectiveness_score: float = Field(ge=0.0, le=1.0, default=0.5)


class RetrievalTelemetry(BaseModel):
    retrieved_docs_count: int = 0
    reflection_hits: int = 0
    retrieved_reflection_ids: List[str] = Field(default_factory=list)


class BacktestResult(BaseModel):
    strategy: StrategyRules
    actions: List[BacktestAction]
    initial_investment: float = 10000.0
    final_value: float = 0.0
    total_return_pct: float = 0.0
    total_return_dollars: float = 0.0
    cagr: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    total_trades: int
    benchmark_cagr: float
    outperformed: bool
    best_period: str
    worst_period: str
    portfolio_value_series: List[Dict[str, Any]]  # [{date, value}, ...]
    benchmark_value_series: List[Dict[str, Any]]  # [{date, value}, ...]
    ai_explanation: str
    reflection: BacktestReflection
    retrieval_telemetry: RetrievalTelemetry = Field(default_factory=RetrievalTelemetry)
    knowledge_context: str
    data_freshness: Optional["DataFreshness"] = None


# ── Gold Advisor (investor snapshot, not real-time trading) ───────────────────

class GoldAdvisorBriefing(BaseModel):
    """LLM synthesis over deterministic context."""

    directional_bias: str = Field(
        description="constructive | neutral | caution — allocation tone, not a trade signal",
    )
    summary: str
    key_drivers: List[str] = Field(default_factory=list)
    levels_to_watch: str = ""
    risk_factors: List[str] = Field(default_factory=list)
    confidence_0_1: float = Field(ge=0.0, le=1.0, default=0.5)


class GoldAdvisorResponse(BaseModel):
    """Full API payload: facts + narrative."""

    context: Dict[str, Any]
    briefing: Dict[str, Any]
    data_freshness: Optional["DataFreshness"] = None


# ── K2 Investor Decision Terminal (glanceable view-model) ─────────────────────

class TerminalFieldProvenance(BaseModel):
    """Honest sourcing for a single figure or widget."""

    source: str = Field(default="", description="e.g. yfinance, polymarket_gamma, debate_llm, heuristic")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    formula_or_note: str = ""
    missing_reason: Optional[str] = None


class TerminalValuationModel(BaseModel):
    name: str
    fair_value_usd: Optional[float] = None
    available: bool = True
    provenance: TerminalFieldProvenance
    momentum_score: Optional[float] = Field(
        default=None,
        description="0-100 composite momentum pricing score when name is Momentum",
    )
    momentum_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Full momentum readout dict for UI rendering",
    )
    scenarios: Optional[Dict[str, float]] = Field(
        default=None,
        description="Optional bear/base/bull (+market_implied) fair values (e.g. DCF scenario range)",
    )
    classification: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Business archetype classification driving the DCF model (V2)",
    )
    implied_growth: Optional[float] = Field(
        default=None,
        description="Reverse-DCF growth (decimal) the current price embeds (flat over horizon)",
    )
    implied_growth_3y: Optional[float] = Field(
        default=None,
        description="Reverse-DCF high-growth-phase rate (decimal) if held ~3y then faded",
    )
    implied_growth_5y: Optional[float] = Field(
        default=None,
        description="Reverse-DCF high-growth-phase rate (decimal) if held ~5y then faded",
    )
    implied_margin: Optional[float] = Field(
        default=None,
        description="Reverse-DCF operating margin (decimal) implied by price (FCFF models)",
    )
    implied_roic: Optional[float] = Field(
        default=None,
        description="Reverse-DCF ROIC (decimal) implied by price (FCFF models)",
    )
    dcf_tiers: Optional[Dict[str, Optional[float]]] = Field(
        default=None,
        description="Five-tier DCF sensitivity ladder (bear/conservative_base/base/bull/extreme_bull)",
    )
    valuation_range: Optional[List[float]] = Field(
        default=None,
        description="[low, high] fair-value range across scenarios",
    )
    margin_of_safety_pct: Optional[float] = Field(
        default=None,
        description="(base − price) / base × 100; positive = undervalued",
    )
    market_expectation: Optional[str] = Field(
        default=None,
        description="Plain-language read of what the price implies vs base case",
    )
    risk_flags: List[str] = Field(
        default_factory=list,
        description="Model risk flags (e.g. capex_inefficiency, terminal_value_high)",
    )


class MomentumReadout(BaseModel):
    """Structured momentum model output (research / decision-support only)."""

    ticker: str
    as_of_date: str
    momentum_pricing_score: float
    downside_exposure_score: float
    decision_quality_score: float
    classification: str
    crash_risk: str
    subscores: Dict[str, float] = Field(default_factory=dict)
    downside: Dict[str, Any] = Field(default_factory=dict)
    risk_flags: List[str] = Field(default_factory=list)
    agent_summary: str = ""
    partial_mode: bool = False
    latest_price_used: Optional[float] = None
    model_read: Optional[str] = None
    component_breakdown: List[Dict[str, Any]] = Field(default_factory=list)
    technical_positioning: List[Dict[str, Any]] = Field(default_factory=list)
    risk_flags_active: List[str] = Field(default_factory=list)
    risk_flags_clear: List[str] = Field(default_factory=list)
    final_agent_narrative: Optional[str] = None


class AnalystConsensus(BaseModel):
    """Wall St / Yahoo analyst price-target consensus (via FinCrawler)."""

    mean_target_usd: Optional[float] = None
    high_target_usd: Optional[float] = None
    low_target_usd: Optional[float] = None
    median_target_usd: Optional[float] = None
    num_analysts: Optional[int] = None
    recommendation_mean: Optional[float] = None
    recommendation_key: Optional[str] = None
    source: str = ""
    street_vs_price_pct: Optional[float] = Field(
        default=None,
        description="(mean_target − price) / price × 100",
    )
    our_vs_street_pct: Optional[float] = Field(
        default=None,
        description="(our_base_fair − mean_target) / mean_target × 100",
    )
    divergence_flag: bool = False
    provenance: TerminalFieldProvenance = Field(default_factory=TerminalFieldProvenance)


class TerminalValuationPanel(BaseModel):
    current_price_usd: Optional[float] = None
    average_fair_value_usd: Optional[float] = None
    pct_vs_average: Optional[float] = Field(
        default=None,
        description="Margin of safety: (fair − price) / fair × 100; positive = undervalued",
    )
    valuation_gap_pct: Optional[float] = Field(
        default=None,
        description="(price − fair) / fair × 100; positive = trading above fair value",
    )
    implied_downside_pct: Optional[float] = Field(
        default=None,
        description="(fair − price) / price × 100; negative when price exceeds fair value",
    )
    valuation_signal: str = Field(
        default="",
        description="Graduated label, e.g. Moderately Overvalued",
    )
    valuation_confidence: str = Field(
        default="",
        description="Low / Medium / High model agreement and coverage",
    )
    composite_signal: str = Field(
        default="",
        description="Reconciled valuation + momentum verdict (e.g. attractive but momentum weak)",
    )
    dcf_range_low_usd: Optional[float] = None
    dcf_range_high_usd: Optional[float] = None
    dcf_tiers: Optional[Dict[str, Optional[float]]] = Field(
        default=None,
        description="Five-tier DCF sensitivity ladder (bear/conservative_base/base/bull/extreme_bull)",
    )
    bull_case_assessment: str = ""
    bear_case_assessment: str = ""
    gauge_label: str = ""
    business_classification: Optional[str] = Field(
        default=None,
        description="Top business archetype (e.g. platform_reinvestment_supercycle)",
    )
    market_expectation: Optional[str] = Field(
        default=None,
        description="Plain-language read of what the price implies vs the DCF base case",
    )
    implied_growth_3y: Optional[float] = Field(
        default=None,
        description="Reverse-DCF high-growth-phase rate (decimal) if held ~3y then faded",
    )
    implied_growth_5y: Optional[float] = Field(
        default=None,
        description="Reverse-DCF high-growth-phase rate (decimal) if held ~5y then faded",
    )
    risk_flags: List[str] = Field(
        default_factory=list,
        description="Aggregated DCF model risk flags",
    )
    analyst_consensus: Optional[AnalystConsensus] = None
    models: List[TerminalValuationModel] = Field(default_factory=list)
    panel_note: str = ""


class MetricHealthAssessment(BaseModel):
    tone: str = Field(default="neutral", description="positive | neutral | caution | negative")
    label: str = ""
    detail: str = ""


class FundamentalHealthPanel(BaseModel):
    headline: str = ""
    tone: str = "neutral"
    summary: str = ""
    macro_regime: str = ""
    macro_note: str = ""
    coverage_pct: Optional[float] = None


class TerminalQualityRow(BaseModel):
    id: str
    label: str
    value_label: str
    status_label: str = ""
    provenance: TerminalFieldProvenance
    assessment_tone: str = ""
    assessment_label: str = ""
    assessment_detail: str = ""


class TerminalQualityPanel(BaseModel):
    rows: List[TerminalQualityRow] = Field(default_factory=list)
    fundamental_health: Optional[FundamentalHealthPanel] = None


class TerminalVerdictPanel(BaseModel):
    headline_verdict: str
    debate_verdict: str
    swarm_verdict: str
    fusion_note: str = ""
    debate_stance_bull_pct: Optional[float] = Field(
        default=None, description="0-100, bull_score / total stances only"
    )
    debate_confidence_pct: Optional[float] = Field(
        default=None, description="0-100, moderator consensus_confidence only"
    )
    expert_bullish_pct: Optional[float] = Field(
        default=None,
        description="DEPRECATED: 0.5*stance + 0.5*confidence. Use split fields.",
    )
    prediction_market_bullish_pct: Optional[float] = None
    prediction_market_event_title: Optional[str] = None
    polymarket_relevance_score: Optional[float] = Field(
        default=None, description="0-1 internal gate; None if no event"
    )
    polymarket_gated_out: bool = False


class SpotEnvelope(BaseModel):
    price_usd: float
    source: str
    captured_at_utc: str
    degraded: bool
    momentum_anchor_usd: Optional[float] = None


class ReconciliationSignal(BaseModel):
    source: str
    label: str
    tone: str
    detail: str = ""


class TerminalReconciliationPanel(BaseModel):
    primary_headline: str
    supporting_signals: List[ReconciliationSignal] = Field(default_factory=list)
    conflicting_signals: List[ReconciliationSignal] = Field(default_factory=list)
    reconciliation_note: str = ""


class TerminalScorecardSummary(BaseModel):
    """Slim scorecard row for reconciliation + dashboard; not a comparative rating."""

    ticker: str
    preset: str = "balanced"
    is_comparative: bool = False
    ratio: float
    signal: str
    action: str
    verdict: str
    quadrant: str
    return_score_weighted: float
    risk_score_weighted: float
    framing_note: str = (
        "Single-name preview (balanced preset). Not a buy/sell rating — "
        "compare multiple tickers on /scorecard for relative rankings."
    )
    one_line_reason: str = ""
    data_freshness: Optional["DataFreshness"] = None


class HorizonQuantileBand(BaseModel):
    """Predictor fan-chart slice — q10–q90 approximates an 80 % interval."""

    horizon: str = ""
    q10_usd: Optional[float] = None
    q50_usd: Optional[float] = None
    q90_usd: Optional[float] = None
    point_usd: Optional[float] = None


class NarrativeScenarioCase(BaseModel):
    thesis: str = ""
    key_assumption: str = ""
    price_implied_usd: Optional[float] = None


class NarrativeScenariosPanel(BaseModel):
    bull: Optional[NarrativeScenarioCase] = None
    base: Optional[NarrativeScenarioCase] = None
    bear: Optional[NarrativeScenarioCase] = None


class RiskMatrixPanel(BaseModel):
    valuation: str = "Moderate"
    execution: str = "Moderate"
    cyclical: str = "Moderate"
    competitive: str = "Moderate"
    balance_sheet: str = "Moderate"
    regulatory: str = "Moderate"


class FilingIntelligencePanel(BaseModel):
    available: bool = False
    demand_visibility_summary: Optional[str] = None
    order_backlog_usd: Optional[float] = None
    backlog_growth_yoy_pct: Optional[float] = None
    book_to_bill_ratio: Optional[float] = None
    recurring_revenue_pct: Optional[float] = None
    primary_moat_driver: Optional[str] = None
    customer_concentration_note: Optional[str] = None
    thematic_tags: List[str] = Field(default_factory=list)
    source: Optional[str] = None
    stale: bool = False


class InvestmentSurfacePanel(BaseModel):
    investment_score: Optional[float] = None
    stance: Optional[str] = None
    evidence_coverage_pct: Optional[float] = None
    max_allowed_stance: Optional[str] = None
    stance_reason: Optional[str] = None


class TerminalRoadmapPanel(BaseModel):
    bull_price_usd: Optional[float] = None
    base_price_usd: Optional[float] = None
    bear_price_usd: Optional[float] = None
    predicted_cagr_base_pct: Optional[float] = None
    assumptions: List[str] = Field(default_factory=list)
    confidence_0_1: float = Field(default=0.0, ge=0.0, le=1.0)
    used_heuristic_fallback: bool = False
    provenance: TerminalFieldProvenance = Field(default_factory=TerminalFieldProvenance)
    horizon_quantile_bands: List[HorizonQuantileBand] = Field(
        default_factory=list,
        description="Optional multi-horizon q10/q50/q90 bands from the probabilistic predictor.",
    )
    predictor_synthesis_excerpt: Optional[str] = Field(
        default=None,
        description="Short synthesis text when the predictor supplied narrative.",
    )
    predictor_reviewer_excerpt: Optional[str] = Field(
        default=None,
        description="Reviewer check when predictor narrative path ran.",
    )


class DecisionTerminalPayload(BaseModel):
    """
    UI-ready payload for the K2 Investor Decision Terminal.
    Illustrative only — not investment advice; see disclaimer.
    """

    ticker: str
    disclaimer: str
    generated_at_utc: str
    cache_ttl_seconds: int = 300
    verdict_captured_at_utc: Optional[str] = Field(
        default=None,
        description="When the LLM verdict pipeline last ran (may differ from spot overlay on cache hits).",
    )
    verdict_from_cache: bool = Field(
        default=False,
        description="True when swarm/debate/verdict were served from the per-trading-day cache.",
    )
    macro_fetched_at_utc: Optional[str] = Field(
        default=None,
        description="When macro inputs (FRED/VIX) were captured for this verdict.",
    )
    valuation: TerminalValuationPanel
    quality: TerminalQualityPanel
    verdict: TerminalVerdictPanel
    roadmap: TerminalRoadmapPanel
    swarm: Optional[SwarmConsensus] = Field(
        default=None,
        description="Full swarm consensus used to build this terminal; lets the dashboard render the Trace tab from one call.",
    )
    debate: Optional[DebateResult] = Field(
        default=None,
        description="Full debate result used to build this terminal; lets the dashboard render the Debate tab from one call.",
    )
    market_data_degraded: bool = Field(
        default=False,
        description="True when Yahoo history/momentum may be incomplete (fallback spot used).",
    )
    spot_price_source: Optional[str] = Field(
        default=None,
        description="yfinance_history | yfinance_info | stooq | fincrawler | merged_from_info",
    )
    provider_audit: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "When requested (provider_audit / audit=1), maps each Decision Terminal block to "
            "upstream data families: yfinance, Stooq, FinCrawler, Polymarket, heuristics, LLM, data lake."
        ),
    )
    data_freshness: Optional["DataFreshness"] = Field(
        default=None,
        description="Freshness/provenance envelope for the spot price (folds market_data_degraded + spot_price_source).",
    )
    spot: Optional[SpotEnvelope] = Field(
        default=None,
        description="Canonical spot price envelope; valuation.current_price_usd mirrors spot.price_usd.",
    )
    scorecard_summary: Optional[TerminalScorecardSummary] = None
    reconciliation: Optional[TerminalReconciliationPanel] = None
    brain: Optional["BrainVerdict"] = Field(
        default=None,
        description=(
            "Brain live-blend block for the decision terminal; None when brain serving "
            "is disabled (BRAIN_SERVE_ENABLE=0) or no snapshot exists for this ticker."
        ),
    )
    options: Optional["OptionsFlow"] = Field(
        default=None,
        description="Options flow aggregates; None when all providers failed or disabled.",
    )
    filing_intelligence: Optional[FilingIntelligencePanel] = None
    risk_matrix: Optional[RiskMatrixPanel] = None
    narrative_scenarios: Optional[NarrativeScenariosPanel] = None
    investment_surface: Optional[InvestmentSurfacePanel] = None


class DecisionSnapshotPayload(BaseModel):
    """Fast slice: valuation + quality + spot (no swarm/debate/roadmap LLM)."""

    ticker: str
    disclaimer: str
    generated_at_utc: str
    cache_ttl_seconds: int = 300
    slice_from_cache: bool = False
    valuation: TerminalValuationPanel
    quality: TerminalQualityPanel
    market_data_degraded: bool = False
    spot_price_source: Optional[str] = None
    data_freshness: Optional["DataFreshness"] = None
    spot: Optional[SpotEnvelope] = None
    scorecard_summary: Optional[TerminalScorecardSummary] = None
    filing_intelligence: Optional[FilingIntelligencePanel] = None
    risk_matrix: Optional[RiskMatrixPanel] = None
    narrative_scenarios: Optional[NarrativeScenariosPanel] = None
    short_interest: Optional[ShortInterestPanel] = None


class DecisionSwarmPayload(BaseModel):
    """Medium slice: swarm consensus + polymarket-gated partial verdict (debate pending)."""

    ticker: str
    generated_at_utc: str
    cache_ttl_seconds: int = 300
    slice_from_cache: bool = False
    macro_fetched_at_utc: Optional[str] = None
    swarm: SwarmConsensus
    verdict: TerminalVerdictPanel = Field(
        description="Swarm + prediction-market fields only; debate stance fills in via /debate slice.",
    )


class DecisionVerdictPayload(BaseModel):
    """Slow slice: fused verdict + embedded swarm/debate for Trace/Debate tabs."""

    ticker: str
    generated_at_utc: str
    cache_ttl_seconds: int = 300
    slice_from_cache: bool = False
    verdict_captured_at_utc: Optional[str] = None
    macro_fetched_at_utc: Optional[str] = None
    verdict: TerminalVerdictPanel
    swarm: SwarmConsensus
    debate: DebateResult
    brain: Optional["BrainVerdict"] = None
    options: Optional["OptionsFlow"] = None
    investment_surface: Optional[InvestmentSurfacePanel] = None


class DecisionRoadmapPayload(BaseModel):
    """Roadmap slice: 3Y scenario prices (predictor-first, heuristic fallback)."""

    ticker: str
    generated_at_utc: str
    cache_ttl_seconds: int = 300
    slice_from_cache: bool = False
    roadmap: TerminalRoadmapPanel
    current_price_usd: Optional[float] = Field(
        default=None,
        description="Spot used to anchor roadmap scenarios (may differ from live overlay).",
    )
