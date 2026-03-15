"""
Price Movements Connector — daily top S&P 500 movers (gainers & losers).
Fetches the top 20 gainers and losers by % change using yFinance.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Representative S&P 500 subset — liquid, diverse, covers most sectors
_UNIVERSE = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "JPM", "JNJ", "V",
    "PG", "UNH", "HD", "MA", "BAC", "ABBV", "PFE", "AVGO", "KO", "PEP",
    "COST", "MRK", "TMO", "WMT", "CSCO", "ABT", "ACN", "CVX", "LLY", "MCD",
    "DHR", "NEE", "NKE", "TXN", "AMD", "PM", "ORCL", "IBM", "CRM", "QCOM",
    "HON", "AMGN", "LIN", "SBUX", "INTU", "GS", "BLK", "SPGI", "CAT", "BA",
    "AXP", "MS", "RTX", "ISRG", "ADI", "GILD", "TJX", "BKNG", "NOW", "DE",
    "SYK", "ZTS", "CI", "USB", "MO", "REGN", "VRTX", "HCA", "EOG", "SLB",
    "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "AMT", "PLD", "EQIX", "XOM",
    "F", "GM", "UBER", "PYPL", "SHOP", "WFC", "C", "PNC", "TFC", "COF",
]

TOP_N = 20  # top N gainers + top N losers


async def fetch_top_movers() -> list[dict]:
    """
    Returns up to 2*TOP_N entries: gainers first, then losers.
    Each entry: {ticker, change_pct, volume_ratio, sector, context, direction}
    """
    return await asyncio.to_thread(_sync_fetch)


def _sync_fetch() -> list[dict]:
    try:
        import yfinance as yf
        tickers_obj = yf.Tickers(" ".join(_UNIVERSE))
        today = str(datetime.now(timezone.utc).date())

        results = []
        for ticker in _UNIVERSE:
            try:
                t = tickers_obj.tickers.get(ticker.upper())
                if not t:
                    continue
                info = t.fast_info
                if info is None:
                    continue

                # Fast info gives us current/previous close
                current = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
                prev    = getattr(info, "previous_close", None) or getattr(info, "regularMarketPreviousClose", None)
                if not current or not prev or prev == 0:
                    continue

                change_pct = ((current - prev) / prev) * 100
                volume      = getattr(info, "three_month_average_volume", None) or 1
                day_volume  = getattr(info, "last_volume", None) or 1
                volume_ratio = round(day_volume / volume, 2) if volume else 1.0

                full_info = t.info or {}
                sector = full_info.get("sector", "Unknown")

                context = _build_context(ticker, change_pct, full_info)

                results.append({
                    "ticker": ticker,
                    "change_pct": round(change_pct, 2),
                    "volume_ratio": volume_ratio,
                    "sector": sector,
                    "context": context,
                    "direction": "gainer" if change_pct >= 0 else "loser",
                    "current_price": round(current, 2),
                    "date": today,
                })
            except Exception:
                continue

        # Sort by absolute change and return top gainers + top losers
        gainers = sorted([r for r in results if r["change_pct"] >= 0], key=lambda x: x["change_pct"], reverse=True)[:TOP_N]
        losers  = sorted([r for r in results if r["change_pct"] < 0],  key=lambda x: x["change_pct"])[:TOP_N]
        return gainers + losers

    except Exception as e:
        logger.warning(f"[PriceMovementsConnector] Failed: {e}")
        return []


def _build_context(ticker: str, change_pct: float, info: dict) -> str:
    parts = []
    high_52 = info.get("fiftyTwoWeekHigh")
    low_52  = info.get("fiftyTwoWeekLow")
    price   = info.get("currentPrice") or info.get("regularMarketPrice")

    if high_52 and price:
        pct_from_high = ((price - high_52) / high_52) * 100
        if pct_from_high > -5:
            parts.append("Near 52-week high")
        elif pct_from_high < -30:
            parts.append("Far below 52-week high")

    if low_52 and price:
        pct_from_low = ((price - low_52) / low_52) * 100
        if pct_from_low < 10:
            parts.append("Near 52-week low")

    pe = info.get("trailingPE")
    if pe and pe > 0:
        parts.append(f"P/E {round(pe, 1)}")

    return ". ".join(parts) if parts else ""
