"""
Phase 5 — Generate condensed RAG summaries from raw Parquet data.

Reads locally stored Parquet files and produces text documents suitable for
vector storage. Four summary types:

  5a. Stock Profiles — one per ticker, 15-year narrative
  5b. Earnings Memories — one per earnings event with price reaction
  5c. Price Pattern Memories — notable moves (>3% or >2x volume)
  5d. Macro Regime Snapshots — one per quarter

Summaries can optionally be LLM-enhanced (via OpenRouter / Nemotron) or
generated with pure data templates (zero API cost).

Usage:
    python -m backend.data_lake.summarize_for_rag --dry-run
    python -m backend.data_lake.summarize_for_rag --tickers AAPL,MSFT
    python -m backend.data_lake.summarize_for_rag --no-llm   # template only
    python -m backend.data_lake.summarize_for_rag
"""
import argparse
import asyncio
import json
import logging
import os

import pandas as pd

from . import config
from . import checkpoint

logger = logging.getLogger(__name__)
PHASE_PROFILES = "summarize_profiles"
PHASE_EARNINGS = "summarize_earnings"
PHASE_PATTERNS = "summarize_patterns"
PHASE_MACRO = "summarize_macro"


def _macro_context_for_timestamp(ts: pd.Timestamp) -> str:
    """Attach coarse macro regime text from quarterly parquet when available."""
    macro_path = os.path.join(config.MACRO_DIR, "macro_quarterly.parquet")
    if not os.path.exists(macro_path):
        return ""
    try:
        q = pd.read_parquet(macro_path)
        if q.empty:
            return ""
        raw_ts = pd.Timestamp(ts)
        tsn = raw_ts.tz_localize(None) if raw_ts.tzinfo else raw_ts
        best_diff = None
        best_row = None
        for idx, row in q.iterrows():
            d = pd.Timestamp(idx)
            if d.tzinfo:
                d = d.tz_localize(None)
            diff = abs((tsn - d).days)
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_row = row
        if best_row is None or best_diff is None or best_diff > 120:
            return ""
        bits = []
        v = best_row.get("vix")
        if v is not None and not pd.isna(v):
            bits.append(f"VIX near {float(v):.1f}")
        fr = best_row.get("fed_funds_rate")
        if fr is not None and not pd.isna(fr):
            bits.append(f"Fed funds ~{float(fr):.2f}%")
        if bits:
            return "Macro backdrop (quarterly file): " + "; ".join(bits) + "."
    except Exception:
        return ""
    return ""


async def _polish_summary_batch(items: list[dict], llm) -> None:
    """LLM-polish stock_profiles and earnings_memory documents in place."""
    if not items or llm is None:
        return
    sem = asyncio.Semaphore(max(1, int(os.environ.get("RAG_LLM_CONCURRENCY", "3"))))

    async def one(entry: dict) -> None:
        async with sem:
            label = entry.get("metadata", {}).get("type", "summary")
            polished = await llm.generate_rag_polish(label, entry.get("document", ""))
            if polished and len(polished) > 40:
                entry["document"] = polished

    await asyncio.gather(*(one(s) for s in items))


# ── 5a. Stock Profile Summaries ───────────────────────────────────────────────

