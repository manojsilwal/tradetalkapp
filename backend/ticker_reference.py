"""
Bundled ticker reference (ticker -> company name / sector / liquidity signals).

Used by the 13F security mapper to:
  - enrich a resolved ticker with sector + company name (no network), and
  - fall back to issuer-name matching when a CUSIP cannot be resolved.

Data is built from a wide ticker CSV via scripts/build_ticker_reference.py into
two bundled JSON files:
  - data/ticker_reference.json   {TICKER: {company_name, sector, ...}}
  - data/ticker_name_index.json  {normalized_company_name: TICKER}
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent / "data"
_REF_PATH = _DATA_DIR / "ticker_reference.json"
_INDEX_PATH = _DATA_DIR / "ticker_name_index.json"

# Legal-entity suffixes and share-class phrases stripped from issuer names before
# matching. Order matters: longer phrases first so they are removed wholesale.
_NAME_NOISE = [
    "class a ordinary shares",
    "class b ordinary shares",
    "ordinary shares",
    "common shares",
    "common stock",
    "depositary shares",
    "american depositary shares",
    "class a common stock",
    "class b common stock",
    "class c common stock",
    "class a",
    "class b",
    "class c",
    "new common stock",
    "the",
]

# Whole-word legal suffixes (matched at word boundaries to avoid mangling names
# like CORP -> ORATION).
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd",
    "limited", "llc", "lp", "plc", "sa", "ag", "nv", "se", "spa", "as",
    "holding", "holdings", "group", "trust", "fund", "reit",
}


def normalize_ticker(ticker: str) -> str:
    """Uppercase + trim; normalize class separators to '-' (e.g. BRK.B -> BRK-B)."""
    return (ticker or "").strip().upper().replace(".", "-")


def normalize_issuer_name(name: str) -> str:
    """Normalize an issuer / company name for fuzzy matching.

    Lowercases, strips punctuation, removes share-class phrases and trailing
    legal suffixes, and collapses whitespace.
    """
    s = (name or "").lower()
    # Drop anything after a dash that typically introduces a class descriptor.
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""

    for phrase in _NAME_NOISE:
        s = re.sub(rf"\b{re.escape(phrase)}\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    tokens = [t for t in s.split(" ") if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


@lru_cache(maxsize=1)
def _load_reference() -> Dict[str, Dict[str, Any]]:
    try:
        raw = json.loads(_REF_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[TickerRef] reference missing (%s): %s", _REF_PATH, e)
        return {}
    return {normalize_ticker(k): v for k, v in raw.items()}


@lru_cache(maxsize=1)
def _load_name_index() -> Dict[str, str]:
    try:
        raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("[TickerRef] name index missing (%s): %s", _INDEX_PATH, e)
        return {}
    return {k: normalize_ticker(v) for k, v in raw.items()}


@lru_cache(maxsize=1)
def _token_postings() -> Dict[str, List[str]]:
    """Inverted index: token -> [normalized_name, ...] for token-overlap scoring."""
    postings: Dict[str, List[str]] = {}
    for norm_name in _load_name_index():
        for tok in set(norm_name.split(" ")):
            if tok:
                postings.setdefault(tok, []).append(norm_name)
    return postings


def get_ticker_meta(ticker: str) -> Optional[Dict[str, Any]]:
    """Return {company_name, sector, priority_score, ...} for a ticker, or None."""
    sym = normalize_ticker(ticker)
    if not sym:
        return None
    row = _load_reference().get(sym)
    return dict(row) if row else None


def lookup_by_issuer_name(name: str, min_overlap: float = 0.6) -> Optional[str]:
    """Resolve a 13F issuer name to a ticker via the bundled name index.

    Tries an exact normalized match first, then token-overlap scoring against
    candidate names that share at least one token. Returns the best ticker whose
    overlap ratio meets ``min_overlap``, breaking ties by priority_score.
    """
    norm = normalize_issuer_name(name)
    if not norm:
        return None

    index = _load_name_index()
    exact = index.get(norm)
    if exact:
        return exact

    query_tokens = set(norm.split(" "))
    if not query_tokens:
        return None

    postings = _token_postings()
    candidates: set[str] = set()
    for tok in query_tokens:
        candidates.update(postings.get(tok, ()))
    if not candidates:
        return None

    reference = _load_reference()
    best_ticker: Optional[str] = None
    best_score = 0.0
    best_priority = -1.0

    for cand_name in candidates:
        cand_tokens = set(cand_name.split(" "))
        if not cand_tokens:
            continue
        overlap = len(query_tokens & cand_tokens) / len(query_tokens | cand_tokens)
        if overlap < min_overlap:
            continue
        ticker = index.get(cand_name)
        if not ticker:
            continue
        priority = (reference.get(ticker, {}) or {}).get("priority_score") or 0.0
        if overlap > best_score or (overlap == best_score and priority > best_priority):
            best_ticker = ticker
            best_score = overlap
            best_priority = priority

    return best_ticker
