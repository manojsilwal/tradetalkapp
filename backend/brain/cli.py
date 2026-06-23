"""Offline end-to-end demo of the finance brain.

Trains both candidate models on synthetic data, registers them, ranks a universe
with the best one, and prints a grounded explanation for the top pick. No network.

    PYTHONPATH=. python -m backend.brain.cli
    PYTHONPATH=. python -m backend.brain.cli --model logreg --top 5
"""
from __future__ import annotations

import argparse
import tempfile

from . import agent_explainer, dataset, pipeline
from .inference import InferenceEngine
from .model_registry import ModelRegistry
from .ports.local_adapters import LocalStorage


def main() -> None:
    ap = argparse.ArgumentParser(description="Finance brain offline demo")
    ap.add_argument("--model", default="finrank-net", choices=["finrank-net", "logreg"])
    ap.add_argument("--tickers", type=int, default=80)
    ap.add_argument("--periods", type=int, default=20)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    panel = dataset.synthetic_panel(n_tickers=args.tickers, n_periods=args.periods, seed=args.seed)
    registry = ModelRegistry(root="artifacts", storage=LocalStorage(tempfile.mkdtemp()))

    print("Training + validating models (purged CV + backtest with costs)...\n")
    best = None
    for name in ("logreg", "finrank-net"):
        summ = pipeline.train_and_register(panel, version="v1", registry=registry, model_name=name)
        v, bt = summ["metrics"]["validation"], summ["metrics"]["backtest"]
        print(f"  {name:12s} AUC={v['auc']:.3f}  acc={v['accuracy']:.3f}  "
              f"base={v['base_rate']:.3f} | backtest sharpe={bt['sharpe']:.2f}  "
              f"ann_excess={bt['annualized_excess']:+.3%}  hit={bt['hit_rate']:.0%}")
        if best is None or v["auc"] > best[1]:
            best = (name, v["auc"])
    print(f"\nBest validated model: {best[0]} (AUC {best[1]:.3f})\n")

    eng = InferenceEngine(registry, args.model, "v1")
    rows = panel["rows"][: args.tickers]
    tickers = panel["tickers"][: args.tickers]
    ranked = eng.rank_universe(rows, tickers, as_of_date="2026-06-22")

    print(f"Top {args.top} ranked by outperformance probability ({args.model}):")
    print(f"  {'ticker':8s} {'P(outperf)':>11s} {'composite':>10s} {'momentum':>9s} {'risk':>6s}")
    for c in ranked[: args.top]:
        print(f"  {c['ticker']:8s} {c['outperform_probability']:>11.3f} "
              f"{c['composite_score']:>10.1f} {c['signal_scores']['momentum']:>9.1f} "
              f"{c['risk_score']:>6.2f}")

    top = ranked[0]
    text = agent_explainer.generate_explanation(top)
    check = agent_explainer.verify_grounding(text, top)
    print(f"\nExplanation (grounded={check['grounded']}, numbers checked={check['checked']}):")
    print(f"  {text}")


if __name__ == "__main__":
    main()