def _build_stock_profile(ticker: str) -> str | None:
    """Build a data-driven stock profile from prices + fundamentals."""
    price_path = os.path.join(config.PRICES_DIR, f"{ticker}.parquet")
    fund_path = os.path.join(config.FUNDAMENTALS_DIR, f"{ticker}.parquet")

    parts = [f"{ticker} 15-Year Profile:"]

    if os.path.exists(price_path):
        prices = pd.read_parquet(price_path)
        if not prices.empty and "Close" in prices.columns:
            first_price = prices["Close"].iloc[0]
            last_price = prices["Close"].iloc[-1]
            total_return = ((last_price / first_price) - 1) * 100
            max_drawdown = ((prices["Close"] / prices["Close"].cummax()) - 1).min() * 100
            avg_volume = prices["Volume"].mean() if "Volume" in prices.columns else 0
            parts.append(
                f"Price moved from ${first_price:.2f} to ${last_price:.2f} "
                f"({total_return:+.1f}% total return). "
                f"Max drawdown: {max_drawdown:.1f}%. "
                f"Average daily volume: {avg_volume:,.0f}."
            )
            if "daily_return_pct" in prices.columns:
                big_drops = (prices["daily_return_pct"] < -5).sum()
                big_rallies = (prices["daily_return_pct"] > 5).sum()
                parts.append(
                    f"Had {big_drops} days with >5% drops and {big_rallies} days with >5% rallies."
                )

    if os.path.exists(fund_path):
        fund = pd.read_parquet(fund_path)
        if not fund.empty:
            if "Total Revenue" in fund.columns:
                rev = fund["Total Revenue"].dropna()
                if len(rev) >= 2:
                    first_rev = rev.iloc[0]
                    last_rev = rev.iloc[-1]
                    parts.append(f"Revenue: ${first_rev/1e9:.1f}B -> ${last_rev/1e9:.1f}B.")
            if "net_margin" in fund.columns:
                margin = fund["net_margin"].dropna()
                if not margin.empty:
                    parts.append(f"Net margin range: {margin.min():.1%} to {margin.max():.1%}.")
            if "cash_to_debt" in fund.columns:
                c2d = fund["cash_to_debt"].dropna()
                if not c2d.empty:
                    parts.append(f"Cash-to-debt ratio: {c2d.iloc[-1]:.2f} (latest).")

    # Events context (flat {TICKER}_*.parquet or legacy dir)
    insider_path = config.resolve_event_parquet(ticker, "insider")
    if insider_path:
        insider = pd.read_parquet(insider_path)
        if not insider.empty:
            buys = 0
            if "transaction_type" in insider.columns:
                buys = insider["transaction_type"].astype(str).str.lower().str.contains(
                    "buy|purchase", regex=True
                ).sum()
            parts.append(
                f"Insider transactions on file: {len(insider)} rows"
                f"{f' (~{int(buys)} buy-side mentions)' if buys else ''}."
            )

    recs_path = config.resolve_event_parquet(ticker, "recommendations")
    if recs_path:
        recs = pd.read_parquet(recs_path)
        if not recs.empty:
            parts.append(f"Analyst recommendations on file: {len(recs)} rows.")

    mh_path = config.resolve_event_parquet(ticker, "major_holders")
    if mh_path:
        mh = pd.read_parquet(mh_path)
        if not mh.empty:
            parts.append(f"Major holder breakdown snapshot: {len(mh)} rows.")

    if len(parts) <= 1:
        return None
    return " ".join(parts)


def generate_stock_profiles(tickers: list[str], dry_run: bool = False, use_llm: bool = False, llm=None) -> list[dict]:
    """Generate stock profile summaries for all tickers."""
    remaining = checkpoint.get_remaining(PHASE_PROFILES, tickers)
    logger.info("[Profiles] %d remaining of %d", len(remaining), len(tickers))

    if dry_run:
        logger.info("[DRY RUN] Would generate %d stock profiles", len(remaining))
        return []

    summaries = []
    for ticker in remaining:
        profile = _build_stock_profile(ticker)
        if profile:
            summaries.append({
                "collection": "stock_profiles",
                "id": f"profile_{ticker}",
                "document": profile,
                "metadata": {"ticker": ticker, "type": "stock_profile"},
            })
        checkpoint.mark_done(PHASE_PROFILES, ticker)

    if use_llm and llm is not None and summaries and not dry_run:
        asyncio.run(_polish_summary_batch(summaries, llm))

    logger.info("[Profiles] Generated %d profiles", len(summaries))
    return summaries


# ── 5b. Earnings Memories ─────────────────────────────────────────────────────

