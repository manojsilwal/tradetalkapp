# Weekly Swarm Effectiveness Report

Date: 2026-06-19
Run ID: eval_20260619_012743
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
| Production 5-Agent Swarm | 91.65 | 93.0 | 96.0 | 91.0 | 87.0 | 86.0 | 99.0 | 86.0 | 4800.0 | 0.1 | ok |
| Single-Agent RAG | 82.2 | 85.0 | 93.0 | 74.0 | 62.0 | 82.0 | 95.0 | 84.0 | 3300.0 | 0.06 | ok |
| Planner-Executor | 80.9 | 84.0 | 85.0 | 79.0 | 65.0 | 79.0 | 90.0 | 80.0 | 3900.0 | 0.08 | ok |
| Reduced Swarm + Current LLM | 83.95 | 87.0 | 89.0 | 83.0 | 73.0 | 79.0 | 92.0 | 78.0 | 4100.0 | 0.08 | ok |
| Production swarm without critic | 80.85 | 84.0 | 88.0 | 80.0 | 73.0 | 71.0 | 90.0 | 74.0 | 5000.0 | 0.1 | ok |
| Production swarm without reflection loop | 81.6 | 85.0 | 88.0 | 82.0 | 70.0 | 73.0 | 90.0 | 76.0 | 4700.0 | 0.095 | ok |
| Production swarm without RRF memory retrieval | 81.8 | 84.0 | 84.0 | 86.0 | 70.0 | 74.0 | 92.0 | 74.0 | 5100.0 | 0.1 | ok |
| Production swarm without Nightly Mutation Engine | 81.95 | 85.0 | 89.0 | 86.0 | 68.0 | 70.0 | 92.0 | 73.0 | 5000.0 | 0.1 | ok |
| Production swarm without CORAL / Meta-LLM | 80.4 | 83.0 | 87.0 | 80.0 | 71.0 | 72.0 | 89.0 | 76.0 | 4800.0 | 0.095 | ok |

## 3. Swarm Advantage

Production Swarm Score: 91.65
Best Simpler Baseline Score: 83.95
Swarm Advantage Score: 7.7

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
| Critic agent | 91.65 | 80.85 | 10.8 | Keep always-on |
| Reflection loop | 91.65 | 81.6 | 10.05 | Keep always-on |
| RRF memory retrieval | 91.65 | 81.8 | 9.85 | Keep always-on |
| Nightly Mutation Engine | 91.65 | 81.95 | 9.7 | Keep always-on |
| CORAL / Meta-LLM | 91.65 | 80.4 | 11.25 | Keep always-on |

## 6. Safety and Tool-Call Findings

Hallucination Rate: 0.018
Critical Hallucinations: 0
Fabricated Tool-Call Claims: 0
Tool-Call Validity: 0.98
Citation Validity: 0.96

## 7. Dashboard Notification

Status: pass
Dashboard Badge: Eval: Pass
Summary File: /public/dashboard/eval-summary.json

## 8. Missing Data / Skipped Tests

- none

## 9. Recommended Actions

1. Keep production swarm as baseline until a candidate clearly wins.
