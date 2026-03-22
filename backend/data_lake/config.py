"""
Configuration for the 15-year S&P 500 data lake ingestion.
"""
from __future__ import annotations

import os
from datetime import date

# ── Date range ────────────────────────────────────────────────────────────────
START_DATE = "2011-01-01"
END_DATE = str(date.today())

# ── Local storage paths ──────────────────────────────────────────────────────
DATA_DIR = os.environ.get("DATA_LAKE_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "data_lake_output"))
PRICES_DIR = os.path.join(DATA_DIR, "daily_prices")
FUNDAMENTALS_DIR = os.path.join(DATA_DIR, "quarterly_financials")
EVENTS_DIR = os.path.join(DATA_DIR, "events")
MACRO_DIR = os.path.join(DATA_DIR, "macro_history")
SUMMARIES_DIR = os.path.join(DATA_DIR, "rag_summaries")
CHECKPOINT_FILE = os.path.join(DATA_DIR, ".checkpoint.json")

# ── Google Drive ──────────────────────────────────────────────────────────────
GDRIVE_CREDENTIALS_FILE = os.environ.get("GDRIVE_CREDENTIALS_FILE", "credentials.json")
GDRIVE_FOLDER_NAME = os.environ.get("GDRIVE_FOLDER_NAME", "TradetalkDataLake")

# ── Rate limiting ─────────────────────────────────────────────────────────────
YFINANCE_BATCH_SIZE = 50
YFINANCE_SLEEP_BETWEEN_TICKERS = 1.5   # seconds between individual ticker calls
YFINANCE_SLEEP_BETWEEN_BATCHES = 10    # seconds between batch downloads
FRED_SLEEP = 1.0

# Yahoo Finance uses hyphens for class shares; our list uses dots (S&P style).
YFINANCE_SYMBOL_ALIASES = {
    "BRK.B": "BRK-B",
    "BF.B": "BF-B",
}


def yfinance_symbol(ticker: str) -> str:
    """Ticker as used by yfinance / Yahoo (hyphenated class shares)."""
    return YFINANCE_SYMBOL_ALIASES.get(ticker, ticker)

# ── S&P 500 Tickers (as of March 2026, plus notable historical removals) ─────
# Source: Wikipedia S&P 500 list. ~503 current + ~30 notable removed tickers.
SP500_TICKERS = [
    "A", "AAPL", "ABBV", "ABNB", "ABT", "ACGL", "ACN", "ADBE", "ADI", "ADM",
    "ADP", "ADSK", "AEE", "AEP", "AES", "AFL", "AIG", "AIZ", "AJG", "AKAM",
    "ALB", "ALGN", "ALL", "ALLE", "AMAT", "AMCR", "AMD", "AME", "AMGN", "AMP",
    "AMT", "AMZN", "ANET", "ANSS", "AON", "AOS", "APA", "APD", "APH", "APTV",
    "ARE", "ATO", "ATVI", "AVB", "AVGO", "AVY", "AWK", "AXP", "AZO",
    "BA", "BAC", "BAX", "BBWI", "BBY", "BDX", "BEN", "BF.B", "BG", "BIIB",
    "BIO", "BK", "BKNG", "BKR", "BLK", "BMY", "BR", "BRK.B", "BRO", "BSX",
    "BWA", "BXP",
    "C", "CAG", "CAH", "CARR", "CAT", "CB", "CBOE", "CBRE", "CCI", "CCL",
    "CDAY", "CDNS", "CDW", "CE", "CEG", "CF", "CFG", "CHD", "CHRW", "CHTR",
    "CI", "CINF", "CL", "CLX", "CMA", "CMCSA", "CME", "CMG", "CMI", "CMS",
    "CNC", "CNP", "COF", "COO", "COP", "COST", "CPB", "CPRT", "CPT", "CRL",
    "CRM", "CSCO", "CSGP", "CSX", "CTAS", "CTLT", "CTRA", "CTSH", "CTVA",
    "CVS", "CVX", "CZR",
    "D", "DAL", "DD", "DE", "DFS", "DG", "DGX", "DHI", "DHR", "DIS",
    "DISH", "DLR", "DLTR", "DOV", "DOW", "DPZ", "DRI", "DTE", "DUK", "DVA",
    "DVN", "DXC",
    "EA", "EBAY", "ECL", "ED", "EFX", "EIX", "EL", "EMN", "EMR", "ENPH",
    "EOG", "EPAM", "EQIX", "EQR", "EQT", "ES", "ESS", "ETN", "ETR", "ETSY",
    "EVRG", "EW", "EXC", "EXPD", "EXPE", "EXR",
    "F", "FANG", "FAST", "FBHS", "FCX", "FDS", "FDX", "FE", "FFIV", "FIS",
    "FISV", "FITB", "FLT", "FMC", "FOX", "FOXA", "FRC", "FRT",
    "FTNT", "FTV",
    "GD", "GE", "GEHC", "GEN", "GILD", "GIS", "GL", "GLW", "GM", "GNRC",
    "GOOG", "GOOGL", "GPC", "GPN", "GRMN", "GS", "GWW",
    "HAL", "HAS", "HBAN", "HCA", "PEAK", "HD", "HOLX", "HON", "HPE",
    "HPQ", "HRL", "HSIC", "HST", "HSY", "HUBB", "HUM", "HWM",
    "IBM", "ICE", "IDXX", "IEX", "IFF", "ILMN", "INCY", "INTC", "INTU",
    "INVH", "IP", "IPG", "IQV", "IR", "IRM", "ISRG", "IT", "ITW", "IVZ",
    "J", "JBHT", "JCI", "JKHY", "JNJ", "JNPR", "JPM",
    "K", "KDP", "KEY", "KEYS", "KHC", "KIM", "KLAC", "KMB", "KMI", "KMX",
    "KO", "KR",
    "L", "LDOS", "LEN", "LH", "LHX", "LIN", "LKQ", "LLY", "LMT", "LNC",
    "LNT", "LOW", "LRCX", "LULU", "LUV", "LVS", "LW", "LYB", "LYV",
    "MA", "MAA", "MAR", "MAS", "MCD", "MCHP", "MCK", "MCO", "MDLZ", "MDT",
    "MET", "META", "MGM", "MHK", "MKC", "MKTX", "MLM", "MMC", "MMM", "MNST",
    "MO", "MOH", "MOS", "MPC", "MPWR", "MRK", "MRNA", "MRO", "MS", "MSCI",
    "MSFT", "MSI", "MTB", "MTCH", "MTD", "MU",
    "NCLH", "NDAQ", "NDSN", "NEE", "NEM", "NFLX", "NI", "NKE", "NOC",
    "NOW", "NRG", "NSC", "NTAP", "NTRS", "NUE", "NVDA", "NVR", "NWL", "NWS",
    "NWSA",
    "O", "ODFL", "OGN", "OKE", "OMC", "ON", "ORCL", "ORLY", "OTIS", "OXY",
    "PARA", "PAYC", "PAYX", "PCAR", "PCG", "PEAK", "PEG", "PEP", "PFE",
    "PFG", "PG", "PGR", "PH", "PHM", "PKG", "PKI", "PLD", "PM", "PNC",
    "PNR", "PNW", "POOL", "PPG", "PPL", "PRU", "PSA", "PSX", "PTC", "PVH",
    "PWR", "PXD",
    "PYPL", "QCOM", "QRVO",
    "RCL", "RE", "REG", "REGN", "RF", "RHI", "RJF", "RL", "RMD", "ROK",
    "ROL", "ROP", "ROST", "RSG", "RTX",
    "SBAC", "SBNY", "SBUX", "SCHW", "SEE", "SHW", "SIVB", "SJM", "SLB",
    "SNA", "SNPS", "SO", "SPG", "SPGI", "SRE", "STE", "STT", "STX", "STZ",
    "SWK", "SWKS", "SYF", "SYK", "SYY",
    "T", "TAP", "TDG", "TDY", "TECH", "TEL", "TER", "TFC", "TFX", "TGT",
    "TMO", "TMUS", "TPR", "TRGP", "TRMB", "TROW", "TRV", "TSCO", "TSLA",
    "TSN", "TT", "TTWO", "TXN", "TXT", "TYL",
    "UDR", "UHS", "ULTA", "UNH", "UNP", "UPS", "URI", "USB",
    "V", "VFC", "VICI", "VLO", "VMC", "VRSK", "VRSN", "VRTX", "VTR",
    "VTRS", "VZ",
    "WAB", "WAT", "WBA", "WBD", "WDC", "WEC", "WELL", "WFC", "WHR", "WM",
    "WMB", "WMT", "WRB", "WRK", "WST", "WTW", "WY", "WYNN",
    "XEL", "XOM", "XRAY", "XYL",
    "YUM",
    "ZBH", "ZBRA", "ZION", "ZTS",
]

# Notable removed tickers for survivorship-bias coverage
HISTORICAL_REMOVED_TICKERS = [
    "FB",     # now META
    "TWTR",   # delisted (acquired)
    "GE",     # was dropped/readded
    "DOW",    # DowDuPont split
    "XRX",    # removed
    "FLR",    # removed
    "RTN",    # merged into RTX
    "UTX",    # merged into RTX
    "CELG",   # acquired by BMY
    "APC",    # acquired by OXY
    "ALXN",   # acquired by AZN
    "MXIM",   # acquired by ADI
    "XLNX",   # acquired by AMD
    "CTXS",   # taken private
    "CERN",   # acquired by ORCL
    "INFO",   # acquired
    "PBCT",   # acquired by M&T
    "KSU",    # merged into CP
    "NLSN",   # taken private
    "DRE",    # acquired by Prologis
]

# Combined list (deduplicated)
ALL_TICKERS = sorted(set(SP500_TICKERS + HISTORICAL_REMOVED_TICKERS))

# Small test set for --dry-run or --tickers flag
TEST_TICKERS = ["AAPL", "MSFT"]


def get_tickers(cli_tickers: str | None = None, dry_run: bool = False) -> list[str]:
    """Resolve ticker list from CLI args."""
    if dry_run:
        return TEST_TICKERS
    if cli_tickers:
        return [t.strip().upper() for t in cli_tickers.split(",")]
    return ALL_TICKERS


def ensure_dirs() -> None:
    """Create all output directories."""
    for d in [DATA_DIR, PRICES_DIR, FUNDAMENTALS_DIR, EVENTS_DIR, MACRO_DIR, SUMMARIES_DIR]:
        os.makedirs(d, exist_ok=True)


# ── Event Parquet layout ───────────────────────────────────────────────────────
# Preferred: events/{TICKER}_{kind}.parquet (flat). Legacy: events/{TICKER}/{kind}.parquet


def event_parquet_path(ticker: str, kind: str) -> str:
    """Flat path: data_lake_output/events/AAPL_earnings.parquet"""
    return os.path.join(EVENTS_DIR, f"{ticker}_{kind}.parquet")


def legacy_event_parquet_path(ticker: str, kind: str) -> str:
    """Legacy nested path."""
    return os.path.join(EVENTS_DIR, ticker, f"{kind}.parquet")


def resolve_event_parquet(ticker: str, kind: str) -> str | None:
    """Return path if flat or legacy file exists."""
    flat = event_parquet_path(ticker, kind)
    if os.path.isfile(flat):
        return flat
    leg = legacy_event_parquet_path(ticker, kind)
    if os.path.isfile(leg):
        return leg
    return None


def ticker_has_any_event_files(ticker: str) -> bool:
    """True if flat *_earnings.parquet etc. exists or legacy dir has parquet."""
    for kind in ("earnings", "splits", "dividends", "insider", "institutional", "major_holders", "recommendations"):
        if resolve_event_parquet(ticker, kind):
            return True
    leg_dir = os.path.join(EVENTS_DIR, ticker)
    if os.path.isdir(leg_dir):
        for f in os.listdir(leg_dir):
            if f.endswith(".parquet"):
                return True
    return False