def generate_earnings_memories(tickers: list[str], dry_run: bool = False, use_llm: bool = False, llm=None) -> list[dict]:
    """Generate one summary per earnings event with price reaction context."""
    remaining = checkpoint.get_remaining(PHASE_EARNINGS, tickers)
    logger.info("[Earnings] %d remaining of %d", len(remaining), len(tickers))

    if dry_run:
        logger.info("[DRY RUN] Would generate earnings memories for %d tickers", len(remaining))
        return []

    summaries = []
    for ticker in remaining:
        earnings_path = config.resolve_event_parquet(ticker, "earnings")
        price_path = os.path.join(config.PRICES_DIR, f"{ticker}.parquet")

        if not earnings_path:
            checkpoint.mark_done(PHASE_EARNINGS, ticker)
            continue

        earnings = pd.read_parquet(earnings_path)
        prices = pd.read_parquet(price_path) if os.path.exists(price_path) else pd.DataFrame()

        for idx, row in earnings.iterrows():
            try:
                date_str = str(idx.date()) if hasattr(idx, "date") else str(idx)
                eps_est = row.get("EPS Estimate", None)
                eps_act = row.get("Reported EPS", None)

                if pd.isna(eps_est) and pd.isna(eps_act):
                    continue

                parts = [f"{ticker} earnings {date_str}:"]
                if not pd.isna(eps_act):
                    parts.append(f"Reported EPS ${eps_act:.2f}")
                if not pd.isna(eps_est):
                    parts.append(f"vs estimate ${eps_est:.2f}")
                    if not pd.isna(eps_act):
                        beat = eps_act - eps_est
                        pct = (beat / abs(eps_est) * 100) if eps_est != 0 else 0
                        label = "beat" if beat > 0 else "missed"
                        parts.append(f"({label} by {abs(pct):.1f}%).")

                rev_act = row.get("Reported Revenue", None)
                rev_est = row.get("Revenue Estimate", None)
                if rev_act is not None and not pd.isna(rev_act):
                    try:
                        parts.append(f"Revenue ${float(rev_act)/1e9:.2f}B reported.")
                    except (TypeError, ValueError):
                        parts.append(f"Revenue reported: {rev_act}.")
                if rev_est is not None and not pd.isna(rev_est) and rev_act is not None and not pd.isna(rev_act):
                    try:
                        re = float(rev_est)
                        ra = float(rev_act)
                        if re != 0:
                            rpct = (ra - re) / abs(re) * 100
                            parts.append(f"vs revenue estimate (~{rpct:+.1f}%).")
                    except (TypeError, ValueError):
                        pass

                macro_snip = _macro_context_for_timestamp(pd.Timestamp(idx))
                if macro_snip:
                    parts.append(macro_snip)

                # Price reaction from prices data
                if not prices.empty and "daily_return_pct" in prices.columns:
                    try:
                        date_idx = pd.Timestamp(idx)
                        mask = prices.index >= date_idx
                        if mask.any():
                            loc = prices.index.get_indexer([date_idx], method="nearest")[0]
                            if loc + 1 < len(prices):
                                next_day_return = prices["daily_return_pct"].iloc[loc + 1]
                                parts.append(f"Stock moved {next_day_return:+.1f}% next day.")
                    except Exception:
                        pass

                doc = " ".join(parts)
                summaries.append({
                    "collection": "earnings_memory",
                    "id": f"earn_{ticker}_{date_str}",
                    "document": doc,
                    "metadata": {"ticker": ticker, "date": date_str, "type": "earnings_event"},
                })
            except Exception as e:
                logger.debug("Earnings row error for %s: %s", ticker, e)

        checkpoint.mark_done(PHASE_EARNINGS, ticker)

    if use_llm and llm is not None and summaries and not dry_run:
        asyncio.run(_polish_summary_batch(summaries, llm))

    logger.info("[Earnings] Generated %d earnings memories", len(summaries))
    return summaries


# ── 5c. Price Pattern Memories ────────────────────────────────────────────────

def generate_price_patterns(tickers: list[str], dry_run: bool = False) -> list[dict]:
    """Generate summaries for notable price moves (>3% or >2x volume)."""
    remaining = checkpoint.get_remaining(PHASE_PATTERNS, tickers)
    logger.info("[Patterns] %d remaining of %d", len(remaining), len(tickers))

    if dry_run:
        logger.info("[DRY RUN] Would scan %d tickers for notable price moves", len(remaining))
        return []

    summaries = []
    for ticker in remaining:
        price_path = os.path.join(config.PRICES_DIR, f"{ticker}.parquet")
        if not os.path.exists(price_path):
            checkpoint.mark_done(PHASE_PATTERNS, ticker)
            continue

        prices = pd.read_parquet(price_path)
        if prices.empty or "daily_return_pct" not in prices.columns:
            checkpoint.mark_done(PHASE_PATTERNS, ticker)
            continue

        big_moves = prices[
            (prices["daily_return_pct"].abs() > 3.0) |
            (prices.get("relative_volume", pd.Series(dtype=float)) > 2.0)
        ]

        for idx, row in big_moves.iterrows():
            date_str = str(idx.date()) if hasattr(idx, "date") else str(idx)
            ret = row.get("daily_return_pct", 0)
            vol = row.get("relative_volume", 1.0)
            close = row.get("Close", 0)
            direction = "rose" if ret > 0 else "dropped"

            doc = (
                f"{ticker} {date_str}: {direction} {abs(ret):.1f}% to ${close:.2f}"
                f" on {vol:.1f}x average volume."
            )
            summaries.append({
                "collection": "price_movements",
                "id": f"pmove_{ticker}_{date_str}",
                "document": doc,
                "metadata": {
                    "ticker": ticker, "date": date_str,
                    "change_pct": round(ret, 2),
                    "relative_volume": round(vol, 2) if not pd.isna(vol) else 1.0,
                    "type": "price_pattern",
                },
            })

        checkpoint.mark_done(PHASE_PATTERNS, ticker)

    logger.info("[Patterns] Generated %d price pattern memories", len(summaries))
    return summaries


# ── 5d. Macro Regime Snapshots ────────────────────────────────────────────────

