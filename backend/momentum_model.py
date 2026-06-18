"""
Composite momentum pricing model — pure functions, no I/O.

Replaces Graham fair-value with a multi-factor momentum score:
absolute price trend, relative vs benchmark/sector, capital flow,
risk-adjusted quality, and market regime support, plus a separate
downside exposure module.

MVP: threshold-based scoring (no stock-universe percentile ranking).
Cross-sectional universe RS is deferred until a universe store exists.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

from backend.data_errors import InsufficientDataError

MIN_BARS_FULL = 252
MIN_BARS_PARTIAL = 126
TRADING_DAYS_YEAR = 252


def _clip_score(x: float) -> float:
    return float(max(0.0, min(100.0, x)))


def _safe_float(v: Any, default: float = float("nan")) -> float:
    try:
        if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in out.columns:
            if col == "Volume":
                out["Volume"] = 0.0
            else:
                raise ValueError(f"missing_column_{col}")
    out = out.sort_index()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["Close"])


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_g = gain.rolling(period, min_periods=period).mean()
    avg_l = loss.rolling(period, min_periods=period).mean()
    rs = avg_g / avg_l.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    v = out.iloc[-1]
    return float(v) if pd.notna(v) else float("nan")


def macd(close: pd.Series) -> Tuple[float, float, float]:
    ema12 = ema(close, 12)
    ema26 = ema(close, 26)
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal
    i = -1
    return (
        _safe_float(macd_line.iloc[i]),
        _safe_float(signal.iloc[i]),
        _safe_float(hist.iloc[i]),
    )


def roc(close: pd.Series, periods: int) -> float:
    if len(close) < periods + 1:
        return float("nan")
    prev = close.iloc[-periods - 1]
    if prev == 0 or pd.isna(prev):
        return float("nan")
    return float((close.iloc[-1] / prev - 1.0) * 100.0)


def return_over_periods(close: pd.Series, periods: int) -> float:
    """Decimal return over N trading days."""
    if len(close) < periods + 1:
        return float("nan")
    prev = close.iloc[-periods - 1]
    if prev == 0 or pd.isna(prev):
        return float("nan")
    return float(close.iloc[-1] / prev - 1.0)


def anchored_vwap(df: pd.DataFrame, anchor_idx: int = 0) -> float:
    sub = df.iloc[anchor_idx:]
    if sub.empty:
        return float("nan")
    typical = (sub["High"] + sub["Low"] + sub["Close"]) / 3.0
    vol = sub["Volume"].replace(0, np.nan)
    cum_vol = vol.sum()
    if cum_vol == 0 or pd.isna(cum_vol):
        return float("nan")
    return float((typical * vol).sum() / cum_vol)


def chaikin_money_flow(df: pd.DataFrame, period: int = 21) -> float:
    if len(df) < period:
        return float("nan")
    sub = df.tail(period)
    hl = sub["High"] - sub["Low"]
    mfm = ((sub["Close"] - sub["Low"]) - (sub["High"] - sub["Close"])) / hl.replace(0, np.nan)
    mfv = mfm * sub["Volume"]
    vol_sum = sub["Volume"].sum()
    if vol_sum == 0:
        return float("nan")
    return float(mfv.sum() / vol_sum)


def on_balance_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
    obv = pd.Series(0.0, index=close.index, dtype=float)
    if len(close) < 2:
        return obv
    direction = close.diff()
    for i in range(1, len(close)):
        if direction.iloc[i] > 0:
            obv.iloc[i] = obv.iloc[i - 1] + volume.iloc[i]
        elif direction.iloc[i] < 0:
            obv.iloc[i] = obv.iloc[i - 1] - volume.iloc[i]
        else:
            obv.iloc[i] = obv.iloc[i - 1]
    return obv


def obv_trend_slope(obv: pd.Series, lookback: int = 21) -> float:
    if len(obv) < lookback:
        return float("nan")
    y = obv.tail(lookback).values.astype(float)
    x = np.arange(len(y), dtype=float)
    if np.all(np.isnan(y)):
        return float("nan")
    coeffs = np.polyfit(x, np.nan_to_num(y), 1)
    return float(coeffs[0])


def max_drawdown(close: pd.Series) -> float:
    if close.empty:
        return float("nan")
    peak = close.cummax()
    dd = close / peak - 1.0
    return float(dd.min())


def downside_deviation(daily_returns: pd.Series) -> float:
    neg = daily_returns.clip(upper=0)
    if neg.empty:
        return float("nan")
    return float(neg.std() * math.sqrt(TRADING_DAYS_YEAR))


def trend_sharpe(close: pd.Series, lookback: int, risk_free: float = 0.0) -> float:
    if len(close) < lookback + 1:
        return float("nan")
    sub = close.tail(lookback + 1)
    rets = sub.pct_change().dropna()
    if rets.empty or rets.std() == 0:
        return float("nan")
    total_ret = sub.iloc[-1] / sub.iloc[0] - 1.0
    ann_ret = (1.0 + total_ret) ** (TRADING_DAYS_YEAR / lookback) - 1.0
    ann_vol = float(rets.std() * math.sqrt(TRADING_DAYS_YEAR))
    if ann_vol == 0:
        return float("nan")
    return float((ann_ret - risk_free) / ann_vol)


def information_ratio(stock_close: pd.Series, bench_close: pd.Series, lookback: int = 126) -> float:
    if len(stock_close) < lookback + 1 or len(bench_close) < lookback + 1:
        return float("nan")
    s = stock_close.tail(lookback + 1).pct_change().dropna()
    b = bench_close.tail(lookback + 1).pct_change().dropna()
    aligned = pd.concat([s, b], axis=1, join="inner").dropna()
    if aligned.empty or len(aligned) < 10:
        return float("nan")
    excess = aligned.iloc[:, 0] - aligned.iloc[:, 1]
    te = float(excess.std() * math.sqrt(TRADING_DAYS_YEAR))
    if te == 0:
        return float("nan")
    ann_excess = float(excess.mean() * TRADING_DAYS_YEAR)
    return ann_excess / te


def _return_threshold_score(ret_pct: float) -> float:
    """Map decimal return to 0-100 via fixed thresholds (no universe percentile)."""
    if math.isnan(ret_pct):
        return 50.0
    pct = ret_pct * 100.0
    if pct >= 40:
        return 100.0
    if pct >= 25:
        return 85.0
    if pct >= 15:
        return 70.0
    if pct >= 8:
        return 60.0
    if pct >= 3:
        return 50.0
    if pct >= 0:
        return 40.0
    if pct >= -5:
        return 30.0
    if pct >= -15:
        return 20.0
    return 10.0


def _ema_alignment_score(close: float, e20: float, e50: float, e100: float, e200: float) -> float:
    if any(math.isnan(x) for x in (close, e50, e200)):
        return 20.0
    if not math.isnan(e20) and not math.isnan(e100):
        if close > e20 > e50 > e100 > e200:
            return 100.0
    if close > e50 > e200:
        return 80.0
    if close > e200:
        return 60.0
    if close < e200 and e50 > e200:
        return 40.0
    return 20.0


def _rsi_zone_score(rsi_v: float) -> float:
    if math.isnan(rsi_v):
        return 50.0
    if 55 <= rsi_v <= 70:
        return 100.0
    if 70 < rsi_v <= 80:
        return 80.0
    if 45 <= rsi_v < 55:
        return 60.0
    if 80 < rsi_v <= 90:
        return 45.0
    if rsi_v < 45:
        return 30.0
    return 20.0


def _cmf_score(cmf: float) -> float:
    if math.isnan(cmf):
        return 50.0
    if cmf > 0.20:
        return 100.0
    if cmf > 0.10:
        return 80.0
    if cmf > 0.0:
        return 60.0
    if cmf > -0.10:
        return 40.0
    return 20.0


def _relative_volume_score(rel_vol: float) -> float:
    if math.isnan(rel_vol):
        return 50.0
    if 1.2 <= rel_vol <= 2.0:
        return 85.0
    if 0.8 <= rel_vol < 1.2:
        return 60.0
    if rel_vol > 3.0:
        return 45.0
    if rel_vol < 0.8:
        return 30.0
    return 70.0


def _benchmark_trend_score(close: pd.Series) -> float:
    if len(close) < 200:
        return 50.0
    c = float(close.iloc[-1])
    e50 = float(ema(close, 50).iloc[-1])
    e200 = float(ema(close, 200).iloc[-1])
    if c > e200 and e50 > e200:
        return 100.0
    if c > e200:
        return 75.0
    if c < e200 and e50 > e200:
        return 45.0
    return 20.0


def compute_momentum_indicators(
    stock_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    sector_df: Optional[pd.DataFrame],
    metadata: Dict[str, Any],
    as_of_date: Optional[Union[str, date, datetime]] = None,
) -> Dict[str, Any]:
    """Compute raw momentum indicators from OHLCV frames."""
    stock = _normalize_ohlcv(stock_df)
    spy = _normalize_ohlcv(spy_df)
    sector = _normalize_ohlcv(sector_df) if sector_df is not None and not sector_df.empty else spy

    close = stock["Close"]
    n = len(close)
    partial = n < MIN_BARS_FULL

    ret_1m = return_over_periods(close, 21)
    ret_3m = return_over_periods(close, 63)
    ret_6m = return_over_periods(close, 126)
    ret_12m_1m = float("nan")
    if n > 273:
        ret_12m_1m = float(close.iloc[-22] / close.iloc[-273] - 1.0)

    e20 = float(ema(close, 20).iloc[-1])
    e50 = float(ema(close, 50).iloc[-1])
    e100 = float(ema(close, 100).iloc[-1]) if n >= 100 else float("nan")
    e200 = float(ema(close, 200).iloc[-1]) if n >= 200 else float("nan")
    last_close = float(close.iloc[-1])

    rsi_v = rsi(close, 14)
    macd_line, macd_signal, macd_hist = macd(close)
    roc_21 = roc(close, 21)
    roc_63 = roc(close, 63)
    roc_accel = roc_21 - roc_63 if not (math.isnan(roc_21) or math.isnan(roc_63)) else float("nan")

    anchor_idx = max(0, n - 126)
    avwap = anchored_vwap(stock, anchor_idx)
    cmf_21 = chaikin_money_flow(stock, 21)
    obv_series = on_balance_volume(close, stock["Volume"])
    obv_slope = obv_trend_slope(obv_series, 21)

    avg_vol_20 = float(stock["Volume"].tail(20).mean()) if n >= 20 else float("nan")
    rel_vol = float(stock["Volume"].iloc[-1] / avg_vol_20) if avg_vol_20 and avg_vol_20 > 0 else float("nan")

    daily_rets = close.pct_change().dropna()
    dd_3m = max_drawdown(close.tail(63)) if n >= 63 else float("nan")
    dd_6m = max_drawdown(close.tail(126)) if n >= 126 else float("nan")
    dd_12m = max_drawdown(close) if n >= 252 else dd_6m

    sharpe_6m = trend_sharpe(close, 126)
    ir_spy = information_ratio(close, spy["Close"], 126)
    ir_sector = information_ratio(close, sector["Close"], 126)
    dd_dev = downside_deviation(daily_rets.tail(126))

    spy_ret_6m = return_over_periods(spy["Close"], 126)
    sector_ret_6m = return_over_periods(sector["Close"], 126)
    beta = _safe_float(metadata.get("beta"), 1.0)
    bench_excess_6m = ret_6m - spy_ret_6m if not (math.isnan(ret_6m) or math.isnan(spy_ret_6m)) else float("nan")
    sector_excess_6m = ret_6m - sector_ret_6m if not (math.isnan(ret_6m) or math.isnan(sector_ret_6m)) else float("nan")
    beta_adj = ret_6m - beta * spy_ret_6m if not (math.isnan(ret_6m) or math.isnan(spy_ret_6m)) else float("nan")

    ema_dist_50 = (last_close - e50) / e50 if e50 and not math.isnan(e50) else float("nan")
    ema_dist_200 = (last_close - e200) / e200 if e200 and not math.isnan(e200) else float("nan")

    # Trend consistency: positive weeks over lookback
    weekly = close.resample("W").last().dropna()
    if len(weekly) >= 4:
        w_rets = weekly.pct_change().dropna()
        trend_consistency = float((w_rets > 0).sum() / len(w_rets)) if len(w_rets) else 0.5
    else:
        trend_consistency = 0.5

    spy_trend = _benchmark_trend_score(spy["Close"])
    sector_trend = _benchmark_trend_score(sector["Close"])
    vol_regime = 70.0 if daily_rets.tail(21).std() < daily_rets.tail(63).std() else 40.0

    market_cap = _safe_float(metadata.get("market_cap"), 0.0)
    avg_dollar_vol = float((close.tail(20) * stock["Volume"].tail(20)).mean()) if n >= 20 else 0.0

    return {
        "bars": n,
        "partial_mode": partial,
        "as_of_date": str(as_of_date or close.index[-1].date().isoformat()),
        "close": last_close,
        "return_1m": ret_1m,
        "return_3m": ret_3m,
        "return_6m": ret_6m,
        "return_12m_minus_1m": ret_12m_1m,
        "ema_20": e20,
        "ema_50": e50,
        "ema_100": e100,
        "ema_200": e200,
        "ema_distance_50": ema_dist_50,
        "ema_distance_200": ema_dist_200,
        "rsi_14": rsi_v,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_histogram": macd_hist,
        "roc_21d": roc_21,
        "roc_63d": roc_63,
        "roc_acceleration": roc_accel,
        "anchored_vwap": avwap,
        "price_vs_avwap": (last_close - avwap) / avwap if avwap and not math.isnan(avwap) else float("nan"),
        "cmf_21d": cmf_21,
        "obv_slope": obv_slope,
        "relative_volume_20d": rel_vol,
        "max_drawdown_3m": dd_3m,
        "max_drawdown_6m": dd_6m,
        "max_drawdown_12m": dd_12m,
        "trend_sharpe_6m": sharpe_6m,
        "information_ratio_spy": ir_spy,
        "information_ratio_sector": ir_sector,
        "downside_deviation": dd_dev,
        "benchmark_excess_6m": bench_excess_6m,
        "sector_excess_6m": sector_excess_6m,
        "beta_adjusted_return_6m": beta_adj,
        "beta": beta,
        "trend_consistency": trend_consistency,
        "spy_trend_score": spy_trend,
        "sector_trend_score": sector_trend,
        "volatility_regime_score": vol_regime,
        "market_cap": market_cap,
        "avg_dollar_volume_20d": avg_dollar_vol,
        "sector": metadata.get("sector", "Unknown"),
        "industry": metadata.get("industry", "Unknown"),
    }


def score_absolute_momentum(ind: Dict[str, Any]) -> float:
    r12 = _return_threshold_score(ind.get("return_12m_minus_1m", float("nan")))
    r6 = _return_threshold_score(ind.get("return_6m", float("nan")))
    r3 = _return_threshold_score(ind.get("return_3m", float("nan")))
    r1 = _return_threshold_score(ind.get("return_1m", float("nan")))
    ema_al = _ema_alignment_score(
        ind["close"],
        ind["ema_20"],
        ind["ema_50"],
        ind.get("ema_100", float("nan")),
        ind.get("ema_200", float("nan")),
    )
    roc_a = ind.get("roc_acceleration", float("nan"))
    roc_score = 70.0 if not math.isnan(roc_a) and roc_a > 0 else (40.0 if not math.isnan(roc_a) else 50.0)
    macd_h = ind.get("macd_histogram", float("nan"))
    macd_score = 80.0 if not math.isnan(macd_h) and macd_h > 0 else (35.0 if not math.isnan(macd_h) else 50.0)
    rsi_score = _rsi_zone_score(ind.get("rsi_14", float("nan")))

    if ind.get("partial_mode"):
        weights = (0.0, 0.35, 0.25, 0.15, 0.10, 0.05, 0.05, 0.05)
    else:
        weights = (0.30, 0.20, 0.15, 0.10, 0.10, 0.05, 0.05, 0.05)

    raw = (
        weights[0] * r12
        + weights[1] * r6
        + weights[2] * r3
        + weights[3] * r1
        + weights[4] * ema_al
        + weights[5] * roc_score
        + weights[6] * macd_score
        + weights[7] * rsi_score
    )
    return _clip_score(raw)


def score_relative_momentum(ind: Dict[str, Any]) -> float:
    """Relative vs SPY and sector ETF only (universe percentile deferred)."""
    bench = ind.get("benchmark_excess_6m", float("nan"))
    sector = ind.get("sector_excess_6m", float("nan"))
    beta_adj = ind.get("beta_adjusted_return_6m", float("nan"))

    def excess_score(ex: float) -> float:
        if math.isnan(ex):
            return 50.0
        pct = ex * 100.0
        if pct >= 15:
            return 100.0
        if pct >= 8:
            return 80.0
        if pct >= 3:
            return 65.0
        if pct >= 0:
            return 50.0
        if pct >= -5:
            return 35.0
        return 20.0

    bench_s = excess_score(bench)
    sector_s = excess_score(sector)
    beta_s = excess_score(beta_adj)
    return _clip_score(0.40 * bench_s + 0.35 * sector_s + 0.25 * beta_s)


def score_capital_flow(ind: Dict[str, Any]) -> float:
    pav = ind.get("price_vs_avwap", float("nan"))
    if math.isnan(pav):
        avwap_s = 50.0
    elif pav > 0.05:
        avwap_s = 100.0
    elif pav > 0:
        avwap_s = 80.0
    elif pav > -0.03:
        avwap_s = 50.0
    else:
        avwap_s = 25.0

    cmf_s = _cmf_score(ind.get("cmf_21d", float("nan")))
    obv_sl = ind.get("obv_slope", float("nan"))
    obv_s = 80.0 if not math.isnan(obv_sl) and obv_sl > 0 else (30.0 if not math.isnan(obv_sl) else 50.0)
    rel_vol_s = _relative_volume_score(ind.get("relative_volume_20d", float("nan")))
    ad_s = cmf_s  # A/D proxy via CMF for MVP

    breakout_s = 70.0 if rel_vol_s >= 60 and avwap_s >= 60 else 40.0

    return _clip_score(
        0.25 * avwap_s + 0.20 * cmf_s + 0.20 * obv_s + 0.15 * rel_vol_s + 0.10 * ad_s + 0.10 * breakout_s
    )


def score_risk_adjusted(ind: Dict[str, Any]) -> float:
    sharpe = ind.get("trend_sharpe_6m", float("nan"))
    ir = ind.get("information_ratio_spy", float("nan"))
    ir_sec = ind.get("information_ratio_sector", float("nan"))
    dd_dev = ind.get("downside_deviation", float("nan"))
    mdd = ind.get("max_drawdown_6m", float("nan"))
    consistency = ind.get("trend_consistency", 0.5)

    def sharpe_score(s: float) -> float:
        if math.isnan(s):
            return 50.0
        if s >= 2.0:
            return 100.0
        if s >= 1.0:
            return 80.0
        if s >= 0.5:
            return 60.0
        if s >= 0:
            return 45.0
        return 25.0

    def ir_score(v: float) -> float:
        if math.isnan(v):
            return 50.0
        if v >= 1.0:
            return 90.0
        if v >= 0.5:
            return 75.0
        if v >= 0:
            return 55.0
        return 30.0

    def low_dd_dev_score(v: float) -> float:
        if math.isnan(v):
            return 50.0
        if v < 0.10:
            return 90.0
        if v < 0.20:
            return 70.0
        if v < 0.35:
            return 50.0
        return 25.0

    def low_mdd_score(v: float) -> float:
        if math.isnan(v):
            return 50.0
        if v > -0.10:
            return 90.0
        if v > -0.20:
            return 70.0
        if v > -0.35:
            return 45.0
        return 20.0

    cons_s = _clip_score(consistency * 100.0)
    return _clip_score(
        0.25 * sharpe_score(sharpe)
        + 0.20 * ir_score(ir)
        + 0.15 * ir_score(ir_sec)
        + 0.15 * low_dd_dev_score(dd_dev)
        + 0.15 * low_mdd_score(mdd)
        + 0.10 * cons_s
    )


def score_market_regime(ind: Dict[str, Any]) -> float:
    return _clip_score(
        0.35 * ind.get("spy_trend_score", 50.0)
        + 0.30 * ind.get("sector_trend_score", 50.0)
        + 0.20 * ind.get("volatility_regime_score", 50.0)
        + 0.15 * 50.0  # breadth / defensive rotation deferred
    )


def compute_downside_exposure(ind: Dict[str, Any]) -> Dict[str, Any]:
    mdd_6m = ind.get("max_drawdown_6m", float("nan"))
    if math.isnan(mdd_6m):
        hist_dd_risk = 50.0
    elif mdd_6m > -0.10:
        hist_dd_risk = 20.0
    elif mdd_6m > -0.20:
        hist_dd_risk = 40.0
    elif mdd_6m > -0.35:
        hist_dd_risk = 70.0
    else:
        hist_dd_risk = 90.0

    dd_dev = ind.get("downside_deviation", float("nan"))
    if math.isnan(dd_dev):
        vol_risk = 50.0
    elif dd_dev < 0.15:
        vol_risk = 25.0
    elif dd_dev < 0.25:
        vol_risk = 45.0
    elif dd_dev < 0.40:
        vol_risk = 65.0
    else:
        vol_risk = 85.0
    beta = ind.get("beta", 1.0)
    if beta > 1.5:
        vol_risk = min(100.0, vol_risk + 10.0)

    ext_50 = ind.get("ema_distance_50", float("nan"))
    if math.isnan(ext_50):
        overext = 50.0
    elif ext_50 < 0.05:
        overext = 20.0
    elif ext_50 < 0.12:
        overext = 40.0
    elif ext_50 < 0.25:
        overext = 70.0
    else:
        overext = 90.0
    rsi_v = ind.get("rsi_14", float("nan"))
    if not math.isnan(rsi_v) and rsi_v > 80:
        overext = min(100.0, overext + 10.0)

    close = ind["close"]
    e20, e50, e100, e200 = ind["ema_20"], ind["ema_50"], ind.get("ema_100"), ind.get("ema_200")
    avwap = ind.get("anchored_vwap", float("nan"))

    def pullback_to(level: float) -> float:
        if level and not math.isnan(level) and close > 0:
            return float(level / close - 1.0)
        return float("nan")

    pb_20 = pullback_to(e20)
    pb_50 = pullback_to(e50)
    pb_100 = pullback_to(e100) if e100 and not math.isnan(e100) else pb_50
    pb_200 = pullback_to(e200) if e200 and not math.isnan(e200) else pb_100
    pb_avwap = pullback_to(avwap)

    support_risk = 50.0
    if not math.isnan(pb_50) and pb_50 > -0.05:
        support_risk = 75.0
    elif not math.isnan(pb_50) and pb_50 > -0.12:
        support_risk = 55.0
    else:
        support_risk = 35.0

    adv = ind.get("avg_dollar_volume_20d", 0.0)
    if adv > 100_000_000:
        liq_risk = 15.0
    elif adv > 25_000_000:
        liq_risk = 35.0
    elif adv > 5_000_000:
        liq_risk = 60.0
    else:
        liq_risk = 85.0

    spy_t = ind.get("spy_trend_score", 50.0)
    if spy_t >= 75:
        regime_risk = 20.0
    elif spy_t >= 45:
        regime_risk = 40.0
    elif spy_t >= 20:
        regime_risk = 70.0
    else:
        regime_risk = 90.0

    downside_score = _clip_score(
        0.25 * hist_dd_risk
        + 0.20 * vol_risk
        + 0.20 * overext
        + 0.15 * support_risk
        + 0.10 * liq_risk
        + 0.10 * regime_risk
    )

    mild_vals = [v for v in (pb_20, pb_50) if not math.isnan(v)]
    mod_vals = [v for v in (pb_50, pb_avwap, pb_100) if not math.isnan(v)]
    sev_vals = [v for v in (pb_200, mdd_6m) if not math.isnan(v)]

    def fmt_range(vals: List[float], default: str) -> str:
        if not vals:
            return default
        lo = min(vals) * 100.0
        hi = max(vals) * 100.0
        return f"{lo:.0f}% to {hi:.0f}%"

    if downside_score < 25:
        crash_risk = "Low"
    elif downside_score < 50:
        crash_risk = "Medium"
    elif downside_score < 75:
        crash_risk = "High"
    else:
        crash_risk = "Extreme"

    return {
        "historical_drawdown_risk": round(hist_dd_risk, 1),
        "volatility_risk": round(vol_risk, 1),
        "overextension_risk": round(overext, 1),
        "support_breakdown_risk": round(support_risk, 1),
        "liquidity_risk": round(liq_risk, 1),
        "regime_stress_risk": round(regime_risk, 1),
        "downside_exposure_score": round(downside_score, 2),
        "crash_risk": crash_risk,
        "mild_pullback_estimate": fmt_range(mild_vals, "-3% to -7%"),
        "trend_damage_estimate": fmt_range(mod_vals, "-8% to -15%"),
        "major_breakdown_estimate": fmt_range(sev_vals, "-18% to -32%"),
    }


def classify_momentum(
    final_score: float,
    downside_score: float,
    ind: Dict[str, Any],
) -> Tuple[str, List[str]]:
    if final_score >= 90 and downside_score < 45:
        label = "Elite Momentum Leader"
    elif final_score >= 80 and downside_score < 60:
        label = "Strong Momentum Candidate"
    elif final_score >= 75 and 60 <= downside_score < 75:
        label = "High Momentum / High Risk"
    elif final_score >= 65:
        label = "Positive Momentum Watchlist"
    elif final_score >= 50:
        label = "Neutral / Mixed Momentum"
    elif final_score >= 35:
        label = "Weak Momentum"
    else:
        label = "Momentum Breakdown"

    flags: List[str] = []
    ext_50 = ind.get("ema_distance_50", float("nan"))
    rsi_v = ind.get("rsi_14", float("nan"))
    if final_score > 75 and not math.isnan(ext_50) and ext_50 > 0.20 and not math.isnan(rsi_v) and rsi_v > 75:
        flags.append("Overextended Winner")
    cmf = ind.get("cmf_21d", float("nan"))
    obv_sl = ind.get("obv_slope", float("nan"))
    if not math.isnan(cmf) and cmf < 0 and not math.isnan(obv_sl) and obv_sl < 0:
        flags.append("False Breakout")
    if not math.isnan(cmf) and cmf < -0.05 and not math.isnan(obv_sl) and obv_sl < 0:
        flags.append("Distribution Risk")
    if final_score > 70 and ind.get("sector_trend_score", 50) < 50:
        flags.append("Sector Rotation Risk")
    if ind.get("spy_trend_score", 50) < 45 and final_score > 65:
        flags.append("Market Regime Conflict")

    return label, flags


def generate_agent_summary(
    final_score: float,
    downside: Dict[str, Any],
    classification: str,
    risk_flags: List[str],
    subscores: Dict[str, float],
) -> str:
    top = sorted(subscores.items(), key=lambda x: x[1], reverse=True)[:2]
    weak = sorted(subscores.items(), key=lambda x: x[1])[:2]
    top_str = ", ".join(f"{k.replace('_', ' ')} ({v:.0f})" for k, v in top)
    weak_str = ", ".join(f"{k.replace('_', ' ')} ({v:.0f})" for k, v in weak)
    flags_str = ", ".join(risk_flags) if risk_flags else "none"
    return (
        f"Momentum Pricing Score {final_score:.1f}/100 — {classification}. "
        f"Strongest: {top_str}. Weakest: {weak_str}. "
        f"Downside exposure {downside['downside_exposure_score']:.1f}/100 ({downside['crash_risk']}). "
        f"Mild pullback {downside['mild_pullback_estimate']}; "
        f"trend damage {downside['trend_damage_estimate']}. "
        f"Risk flags: {flags_str}."
    )


def analyze_momentum(
    stock_df: pd.DataFrame,
    spy_df: pd.DataFrame,
    sector_df: Optional[pd.DataFrame],
    metadata: Dict[str, Any],
    as_of_date: Optional[Union[str, date, datetime]] = None,
) -> Dict[str, Any]:
    """
    Main orchestration — returns full momentum readout dict.
    Raises InsufficientDataError when fewer than 126 trading bars.
    """
    stock = _normalize_ohlcv(stock_df)
    if len(stock) < MIN_BARS_PARTIAL:
        ticker = metadata.get("ticker", "UNKNOWN")
        raise InsufficientDataError(
            "momentum_model",
            f"Insufficient price history for {ticker}: need at least {MIN_BARS_PARTIAL} trading days.",
            ticker=str(ticker),
            missing=["price_history_1y"],
        )

    ind = compute_momentum_indicators(stock_df, spy_df, sector_df, metadata, as_of_date)

    abs_s = score_absolute_momentum(ind)
    rel_s = score_relative_momentum(ind)
    cf_s = score_capital_flow(ind)
    ra_s = score_risk_adjusted(ind)
    reg_s = score_market_regime(ind)

    final_score = _clip_score(0.30 * abs_s + 0.25 * rel_s + 0.20 * cf_s + 0.15 * ra_s + 0.10 * reg_s)
    downside = compute_downside_exposure(ind)
    downside_penalty = downside["downside_exposure_score"] * 0.30
    decision_quality = round(final_score - downside_penalty, 2)

    classification, risk_flags = classify_momentum(final_score, downside["downside_exposure_score"], ind)
    subscores = {
        "absolute_price_momentum": round(abs_s, 2),
        "relative_momentum": round(rel_s, 2),
        "capital_flow_confirmation": round(cf_s, 2),
        "risk_adjusted_momentum": round(ra_s, 2),
        "market_regime_support": round(reg_s, 2),
    }
    summary = generate_agent_summary(
        final_score, downside, classification, risk_flags, subscores
    )

    return {
        "ticker": str(metadata.get("ticker", "")).upper(),
        "as_of_date": ind["as_of_date"],
        "momentum_pricing_score": round(final_score, 2),
        "downside_exposure_score": downside["downside_exposure_score"],
        "decision_quality_score": decision_quality,
        "classification": classification,
        "crash_risk": downside["crash_risk"],
        "subscores": subscores,
        "downside": downside,
        "risk_flags": risk_flags,
        "agent_summary": summary,
        "partial_mode": ind.get("partial_mode", False),
        "indicators": {k: v for k, v in ind.items() if k not in ("sector", "industry")},
    }
