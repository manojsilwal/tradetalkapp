"""
query_router.py
----------------
Lightweight regex-based query router.

Classifies an incoming user query into one of four routing targets:
  - "sql"     → ticker + numeric data queries (route to SQL/data agent)
  - "rag"     → earnings, filings, transcripts (route to RAG/knowledge agent)
  - "python"  → calculation, chart, backtest requests (route to Python/code agent)
  - "general" → none of the above (default LLM response)

Design notes:
  - Ticker matching uses a CASE-SENSITIVE regex so plain English words
    like 'Hello' or 'annual' never match.
  - SQL keyword patterns are case-insensitive.
  - Priority: python > rag > sql > general.
    RAG beats SQL when explicit document signals appear, even with a ticker present.
"""
import re
import logging
from typing import Literal

logger = logging.getLogger(__name__)

RouteTarget = Literal["sql", "rag", "python", "general"]

# ── Ticker pattern (CASE-SENSITIVE) ────────────────────────────────────────────
# Only matches fully-uppercase tokens of 2-5 chars (AAPL, MSFT, JPM, etc.)
# Lookbehind/lookahead prevents partial matches inside longer words.
_TICKER_RE = re.compile(r"(?<![A-Za-z])[A-Z]{2,5}(?![A-Za-z])")

# ── SQL keyword patterns (case-insensitive) ──────────────────────────────────
_SQL_KW_RE = re.compile(
    r"\b("
    r"price|volume|pe ratio|p/e|price.to.earnings|market cap|eps|revenue|earnings per share"
    r"|debt|equity|roa|roe|ebitda|fcf|dividend"
    r"|52.week|ytd|year.to.date|all.time high|ath|float|shares outstanding"
    r"|top \d+|bottom \d+|rank|ranked|ranking|compare|vs\.?|versus"
    r"|screen|screener|filter|criteria|threshold"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

# ── RAG patterns (case-insensitive) ─────────────────────────────────────────
# NOTE: plain 'earnings' is NOT here — it needs a qualifier (transcript/call/report etc.)
# to avoid routing P/E ratio queries to RAG.
_RAG_RE = re.compile(
    r"\b("
    r"(?:earnings\s+(?:transcript|call|report|release|filing|guidance|surprise))"
    r"|transcript|filing|10-k|10-q|8-k|annual report|quarterly report"
    r"|press release|guidance|forward.looking|outlook|management commentary"
    r"|analyst note|research report"
    r"|risk factor|competitive landscape|moat|narrative|story"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

# ── Python / computation patterns (case-insensitive) ─────────────────────────
_PYTHON_RE = re.compile(
    r"\b("
    r"calculate|compute|run|execute|simulate|model|backtest|back.test"
    r"|chart|plot|graph|visualize|visualise|draw"
    r"|sharpe|cagr|drawdown|returns|correlation|regression|monte carlo"
    r"|write code|python|script|function|algo|algorithm|strategy"
    r"|what if|scenario|hypothetical|stress.test|sensitivity"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)


def _score(query: str):
    """Return (sql_score, rag_score, python_score) for a query."""
    tickers     = _TICKER_RE.findall(query)
    sql_kw      = _SQL_KW_RE.findall(query)
    rag_score   = len(_RAG_RE.findall(query))
    python_score = len(_PYTHON_RE.findall(query))

    # SQL score = ticker count + keyword count
    # BUT: if rag or python signals are present, a single lone ticker shouldn't
    # drown them out — discount to keyword-only score in that case.
    ticker_count = len(tickers)
    kw_count = len(sql_kw)

    if rag_score > 0 or python_score > 0:
        # Discount isolated ticker(s) competing vs strong semantic signals
        effective_tickers = ticker_count if ticker_count >= 2 else 0
    else:
        effective_tickers = ticker_count

    sql_score = kw_count + effective_tickers
    return sql_score, rag_score, python_score


def route_query(query: str) -> RouteTarget:
    """
    Classify a user query and return the appropriate routing target.

    Priority: python > rag > sql > general.
    RAG wins over SQL when explicit document/earnings signals appear,
    even if a ticker is also present in the query.
    """
    sql_score, rag_score, python_score = _score(query)

    logger.debug(
        "[QueryRouter] scores: sql=%d  rag=%d  python=%d  query=%r",
        sql_score, rag_score, python_score, query[:80],
    )

    if python_score > 0 and python_score >= rag_score:
        route: RouteTarget = "python"
    elif rag_score > 0:
        route = "rag"
    elif sql_score > 0:
        route = "sql"
    else:
        route = "general"

    logger.info("[QueryRouter] route=%s  query=%r", route, query[:60])
    return route


def route_query_detail(query: str) -> dict:
    """
    Return routing result with scores for debugging / observability.

    Returns:
        {
            "route": "sql" | "rag" | "python" | "general",
            "scores": {"sql": int, "rag": int, "python": int},
            "query_preview": str,
        }
    """
    sql_score, rag_score, python_score = _score(query)

    if python_score > 0 and python_score >= rag_score:
        route: RouteTarget = "python"
    elif rag_score > 0:
        route = "rag"
    elif sql_score > 0:
        route = "sql"
    else:
        route = "general"

    return {
        "route": route,
        "scores": {"sql": sql_score, "rag": rag_score, "python": python_score},
        "query_preview": query[:100],
    }
