"""Theme edges for graph_edges (source_category -> target_category)."""
from __future__ import annotations

from typing import List, Tuple

# (source_category_id, target_category_id, relationship_type, lag_days, base_strength, description)
AI_INFRA_CHAIN: Tuple[Tuple[str, str, str, int, float, str], ...] = (
    ("ai_infra", "cloud_software", "demand_pull", 5, 0.85, "GPU / networking demand pulls cloud spend"),
    ("ai_infra", "financials", "liquidity_cycle", 21, 0.45, "Cap-ex cycles interact with credit conditions"),
    ("cloud_software", "financials", "enterprise_spend", 14, 0.55, "IT budgets and rate sensitivity"),
)

RISK_ON_ROTATION: Tuple[Tuple[str, str, str, int, float, str], ...] = (
    ("energy_materials", "consumer_health", "cost_pass_through", 30, 0.40, "Input costs vs discretionary"),
    ("financials", "defensive", "flight_to_quality", 7, 0.50, "Credit stress favors defensives"),
    ("ai_infra", "consumer_health", "wealth_effect", 42, 0.30, "Long-horizon wealth / labor spillovers"),
)


def edges_for_sqlite() -> List[Tuple[str, str, str, str, int, float, str]]:
    """Return rows suitable for graph_edges INSERT (edge_id, src, tgt, rel, lag, strength, desc)."""
    out: List[Tuple[str, str, str, str, int, float, str]] = []
    for chain in (AI_INFRA_CHAIN, RISK_ON_ROTATION):
        for src, tgt, rel, lag, strength, desc in chain:
            eid = f"{src}__{tgt}__{rel}"
            out.append((eid, src, tgt, rel, lag, strength, desc))
    return out