def generate_macro_snapshots(dry_run: bool = False) -> list[dict]:
    """Generate one RAG document per quarter from macro indicators."""
    macro_path = os.path.join(config.MACRO_DIR, "macro_quarterly.parquet")
    if not os.path.exists(macro_path):
        logger.warning("[Macro] No quarterly macro file found")
        return []

    if dry_run:
        logger.info("[DRY RUN] Would generate quarterly macro snapshots")
        return []

    quarterly = pd.read_parquet(macro_path)
    summaries = []

    for idx, row in quarterly.iterrows():
        date_str = str(idx.date()) if hasattr(idx, "date") else str(idx)
        q_label = f"Q{((idx.month - 1) // 3) + 1} {idx.year}" if hasattr(idx, "month") else date_str

        parts = [f"Macro snapshot {q_label}:"]
        if not pd.isna(row.get("fed_funds_rate")):
            parts.append(f"Fed Funds Rate {row['fed_funds_rate']:.2f}%.")
        if not pd.isna(row.get("cpi")):
            parts.append(f"CPI level {row['cpi']:.1f}.")
        if not pd.isna(row.get("treasury_10y")):
            parts.append(f"10Y Treasury {row['treasury_10y']:.2f}%.")
        if not pd.isna(row.get("unemployment")):
            parts.append(f"Unemployment {row['unemployment']:.1f}%.")
        if not pd.isna(row.get("vix")):
            parts.append(f"VIX {row['vix']:.1f}.")
        if not pd.isna(row.get("consumer_sentiment")):
            parts.append(f"Consumer sentiment {row['consumer_sentiment']:.1f}.")

        vix_v = row.get("vix")
        un_v = row.get("unemployment")
        regime = "BULL_NORMAL"
        try:
            if vix_v is not None and not pd.isna(vix_v) and float(vix_v) > 35:
                regime = "BEAR_STRESS"
            elif un_v is not None and not pd.isna(un_v) and float(un_v) > 8:
                regime = "BEAR_STRESS"
            elif vix_v is not None and not pd.isna(vix_v) and float(vix_v) > 25:
                regime = "BEAR_NORMAL"
        except (TypeError, ValueError):
            pass
        parts.append(f"Labeled regime (heuristic): {regime}.")

        doc = " ".join(parts)
        summaries.append({
            "collection": "macro_snapshots",
            "id": f"macro_{date_str}",
            "document": doc,
            "metadata": {"date": date_str, "quarter": q_label, "type": "macro_regime", "market_regime": regime},
        })

    logger.info("[Macro] Generated %d quarterly snapshots", len(summaries))
    return summaries


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run(tickers: list[str], dry_run: bool = False, use_llm: bool = False, llm_client=None) -> dict:
    """Run all summarization phases. Returns combined results."""
    config.ensure_dirs()

    profiles = generate_stock_profiles(tickers, dry_run, use_llm=use_llm, llm=llm_client)
    earnings = generate_earnings_memories(tickers, dry_run, use_llm=use_llm, llm=llm_client)
    patterns = generate_price_patterns(tickers, dry_run)
    macro = generate_macro_snapshots(dry_run)

    all_summaries = profiles + earnings + patterns + macro

    if not dry_run and all_summaries:
        out_path = os.path.join(config.SUMMARIES_DIR, "all_summaries.json")
        # Avoid wiping a full corpus when checkpoints are already complete but macro
        # still regenerates (profiles/earnings/patterns return []).
        # When summarize checkpoints are done, profiles/earnings/patterns are empty but
        # macro snapshots still regenerate — merge so we do not wipe the JSON corpus.
        macro_only = bool(macro) and not (profiles or earnings or patterns)
        if os.path.exists(out_path) and macro_only:
            try:
                with open(out_path, "r") as f:
                    existing = json.load(f)
                if len(existing) > len(macro) + 100:
                    kept = [s for s in existing if s.get("collection") != "macro_snapshots"]
                    all_summaries = kept + macro
                    logger.info(
                        "Merged %d macro snapshots into existing %d summaries (total %d)",
                        len(macro), len(kept), len(all_summaries),
                    )
            except Exception as e:
                logger.warning("Could not merge existing summaries: %s", e)

        with open(out_path, "w") as f:
            json.dump(all_summaries, f, indent=2, default=str)
        logger.info("Saved %d summaries to %s", len(all_summaries), out_path)

    return {
        "profiles": len(profiles),
        "earnings": len(earnings),
        "patterns": len(patterns),
        "macro": len(macro),
        "total": len(all_summaries),
        "dry_run": dry_run,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Generate RAG summaries from raw data")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tickers", type=str, default=None)
    parser.add_argument("--no-llm", action="store_true", help="Use data templates only, no LLM calls")
    args = parser.parse_args()

    ticker_list = config.get_tickers(args.tickers, args.dry_run)
    use_llm = not args.no_llm
    llm = None
    if use_llm and not args.dry_run:
        try:
            from backend.llm_client import get_llm_client
            llm = get_llm_client()
        except Exception as e:
            logger.warning("LLM client unavailable (%s); using templates only", e)
            use_llm = False
    run(ticker_list, dry_run=args.dry_run, use_llm=use_llm, llm_client=llm)
