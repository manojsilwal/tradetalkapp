from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from enum import Enum

class VerificationStatus(str, Enum):
    PENDING = "PENDING"
    REJECTED = "REJECTED"
    VERIFIED = "VERIFIED"

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
    cash_reserves: List[CashReserveDataPoint]

class MetricDataPoint(BaseModel):
    current: str
    historical: str
    trend: str
    history: List[float] = []

class InvestorMetricsResponse(BaseModel):
    ticker: str
    metrics: Dict[str, MetricDataPoint]

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

