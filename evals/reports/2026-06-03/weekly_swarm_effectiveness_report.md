# Weekly Swarm Effectiveness Report

Date: 2026-06-03
Run ID: eval_20260603_161335
Production Version: v1.0.0
Benchmark Suite: agentic_swarm_eval_v1

## 1. Executive Decision

Winner: Production 5-Agent Swarm
Decision: hold

Summary:
Keep production swarm as baseline until a candidate clearly wins.

## 2. Score Summary

| Variant | AES | Task Success | RAG Quality | Orchestration | Learning | Efficiency | Safety | Maintainability | p95 Latency | Cost | Decision |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Production 5-Agent Swarm | 86.0 | 88.0 | 90.0 | 88.0 | 75.0 | 80.0 | 95.0 | 78.0 | 4800.0 | 0.1 | ok |
| Single-Agent RAG | 79.95 | 82.0 | 87.0 | 74.0 | 62.0 | 82.0 | 89.0 | 84.0 | 3300.0 | 0.06 | ok |
| Planner-Executor | 80.9 | 84.0 | 85.0 | 79.0 | 65.0 | 79.0 | 90.0 | 80.0 | 3900.0 | 0.08 | ok |
| Reduced Swarm + Current LLM | 83.95 | 87.0 | 89.0 | 83.0 | 73.0 | 79.0 | 92.0 | 78.0 | 4100.0 | 0.08 | ok |
| Production swarm without critic | 80.65 | 84.0 | 88.0 | 80.0 | 73.0 | 71.0 | 88.0 | 74.0 | 5000.0 | 0.1 | ok |
| Production swarm without reflection loop | 81.6 | 85.0 | 88.0 | 82.0 | 70.0 | 73.0 | 90.0 | 76.0 | 4700.0 | 0.095 | ok |
| Production swarm without RRF memory retrieval | 80.6 | 82.0 | 82.0 | 86.0 | 70.0 | 74.0 | 88.0 | 74.0 | 5100.0 | 0.1 | ok |
| Production swarm without Nightly Mutation Engine | 81.95 | 85.0 | 89.0 | 86.0 | 68.0 | 70.0 | 92.0 | 73.0 | 5000.0 | 0.1 | ok |
| Production swarm without CORAL / Meta-LLM | 80.4 | 83.0 | 87.0 | 80.0 | 71.0 | 72.0 | 89.0 | 76.0 | 4800.0 | 0.095 | ok |

## 3. Swarm Advantage

Production Swarm Score: 86.0
Best Simpler Baseline Score: 83.95
Swarm Advantage Score: 2.05

Recommendation:
Keep production swarm as baseline until a candidate clearly wins.

## 4. Complexity Tax

Latency Tax: 0.0
Cost Tax: 0.0
Failure Tax: 0.0
Maintenance Tax: 0.0

Overall Complexity Tax: Low

## 5. Component Ablation Results

| Component | With Score | Without Score | Delta | Recommendation |
|---|---:|---:|---:|---|
| Critic agent | 86.0 | 80.65 | 5.35 | Keep always-on |
| Reflection loop | 86.0 | 81.6 | 4.4 | Make conditional |
| RRF memory retrieval | 86.0 | 80.6 | 5.4 | Keep always-on |
| Nightly Mutation Engine | 86.0 | 81.95 | 4.05 | Make conditional |
| CORAL / Meta-LLM | 86.0 | 80.4 | 5.6 | Keep always-on |

## 6. Safety and Tool-Call Findings

Hallucination Rate: 0.02
Critical Hallucinations: 0
Fabricated Tool-Call Claims: 0
Tool-Call Validity: 0.95
Citation Validity: 0.9

## 7. Dashboard Notification

Status: pass
Dashboard Badge: Eval: Pass
Summary File: /public/dashboard/eval-summary.json

## 8. Missing Data / Skipped Tests

- none

## 9. Recommended Actions

1. Keep production swarm as baseline until a candidate clearly wins.
2. Reflection loop: Make conditional.
3. Nightly Mutation Engine: Make conditional.
