"""
Shared application dependencies — singletons, connectors, and mutable state.

Routers import from here instead of from main.py to avoid circular imports.
"""
from . import env_bootstrap  # noqa: F401 — load backend/.env before os.environ reads below

from .connectors import (
    ShortsConnector, SocialSentimentConnector, MacroHealthConnector,
    PolymarketConnector, FundamentalsConnector, InvestorMetricsConnector,
    NewsScannerConnector,
)
from .notification_agents import NotificationPipeline
from .knowledge_store import get_knowledge_store
from .llm_client import get_llm_client
from .tool_registry import registry as tool_registry
from .resource_registry import get_resource_registry
from . import alert_store as db
from . import user_progress as up

shorts_connector = ShortsConnector()
social_connector = SocialSentimentConnector()
macro_connector = MacroHealthConnector()
poly_connector = PolymarketConnector()
fund_connector = FundamentalsConnector()
investor_metrics_connector = InvestorMetricsConnector()
news_scanner = NewsScannerConnector()
notification_pipeline = NotificationPipeline()

sse_clients: list = []
last_trace_data: dict = {}

knowledge_store = get_knowledge_store()
llm_client = get_llm_client()
resource_registry = get_resource_registry()
