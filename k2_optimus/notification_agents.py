"""
Macro News Notification Agent Pipeline — Two-stage validation:
  1. NotificationAgent — scores importance (1-10), filters noise
  2. AnalystAgent — checks source legitimacy, identifies affected sectors
"""
import time, uuid, re
from typing import Dict, Any, List, Optional

HIGH_TRUST_SOURCES = {
    "reuters", "bloomberg", "cnbc", "associated press", "ap news",
    "wall street journal", "wsj", "financial times", "ft",
    "federal reserve", "fed.gov", "bls.gov", "treasury.gov",
    "bureau of labor statistics", "sec.gov", "white house",
    "new york times", "washington post", "bbc news",
    "marketwatch", "barron's", "the economist",
}
MEDIUM_TRUST_SOURCES = {
    "yahoo finance", "investing.com", "seekingalpha", "benzinga",
    "fox business", "cnn business", "fortune", "business insider",
}
BREAKING_KEYWORDS = ["breaking", "just in", "alert", "emergency", "surprise", "unexpected", "shock", "crash", "surge", "plunge"]
HIGH_IMPACT_KEYWORDS = [
    "interest rate cut", "interest rate hike", "rate decision", "CPI", "inflation", "GDP", "recession",
    "unemployment", "FOMC", "Federal Reserve", "Fed chair", "Powell", "tariff", "trade war",
    "sanctions", "debt ceiling", "bank failure", "banking crisis", "market crash", "government shutdown",
]
MODERATE_IMPACT_KEYWORDS = [
    "jobs report", "nonfarm payroll", "consumer confidence", "housing starts", "retail sales", "PMI",
    "oil prices", "OPEC", "bond yield", "treasury", "supply chain", "chip shortage", "semiconductor",
]
SECTOR_KEYWORDS = {
    "Technology": ["tech", "semiconductor", "chip", "AI", "software", "FAANG", "big tech"],
    "Energy": ["oil", "OPEC", "energy", "gas", "petroleum", "crude", "renewable"],
    "Financials": ["bank", "banking", "financial", "lending", "credit", "loan", "mortgage"],
    "Healthcare": ["healthcare", "pharma", "drug", "FDA", "biotech"],
    "Real Estate": ["housing", "real estate", "mortgage", "home", "construction"],
    "Consumer": ["consumer", "retail", "spending", "CPI", "inflation", "grocery"],
    "All Sectors": ["interest rate", "Fed", "FOMC", "GDP", "recession", "economy", "tariff", "trade war", "government"],
}

