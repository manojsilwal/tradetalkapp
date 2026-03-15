"""
Investor Learning Path — 5 levels × 5 modules each.
Each module ties directly to an existing app feature with guided steps + a quiz.
Progress is stored in SQLite.
"""
import sqlite3
import json
import os
import threading
from typing import Any, Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "progress.db")
_local  = threading.local()


def _get_conn():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_learning_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS learning_progress (
            module_id    TEXT PRIMARY KEY,
            completed    INTEGER DEFAULT 0,
            score        INTEGER DEFAULT 0,
            completed_at TEXT DEFAULT NULL
        );
    """)
    conn.commit()


CURRICULUM: List[Dict] = [
    # ── Level 1: Value Basics ─────────────────────────────────────────────────
    {
        "id": "L1M1", "level": 1, "level_title": "Value Basics", "module": 1,
        "title": "What is a P/E Ratio?",
        "description": "Learn the most widely used valuation metric and how to apply it.",
        "app_feature": "consumer",
        "guided_steps": [
            "Open the Valuation Dashboard and enter ticker AAPL",
            "Find the 'Valuation & Cash Flow' section — note the P/E ratio",
            "A P/E of 25 means investors pay $25 for every $1 of earnings",
            "Compare AAPL's P/E to the S&P 500 average (~21) to judge relative value",
        ],
        "quiz": [
            {"q": "A P/E ratio of 10 vs sector average of 20 suggests the stock is:", "opts": ["Overvalued","Undervalued","Fairly valued","Unprofitable"], "a": 1},
            {"q": "P/E stands for:", "opts": ["Price-to-Earnings","Profit-to-Equity","Price-to-EBITDA","Performance-to-Equity"], "a": 0},
            {"q": "Growth stocks typically have:", "opts": ["Low P/E ratios","High P/E ratios","No P/E ratio","Negative earnings only"], "a": 1},
            {"q": "Which industry typically has the LOWEST P/E ratios?", "opts": ["Technology","Banks/Financials","Biotech","Software"], "a": 1},
            {"q": "If earnings double and price stays flat, P/E will:", "opts": ["Double","Halve","Stay the same","Triple"], "a": 1},
        ],
        "xp": 25,
    },
    {
        "id": "L1M2", "level": 1, "level_title": "Value Basics", "module": 2,
        "title": "Free Cash Flow Yield",
        "description": "Why FCF Yield reveals what earnings can't — the real cash a business generates.",
        "app_feature": "consumer",
        "guided_steps": [
            "Open the Valuation Dashboard, enter ticker MSFT",
            "In 'Valuation & Cash Flow', find FCF Yield",
            "FCF Yield = Free Cash Flow / Market Cap × 100%",
            "Higher FCF Yield = more cash generated relative to price — a value signal",
            "Compare FCF Yield to a 10-year Treasury bond yield (~4.5%) as a benchmark",
        ],
        "quiz": [
            {"q": "FCF Yield is calculated as:", "opts": ["Net Income/Revenue","FCF/Market Cap","EBITDA/EV","Revenue/Price"], "a": 1},
            {"q": "A FCF Yield of 6% vs Treasury yield of 4.5% suggests:", "opts": ["The stock is expensive","The stock offers an attractive cash yield premium","The company has no earnings","The P/E is low"], "a": 1},
            {"q": "Free Cash Flow is best described as:", "opts": ["Net income after tax","Cash after operating expenses and capex","Total revenue","Gross profit"], "a": 1},
            {"q": "Which company type typically has the HIGHEST FCF Yield?", "opts": ["Early-stage biotech","Mature consumer staples","Pre-revenue startups","Unprofitable growth companies"], "a": 1},
            {"q": "If FCF doubles while market cap stays flat, FCF Yield will:", "opts": ["Halve","Double","Stay the same","Fall to zero"], "a": 1},
        ],
        "xp": 25,
    },
    {
        "id": "L1M3", "level": 1, "level_title": "Value Basics", "module": 3,
        "title": "Reading the Macro Dashboard",
        "description": "Understand VIX, credit stress, and market regime — the macro context every investor needs.",
        "app_feature": "macro",
        "guided_steps": [
            "Open the Global Macro tab",
            "Note the VIX level — above 20 = elevated fear, above 30 = high fear",
            "Check the Credit Stress Index — above 1.5 signals stress in credit markets",
            "Review Sector Rotation — which sectors are gaining/losing institutional flows?",
            "Use macro context to calibrate your valuation: bear regimes deserve lower multiples",
        ],
        "quiz": [
            {"q": "VIX is often called:", "opts": ["The value index","The fear gauge","The momentum indicator","The credit index"], "a": 1},
            {"q": "A VIX above 30 typically signals:", "opts": ["Low volatility","Market complacency","High fear / uncertainty","A bull market"], "a": 2},
            {"q": "In a BEAR_STRESS macro regime, you should:", "opts": ["Increase leverage","Apply a lower valuation multiple","Ignore fundamentals","Buy all dips"], "a": 1},
            {"q": "Sector rotation into Utilities typically signals:", "opts": ["Risk appetite increasing","Defensive positioning / risk-off","Technology boom","Inflation rising"], "a": 1},
            {"q": "Credit stress above 1.5 most commonly leads to:", "opts": ["Higher P/E multiples","Market selloffs or volatility","Bull market rally","Lower VIX"], "a": 1},
        ],
        "xp": 25,
    },
    {
        "id": "L1M4", "level": 1, "level_title": "Value Basics", "module": 4,
        "title": "Your First AI Debate",
        "description": "Run a 5-agent debate and learn how to interpret conflicting analyst perspectives.",
        "app_feature": "debate",
        "guided_steps": [
            "Open the AI Debate tab",
            "Enter ticker KO (Coca-Cola) — a classic value stock",
            "Wait for all 5 agents: Bull, Bear, Macro, Value, Momentum",
            "Notice the score ring — bull score vs bear score",
            "Read the moderator summary — this is the synthesised verdict",
            "A high bull score + low bear score = strong consensus",
        ],
        "quiz": [
            {"q": "In the AI Debate, the Moderator agent's role is to:", "opts": ["Always pick the bull side","Synthesise all perspectives into a verdict","Generate trading signals","Replace human analysis"], "a": 1},
            {"q": "A debate score of Bull: 4, Bear: 1 suggests:", "opts": ["Strong consensus for selling","Strong consensus for buying","A neutral market","Technical breakdown"], "a": 1},
            {"q": "The Value Investor agent focuses primarily on:", "opts": ["Price momentum","Short-term news","Intrinsic value vs market price","Options flow"], "a": 2},
            {"q": "Why might the Bear analyst be correct even with a high bull score?", "opts": ["Bears are always right","New information or tail risks could materialise","The AI makes errors","Bulls always win"], "a": 1},
            {"q": "RAG (Retrieval Augmented Generation) helps debate agents by:", "opts": ["Searching the internet live","Grounding responses in stored historical analyses","Making the agents faster","Reducing costs"], "a": 1},
        ],
        "xp": 25,
    },
    {
        "id": "L1M5", "level": 1, "level_title": "Value Basics", "module": 5,
        "title": "Level 1 Assessment",
        "description": "Test your understanding of the fundamentals before advancing.",
        "app_feature": None,
        "guided_steps": [],
        "quiz": [
            {"q": "Which metric compares a stock's price to its annual earnings?", "opts": ["EV/EBITDA","P/E ratio","FCF Yield","Debt/Equity"], "a": 1},
            {"q": "A stock with P/E of 8 vs sector P/E of 20 is most likely:", "opts": ["A growth stock","A potentially undervalued stock","Definitely a bad investment","In a bubble"], "a": 1},
            {"q": "FCF Yield tells you:", "opts": ["Net income growth","Cash generated per dollar of market cap","Revenue trends","Dividend safety"], "a": 1},
            {"q": "VIX above 30 typically means:", "opts": ["Low fear","High market fear / volatility","Bull market","Fed is cutting rates"], "a": 1},
            {"q": "The AI Debate Moderator synthesises:", "opts": ["Only the bull's view","All agent perspectives","Market data","Historical prices"], "a": 1},
            {"q": "A BEAR_STRESS regime should cause you to:", "opts": ["Increase leverage","Pay lower multiples for stocks","Ignore macro signals","Only buy growth stocks"], "a": 1},
            {"q": "Margin of Safety means:", "opts": ["Buying any stock on a dip","Buying below intrinsic value for protection","The profit margin","Safety stops in trading"], "a": 1},
            {"q": "Free Cash Flow is calculated as:", "opts": ["Revenue − COGS","Operating cash flow − capex","Net income + depreciation","EBITDA × tax rate"], "a": 1},
            {"q": "Sector rotation into Energy and Commodities typically signals:", "opts": ["Deflation","Inflationary environment","Tech boom","Rate cuts"], "a": 1},
            {"q": "The P/E ratio is most useful when compared to:", "opts": ["Its own price","Industry peers and historical averages","Dividend yield","Debt levels"], "a": 1},
        ],
        "xp": 50,
        "is_assessment": True,
        "pass_score": 7,
    },

    # ── Level 2: Intermediate Analysis ────────────────────────────────────────
    {
        "id": "L2M1", "level": 2, "level_title": "Intermediate Analysis", "module": 1,
        "title": "ROIC — The Moat Metric",
        "description": "Return on Invested Capital reveals companies with durable competitive advantages.",
        "app_feature": "consumer",
        "guided_steps": [
            "Open Valuation Dashboard, enter ticker V (Visa)",
            "Find 'Profitability & Moat' — note ROIC/ROE values",
            "Visa's ROIC consistently exceeds 30% — a sign of a wide moat",
            "Compare to a commodity business (try X — US Steel): much lower ROIC",
            "Rule of thumb: ROIC > 15% sustained for 5+ years = likely moat",
        ],
        "quiz": [
            {"q": "ROIC stands for:", "opts": ["Return on Invested Capital","Rate of Inflation Control","Return on Individual Contracts","Revenue over Income Capital"], "a": 0},
            {"q": "A consistently high ROIC (>15%) suggests:", "opts": ["The company has high debt","A durable competitive moat","The stock is cheap","Revenue is growing"], "a": 1},
            {"q": "Which company type has the HIGHEST expected ROIC?", "opts": ["Capital-intensive manufacturer","Asset-light software/payments company","Mining company","Airlines"], "a": 1},
            {"q": "ROIC is compared to WACC to determine:", "opts": ["Revenue growth","Whether the company is creating or destroying value","P/E ratio","Cash flow timing"], "a": 1},
            {"q": "If ROIC < WACC, the company is:", "opts": ["Creating value","Destroying shareholder value","Highly profitable","Paying dividends"], "a": 1},
        ],
        "xp": 30,
    },
    {
        "id": "L2M2", "level": 2, "level_title": "Intermediate Analysis", "module": 2,
        "title": "Backtesting a PE Strategy",
        "description": "Use the Strategy Lab to test the classic P/E-based buy low, sell high approach.",
        "app_feature": "backtest",
        "guided_steps": [
            "Open Strategy Lab",
            "Enter: 'Buy Mag7 stocks when P/E is below 25, sell when P/E exceeds 35'",
            "Set date range: 2015-01-01 to 2024-01-01",
            "Run the backtest and review CAGR vs SPY benchmark",
            "Check the Transaction Log — each buy shows entry PE, each sell shows exit PE",
            "A strategy that beats SPY with lower Max Drawdown is considered superior",
        ],
        "quiz": [
            {"q": "CAGR stands for:", "opts": ["Compound Annual Growth Rate","Cost Adjusted Gain Ratio","Capital Allocation Growth Rate","Cumulative Asset Growth Rate"], "a": 0},
            {"q": "Max Drawdown measures:", "opts": ["Maximum profit","Peak-to-trough loss before recovery","Average annual return","Volatility only"], "a": 1},
            {"q": "A strategy with CAGR of 18% vs SPY's 12% with higher Sharpe is:", "opts": ["Inferior","Superior on a risk-adjusted basis","Too risky","Not worth considering"], "a": 1},
            {"q": "Why might a PE<25 buy signal underperform in a rising rate environment?", "opts": ["PE ratios are irrelevant","Higher rates increase discount rates, compressing multiples","Bonds become less attractive","Growth stocks benefit"], "a": 1},
            {"q": "Win Rate in a backtest represents:", "opts": ["% of months the portfolio was positive","% of individual trades that were profitable","% of days above SPY","% of signals triggered"], "a": 1},
        ],
        "xp": 30,
    },
    {
        "id": "L2M3", "level": 2, "level_title": "Intermediate Analysis", "module": 3,
        "title": "Short Interest as a Signal",
        "description": "Understand how short interest data reveals what sophisticated investors think.",
        "app_feature": "observer",
        "guided_steps": [
            "Open Developer Trace",
            "Enter GME or AMC — historically high short interest stocks",
            "Review the Short Interest agent — note % of float short",
            "Short interest > 20% = heavy bearish positioning",
            "Short interest > 30% + positive catalyst = potential squeeze setup",
            "Cross-reference with the Social Sentiment agent for crowd vs. institution divergence",
        ],
        "quiz": [
            {"q": "Short interest as % of float above 20% signals:", "opts": ["Strong institutional buying","Heavy bearish sentiment","Market neutrality","Low trading volume"], "a": 1},
            {"q": "A short squeeze happens when:", "opts": ["Shorts add to their positions","Shorts are forced to cover, pushing price up","Volume decreases","Price stays flat"], "a": 1},
            {"q": "The 'days to cover' metric measures:", "opts": ["How long until earnings","How many days of average volume needed to cover all shorts","Dividend payment timing","Debt maturity"], "a": 1},
            {"q": "High short interest combined with a positive earnings surprise often leads to:", "opts": ["Price decline","A short squeeze","Neutral price action","Increased short positions"], "a": 1},
            {"q": "Institutional short sellers typically have:", "opts": ["Less information than retail","Deep fundamental research","Only momentum signals","No P&L accountability"], "a": 1},
        ],
        "xp": 30,
    },
    {
        "id": "L2M4", "level": 2, "level_title": "Intermediate Analysis", "module": 4,
        "title": "Moving Averages & Momentum",
        "description": "How trend-following strategies use price history to time entries and exits.",
        "app_feature": "backtest",
        "guided_steps": [
            "Open Strategy Lab",
            "Enter: 'Buy stocks in the S&P 500 trading above their 200-day moving average'",
            "Run from 2010-01-01 to 2024-01-01",
            "Note: this is a trend-following filter, not a valuation screen",
            "Compare Sharpe ratio to the PE-based strategy from L2M2",
            "Trend strategies often have lower drawdowns during bear markets",
        ],
        "quiz": [
            {"q": "The 200-day moving average is used to identify:", "opts": ["Earnings trends","Long-term price trend direction","Short-term volatility","Dividend timing"], "a": 1},
            {"q": "Price above 200-DMA typically signals:", "opts": ["Downtrend","Uptrend / bullish regime","No trend","Market crash incoming"], "a": 1},
            {"q": "Momentum investing assumes:", "opts": ["Markets are always mean-reverting","Recent winners continue to outperform in the short term","Value always beats momentum","High P/E is always bad"], "a": 1},
            {"q": "The Golden Cross is when:", "opts": ["50-DMA crosses above 200-DMA","Stock hits all-time high","P/E drops to historic low","Volume doubles"], "a": 0},
            {"q": "Trend following strategies typically underperform during:", "opts": ["Strong bull markets","Choppy / sideways markets","Recessions","Rate hikes"], "a": 1},
        ],
        "xp": 30,
    },
    {
        "id": "L2M5", "level": 2, "level_title": "Intermediate Analysis", "module": 5,
        "title": "Level 2 Assessment",
        "description": "Demonstrate mastery of intermediate analysis concepts.",
        "app_feature": None,
        "guided_steps": [],
        "quiz": [
            {"q": "ROIC > 15% sustained for 5+ years suggests:", "opts": ["Overvaluation","A competitive moat","High debt","Declining margins"], "a": 1},
            {"q": "CAGR of a backtest is:", "opts": ["Total return divided by years","The compound annual return","Annualised alpha vs SPY","Monthly return × 12"], "a": 1},
            {"q": "Max Drawdown of -15% means:", "opts": ["Average loss per trade","Peak-to-trough portfolio decline of 15%","15% of trades lost","Annual return was -15%"], "a": 1},
            {"q": "A stock trading below its 200-DMA is generally in:", "opts": ["An uptrend","A downtrend","A consolidation phase","Fair value"], "a": 1},
            {"q": "Short interest > 30% + positive catalyst =", "opts": ["High risk of bankruptcy","Potential short squeeze","Low volatility","Institutional selling"], "a": 1},
            {"q": "Sharpe Ratio > 1 is considered:", "opts": ["Poor","Acceptable","Good","Exceptional"], "a": 2},
            {"q": "ROIC should be compared to:", "opts": ["P/E ratio","WACC (cost of capital)","Revenue growth","Short interest"], "a": 1},
            {"q": "Golden Cross (50-DMA cross 200-DMA) is a:", "opts": ["Bearish signal","Bullish momentum signal","Valuation signal","Dividend signal"], "a": 1},
        ],
        "xp": 50,
        "is_assessment": True,
        "pass_score": 6,
    },
]


def get_curriculum() -> Dict[str, Any]:
    """Return full curriculum with completion status for each module."""
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM learning_progress").fetchall()
    completed = {r["module_id"]: dict(r) for r in rows}

    levels: Dict[int, Any] = {}
    for mod in CURRICULUM:
        lvl = mod["level"]
        if lvl not in levels:
            levels[lvl] = {"level": lvl, "title": mod["level_title"], "modules": []}
        prog = completed.get(mod["id"], {})
        levels[lvl]["modules"].append({
            **{k: v for k, v in mod.items() if k != "quiz"},
            "quiz_count":  len(mod.get("quiz", [])),
            "completed":   bool(prog.get("completed", 0)),
            "score":       prog.get("score", 0),
            "completed_at": prog.get("completed_at"),
        })

    # Compute lock status — a module is locked if previous module not completed
    for lvl_data in levels.values():
        for i, m in enumerate(lvl_data["modules"]):
            if i == 0 and lvl_data["level"] == 1:
                m["locked"] = False
            elif i == 0:
                # First module of level N: unlocked if all level N-1 modules done
                prev_level = lvl_data["level"] - 1
                prev_mods  = levels.get(prev_level, {}).get("modules", [])
                m["locked"] = not all(pm["completed"] for pm in prev_mods)
            else:
                m["locked"] = not lvl_data["modules"][i - 1]["completed"]

    return {"levels": list(levels.values()), "total_modules": len(CURRICULUM)}


def get_module(module_id: str) -> Optional[Dict]:
    mod = next((m for m in CURRICULUM if m["id"] == module_id), None)
    if not mod:
        return None
    conn = _get_conn()
    row  = conn.execute(
        "SELECT * FROM learning_progress WHERE module_id=?", (module_id,)
    ).fetchone()
    return {**mod, "progress": dict(row) if row else None}


def complete_module(module_id: str, score: int) -> Dict[str, Any]:
    """Mark a module complete, return XP awarded."""
    mod = next((m for m in CURRICULUM if m["id"] == module_id), None)
    if not mod:
        return {"error": "Module not found"}

    pass_score = mod.get("pass_score", 3)
    passed     = score >= pass_score
    xp         = mod.get("xp", 25) if passed else 5

    from datetime import date as _date
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO learning_progress (module_id, completed, score, completed_at)
        VALUES (?, ?, ?, ?)
    """, (module_id, 1 if passed else 0, score, _date.today().isoformat()))
    conn.commit()

    return {"passed": passed, "score": score, "xp_awarded": xp, "module": mod["title"]}
