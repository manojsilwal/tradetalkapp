"""
Daily Challenge Engine — per-user answers, shared deterministic challenge per day.
"""
import sqlite3
import json
import os
import time
import hashlib
import threading
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "progress.db")
_local  = threading.local()


def _get_conn():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_challenges_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_challenges (
            user_id          TEXT NOT NULL,
            challenge_date   TEXT NOT NULL,
            challenge_type   TEXT NOT NULL,
            challenge_json   TEXT NOT NULL,
            user_answer      TEXT DEFAULT NULL,
            answered_at      REAL DEFAULT NULL,
            resolved         INTEGER DEFAULT 0,
            correct          INTEGER DEFAULT NULL,
            xp_awarded       INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, challenge_date)
        );
    """)
    conn.commit()


QUIZ_BANK: List[Dict] = [
    {"question": "A stock has a P/E of 12 while its sector average is 22. This most likely indicates:", "options": ["The company is overvalued", "The company may be undervalued", "The company has no earnings", "The company is in a bubble"], "answer": 1, "explanation": "A P/E below the sector average can signal undervaluation — a core value investing signal."},
    {"question": "The yield curve inverts when:", "options": ["Short-term rates exceed long-term rates", "Long-term rates exceed short-term rates", "The Fed cuts rates", "Inflation rises above 3%"], "answer": 0, "explanation": "Inversion means 2-year yields > 10-year yields — historically a recession predictor."},
    {"question": "Free Cash Flow (FCF) Yield is calculated as:", "options": ["Net Income / Market Cap", "FCF / Enterprise Value", "FCF / Market Cap", "Revenue / Price"], "answer": 2, "explanation": "FCF Yield = FCF / Market Cap — tells you what % of the market cap comes back as real cash."},
    {"question": "The Sharpe Ratio measures:", "options": ["Total returns", "Return per unit of risk", "Volatility only", "Dividend yield"], "answer": 1, "explanation": "Sharpe = (Return − Risk-Free Rate) / StdDev. Higher is better; >1 is generally considered good."},
    {"question": "Which scenario typically benefits growth stocks the MOST?", "options": ["Rising interest rates", "Falling interest rates", "High inflation", "Inverted yield curve"], "answer": 1, "explanation": "Lower rates reduce the discount rate applied to future earnings, boosting growth stock valuations."},
    {"question": "A company with a Debt/Equity ratio of 0.2 is considered:", "options": ["Highly leveraged", "Moderately leveraged", "Conservatively financed", "Technically insolvent"], "answer": 2, "explanation": "D/E < 0.5 is generally low leverage. Warren Buffett prefers companies with low debt burdens."},
    {"question": "ROIC stands for:", "options": ["Return on Invested Capital", "Rate of Inflation Control", "Ratio of Income to Cost", "Return on Individual Contracts"], "answer": 0, "explanation": "ROIC = NOPAT / Invested Capital. Consistently high ROIC (>15%) often signals a durable moat."},
    {"question": "What does a VIX reading above 30 typically indicate?", "options": ["Low market fear", "High market volatility/fear", "A bull market", "Low trading volume"], "answer": 1, "explanation": "VIX > 30 signals elevated fear. Historically, high VIX periods can be buying opportunities for long-term investors."},
    {"question": "The '200-day moving average' is primarily used to:", "options": ["Calculate dividends", "Identify long-term trend direction", "Measure P/E ratio", "Predict earnings"], "answer": 1, "explanation": "Price above its 200-DMA = uptrend. Price below = downtrend. A widely watched momentum indicator."},
    {"question": "A company's 'Margin of Safety' refers to:", "options": ["The profit margin on sales", "Buying at a discount to intrinsic value", "Insurance against losses", "The safety of its dividends"], "answer": 1, "explanation": "Ben Graham coined this — buying below intrinsic value provides a buffer against errors in your analysis."},
    {"question": "Short interest as % of float above 20% typically suggests:", "options": ["Strong institutional buying", "Heavy bearish sentiment / potential squeeze", "The stock is in an index", "Low liquidity"], "answer": 1, "explanation": "High short interest = many traders betting against the stock. If the thesis fails, shorts covering drives a squeeze."},
    {"question": "Enterprise Value (EV) includes:", "options": ["Market Cap only", "Market Cap + Net Debt + Preferred Stock", "Revenue × P/E", "Book Value + Goodwill"], "answer": 1, "explanation": "EV = Market Cap + Total Debt − Cash. It represents the total acquisition cost of a business."},
    {"question": "What does a Sharpe Ratio of -0.5 indicate?", "options": ["Excellent risk-adjusted returns", "The strategy underperforms the risk-free rate", "Zero volatility", "High alpha generation"], "answer": 1, "explanation": "Negative Sharpe = returns below risk-free rate after adjusting for risk. The strategy is destroying value."},
    {"question": "In a DCF model, the 'discount rate' represents:", "options": ["The inflation rate", "The required rate of return / cost of capital", "The tax rate", "The dividend yield"], "answer": 1, "explanation": "Higher discount rates lower a stock's DCF value. This is why rising interest rates hurt growth stocks."},
]

DEBATE_TICKERS = ["AAPL","MSFT","GOOGL","AMZN","META","NVDA","TSLA","JPM","V","KO","PEP","WMT","MCD","NFLX","COST","CSCO","AMGN","GS","AMD","DIS"]
SECTORS        = ["Technology","Healthcare","Financials","Energy","Consumer Discretionary","Industrials","Utilities","Real Estate"]
SECTOR_ETFS    = {"Technology":"XLK","Healthcare":"XLV","Financials":"XLF","Energy":"XLE","Consumer Discretionary":"XLY","Industrials":"XLI","Utilities":"XLU","Real Estate":"XLRE"}


def _day_seed(challenge_date: str) -> int:
    return int(hashlib.md5(challenge_date.encode()).hexdigest(), 16)


def _build_challenge(challenge_date: str) -> Dict[str, Any]:
    seed  = _day_seed(challenge_date)
    ctype = ["A", "B", "C"][seed % 3]
    ch: Dict[str, Any] = {"date": challenge_date, "type": ctype, "xp_reward": 30}
    if ctype == "A":
        sector = SECTORS[seed % len(SECTORS)]
        ch.update({"title": f"Market Call: {sector}", "prompt": f"Based on today's macro environment, will the {sector} sector close HIGHER or LOWER tomorrow?", "options": ["HIGHER", "LOWER"], "kind": "direction", "sector": sector})
    elif ctype == "B":
        ticker = DEBATE_TICKERS[seed % len(DEBATE_TICKERS)]
        ch.update({"title": f"Debate Duel: {ticker}", "prompt": f"The AI agents are split on {ticker}. Which side do you take?", "options": ["BULLISH", "BEARISH"], "kind": "stance", "ticker": ticker})
    else:
        q = QUIZ_BANK[seed % len(QUIZ_BANK)]
        ch.update({"title": "Strategy Quiz", "prompt": q["question"], "options": q["options"], "kind": "quiz", "answer_idx": q["answer"], "explanation": q["explanation"]})
    return ch


def get_today_challenge(user_id: str) -> Dict[str, Any]:
    today = date.today().isoformat()
    conn  = _get_conn()
    row   = conn.execute("SELECT * FROM daily_challenges WHERE user_id=? AND challenge_date=?", (user_id, today)).fetchone()
    if not row:
        ch = _build_challenge(today)
        conn.execute("""
            INSERT INTO daily_challenges (user_id, challenge_date, challenge_type, challenge_json)
            VALUES (?, ?, ?, ?)
        """, (user_id, today, ch["type"], json.dumps(ch)))
        conn.commit()
        row = conn.execute("SELECT * FROM daily_challenges WHERE user_id=? AND challenge_date=?", (user_id, today)).fetchone()
    ch = json.loads(row["challenge_json"])
    ch.pop("answer_idx", None)
    return {**ch, "answered": row["user_answer"] is not None, "user_answer": row["user_answer"], "resolved": bool(row["resolved"]), "correct": row["correct"], "xp_awarded": row["xp_awarded"]}


def submit_answer(user_id: str, answer: str) -> Dict[str, Any]:
    today = date.today().isoformat()
    conn  = _get_conn()
    row   = conn.execute("SELECT * FROM daily_challenges WHERE user_id=? AND challenge_date=?", (user_id, today)).fetchone()
    if not row:
        return {"error": "No challenge found for today"}
    if row["user_answer"] is not None:
        return {"error": "Already answered today's challenge"}
    ch = json.loads(row["challenge_json"])
    conn.execute("UPDATE daily_challenges SET user_answer=?, answered_at=? WHERE user_id=? AND challenge_date=?", (answer, time.time(), user_id, today))
    conn.commit()
    if ch["type"] == "C":
        correct_idx = ch.get("answer_idx", -1)
        user_idx    = int(answer) if answer.isdigit() else -1
        is_correct  = (user_idx == correct_idx)
        xp          = 30 if is_correct else 10
        conn.execute("UPDATE daily_challenges SET resolved=1, correct=?, xp_awarded=? WHERE user_id=? AND challenge_date=?", (1 if is_correct else 0, xp, user_id, today))
        conn.commit()
        return {"resolved": True, "correct": is_correct, "explanation": ch.get("explanation", ""), "xp_awarded": xp}
    return {"pending": True, "message": "Answer recorded. Check back tomorrow for results!"}


def resolve_yesterday(user_id: str) -> Optional[Dict[str, Any]]:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn      = _get_conn()
    row       = conn.execute("SELECT * FROM daily_challenges WHERE user_id=? AND challenge_date=? AND user_answer IS NOT NULL AND resolved=0", (user_id, yesterday)).fetchone()
    if not row:
        return None
    ch          = json.loads(row["challenge_json"])
    user_answer = row["user_answer"]
    correct: Optional[bool] = None
    xp = 30
    if ch["type"] == "A":
        moved_up = _resolve_ticker_direction(SECTOR_ETFS.get(ch.get("sector","Technology"),"SPY"), yesterday)
        if moved_up is not None:
            correct = (user_answer == "HIGHER") == moved_up
            if not correct: xp = 5
    elif ch["type"] == "B":
        moved_up = _resolve_ticker_direction(ch.get("ticker","AAPL"), yesterday)
        if moved_up is not None:
            correct = (user_answer == "BULLISH") == moved_up
            if not correct: xp = 5
    if correct is None:
        return None
    conn.execute("UPDATE daily_challenges SET resolved=1, correct=?, xp_awarded=? WHERE user_id=? AND challenge_date=?", (1 if correct else 0, xp, user_id, yesterday))
    conn.commit()
    return {"date": yesterday, "correct": correct, "xp_awarded": xp}


def _resolve_ticker_direction(ticker: str, date_str: str) -> Optional[bool]:
    try:
        import yfinance as yf
        from datetime import datetime, timedelta as td
        d0 = datetime.strptime(date_str, "%Y-%m-%d")
        d1 = d0 + td(days=3)
        hist = yf.Ticker(ticker).history(start=date_str, end=d1.strftime("%Y-%m-%d"))
        if len(hist) >= 2:
            return float(hist["Close"].iloc[1]) > float(hist["Close"].iloc[0])
    except Exception:
        pass
    return None


def get_yesterday_result(user_id: str) -> Optional[Dict[str, Any]]:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn = _get_conn()
    row  = conn.execute("SELECT * FROM daily_challenges WHERE user_id=? AND challenge_date=?", (user_id, yesterday)).fetchone()
    if not row:
        return None
    return {"date": yesterday, "answered": row["user_answer"] is not None, "user_answer": row["user_answer"], "resolved": bool(row["resolved"]), "correct": row["correct"], "xp_awarded": row["xp_awarded"], "challenge": json.loads(row["challenge_json"])}