class NotificationAgent:
    THRESHOLD = 5
    def evaluate(self, headline: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        title = headline.get("title", "")
        snippet = headline.get("snippet", "")
        combined = (title + " " + snippet).lower()
        score = 2  # Base score — already passed macro keyword filter
        if any(kw in combined for kw in BREAKING_KEYWORDS): score += 3
        high_matches = sum(1 for kw in HIGH_IMPACT_KEYWORDS if kw.lower() in combined)
        if high_matches > 0: score += min(4, high_matches * 2)
        mod_matches = sum(1 for kw in MODERATE_IMPACT_KEYWORDS if kw.lower() in combined)
        if mod_matches > 0: score += min(2, mod_matches)
        if high_matches + mod_matches >= 3: score += 1
        score = min(10, max(1, score))
        if score < self.THRESHOLD: return None
        return {**headline, "urgency_score": score, "passed_filter": True}

class AnalystAgent:
    def validate(self, filtered_headline: Dict[str, Any]) -> Dict[str, Any]:
        title = filtered_headline.get("title", "")
        snippet = filtered_headline.get("snippet", "")
        source_raw = filtered_headline.get("source", "Unknown")
        combined = (title + " " + snippet).lower()
        source_lower = source_raw.lower().strip()
        if any(ts in source_lower for ts in HIGH_TRUST_SOURCES):
            reliability, reliability_score = "high", 0.9
        elif any(ts in source_lower for ts in MEDIUM_TRUST_SOURCES):
            reliability, reliability_score = "medium", 0.7
        else:
            reliability, reliability_score = "low", 0.4
        affected = [s for s, kws in SECTOR_KEYWORDS.items() if any(k.lower() in combined for k in kws)]
        if not affected: affected = ["General Market"]
        urgency = filtered_headline.get("urgency_score", 5)
        urgency_label = "critical" if urgency >= 8 else ("important" if urgency >= 6 else "moderate")
        clean_title = re.sub(r'\s*-\s*[^-]+$', '', title).strip() if ' - ' in title else title
        summary = f"{'🚨 BREAKING: ' if urgency >= 8 else ''}{clean_title}. Source: {reliability}. "
        summary += "Affects entire market." if "All Sectors" in affected else f"Impacts: {', '.join(affected)}."
        return {
            "id": str(uuid.uuid4())[:8], "title": clean_title, "summary": summary,
            "urgency": urgency, "urgency_label": urgency_label,
            "affected_sectors": affected, "source": source_raw,
            "source_reliability": reliability, "source_reliability_score": reliability_score,
            "link": filtered_headline.get("link", ""), "timestamp": time.time(), "is_read": False,
        }

class NotificationPipeline:
    def __init__(self):
        self.notification_agent = NotificationAgent()
        self.analyst_agent = AnalystAgent()
    def process(self, raw_headlines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        alerts = []
        for headline in raw_headlines:
            filtered = self.notification_agent.evaluate(headline)
            if filtered is None: continue
            alerts.append(self.analyst_agent.validate(filtered))
        alerts.sort(key=lambda a: a["urgency"], reverse=True)
        return alerts

    def process_with_trace(self, raw_headlines: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Process headlines and return full trace for developer debugging."""
        trace = {
            "total_scanned": len(raw_headlines),
            "passed_filter": 0,
            "rejected": 0,
            "alerts_produced": 0,
            "headlines": [],
        }
        alerts = []
        for headline in raw_headlines:
            title = headline.get("title", "")
            snippet = headline.get("snippet", "")
            combined = (title + " " + snippet).lower()

            # Trace: NotificationAgent evaluation
            entry = {
                "title": title[:100],
                "source": headline.get("source", "Unknown"),
                "notification_agent": {"step": "NotificationAgent.evaluate"},
                "analyst_agent": None,
                "final_alert": None,
            }

            # Score breakdown
            score = 2
            breaking_matched = [kw for kw in BREAKING_KEYWORDS if kw in combined]
            high_matched = [kw for kw in HIGH_IMPACT_KEYWORDS if kw.lower() in combined]
            mod_matched = [kw for kw in MODERATE_IMPACT_KEYWORDS if kw.lower() in combined]

            if breaking_matched: score += 3
            if high_matched: score += min(4, len(high_matched) * 2)
            if mod_matched: score += min(2, len(mod_matched))
            if len(high_matched) + len(mod_matched) >= 3: score += 1
            score = min(10, max(1, score))

            entry["notification_agent"] = {
                "base_score": 2,
                "breaking_keywords": breaking_matched,
                "high_impact_keywords": high_matched,
                "moderate_impact_keywords": mod_matched,
                "final_score": score,
                "threshold": self.notification_agent.THRESHOLD,
                "passed": score >= self.notification_agent.THRESHOLD,
                "reasoning": f"Base(2) + Breaking({'+3' if breaking_matched else '0'}) + High({min(4, len(high_matched)*2)}) + Moderate({min(2, len(mod_matched))}) + Multi-match({'+1' if len(high_matched)+len(mod_matched)>=3 else '0'}) = {score}",
            }

            if score < self.notification_agent.THRESHOLD:
                entry["notification_agent"]["conclusion"] = f"REJECTED — score {score} < threshold {self.notification_agent.THRESHOLD}"
                trace["rejected"] += 1
                trace["headlines"].append(entry)
                continue

            trace["passed_filter"] += 1

            # Trace: AnalystAgent validation
            filtered = {**headline, "urgency_score": score, "passed_filter": True}
            alert = self.analyst_agent.validate(filtered)
            alerts.append(alert)

            source_lower = headline.get("source", "").lower().strip()
            entry["analyst_agent"] = {
                "source_checked": headline.get("source", "Unknown"),
                "reliability": alert["source_reliability"],
                "reliability_score": alert["source_reliability_score"],
                "affected_sectors": alert["affected_sectors"],
                "urgency_label": alert["urgency_label"],
                "conclusion": f"APPROVED — {alert['source_reliability']} trust source, urgency={alert['urgency']} ({alert['urgency_label']}), sectors: {', '.join(alert['affected_sectors'])}",
            }
            entry["final_alert"] = {
                "id": alert["id"],
                "title": alert["title"][:100],
                "urgency": alert["urgency"],
                "urgency_label": alert["urgency_label"],
                "sectors": alert["affected_sectors"],
                "source_reliability": alert["source_reliability"],
            }
            trace["headlines"].append(entry)

        alerts.sort(key=lambda a: a["urgency"], reverse=True)
        trace["alerts_produced"] = len(alerts)
        trace["alerts"] = alerts
        return trace

