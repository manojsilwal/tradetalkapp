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


class TerminalValuationPanel(BaseModel):
    current_price_usd: Optional[float] = None
    average_fair_value_usd: Optional[float] = None
    pct_vs_average: Optional[float] = Field(
        default=None,
        description="Positive = stock trades below average fair value (undervalued)",
    )
    gauge_label: str = ""
    models: List[TerminalValuationModel] = Field(default_factory=list)
    panel_note: str = ""


class TerminalQualityRow(BaseModel):
    id: str
    label: str
    value_label: str
    status_label: str = ""
    provenance: TerminalFieldProvenance


class TerminalQualityPanel(BaseModel):
    rows: List[TerminalQualityRow] = Field(default_factory=list)


class TerminalVerdictPanel(BaseModel):
    headline_verdict: str
    debate_verdict: str
    swarm_verdict: str
    fusion_note: str = ""
    expert_bullish_pct: Optional[float] = Field(
        default=None, description="0-100, from debate stance mix + confidence"
    )
    prediction_market_bullish_pct: Optional[float] = None
    prediction_market_event_title: Optional[str] = None
    polymarket_relevance_score: Optional[float] = Field(
        default=None, description="0-1 internal gate; None if no event"
    )
    polymarket_gated_out: bool = False


class HorizonQuantileBand(BaseModel):
    """Predictor fan-chart slice — q10–q90 approximates an 80 % interval."""

    horizon: str = ""
    q10_usd: Optional[float] = None
    q50_usd: Optional[float] = None
    q90_usd: Optional[float] = None
    point_usd: Optional[float] = None


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
