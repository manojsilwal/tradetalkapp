# Weekly Swarm Effectiveness Report

Date: 2026-05-28
Run ID: eval_20260528_152223
Production Version: v1.0.0
Benchmark Suite: agentic_swarm_eval_v1

## 1. Executive Decision

Winner: Reduced Swarm + Current LLM
Decision: shadow_deploy

Summary:
Shadow deploy Reduced Swarm + Current LLM and monitor real traffic.

## 2. Score Summary

| Variant | AES | Task Success | RAG Quality | Orchestration | Learning | Efficiency | Safety | Maintainability | p95 Latency | Cost | Decision |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Production 5-Agent Swarm | 83.2 | 86.0 | 90.0 | 88.0 | 75.0 | 68.0 | 93.0 | 72.0 | 5200.0 | 0.11 | ok |
| Single-Agent RAG | 79.95 | 82.0 | 87.0 | 74.0 | 62.0 | 82.0 | 89.0 | 84.0 | 3300.0 | 0.06 | ok |
| Planner-Executor | 80.9 | 84.0 | 85.0 | 79.0 | 65.0 | 79.0 | 90.0 | 80.0 | 3900.0 | 0.08 | ok |
| Reduced Swarm + Current LLM | 83.95 | 87.0 | 89.0 | 83.0 | 73.0 | 79.0 | 92.0 | 78.0 | 4100.0 | 0.08 | ok |
| Production swarm without critic | 80.65 | 84.0 | 88.0 | 80.0 | 73.0 | 71.0 | 88.0 | 74.0 | 5000.0 | 0.1 | ok |
| Production swarm without reflection loop | 81.6 | 85.0 | 88.0 | 82.0 | 70.0 | 73.0 | 90.0 | 76.0 | 4700.0 | 0.095 | ok |
| Production swarm without RRF memory retrieval | 80.6 | 82.0 | 82.0 | 86.0 | 70.0 | 74.0 | 88.0 | 74.0 | 5100.0 | 0.1 | ok |
| Production swarm without Nightly Mutation Engine | 81.95 | 85.0 | 89.0 | 86.0 | 68.0 | 70.0 | 92.0 | 73.0 | 5000.0 | 0.1 | ok |
| Production swarm without CORAL / Meta-LLM | 80.4 | 83.0 | 87.0 | 80.0 | 71.0 | 72.0 | 89.0 | 76.0 | 4800.0 | 0.095 | ok |

## 3. Swarm Advantage

Production Swarm Score: 83.2
Best Simpler Baseline Score: 83.95
Swarm Advantage Score: -0.75

Recommendation:
Shadow deploy Reduced Swarm + Current LLM and monitor real traffic.

## 4. Complexity Tax

Latency Tax: 4.4
Cost Tax: 12.0
Failure Tax: 1.5
Maintenance Tax: 0.0

Overall Complexity Tax: Low

## 5. Component Ablation Results

| Component | With Score | Without Score | Delta | Recommendation |
|---|---:|---:|---:|---|
| Critic agent | 83.2 | 80.65 | 2.55 | Make conditional |
| Reflection loop | 83.2 | 81.6 | 1.6 | Disable by default |
| RRF memory retrieval | 83.2 | 80.6 | 2.6 | Make conditional |
| Nightly Mutation Engine | 83.2 | 81.95 | 1.25 | Disable by default |
| CORAL / Meta-LLM | 83.2 | 80.4 | 2.8 | Make conditional |

## 6. Safety and Tool-Call Findings

Hallucination Rate: 0.02
Critical Hallucinations: 0
Fabricated Tool-Call Claims: 0
Tool-Call Validity: 0.95
Citation Validity: 0.9

## 7. Dashboard Notification

Status: shadow_recommended
Dashboard Badge: Eval: Shadow Recommended
Summary File: /public/dashboard/eval-summary.json

## 8. Missing Data / Skipped Tests

- none

## 9. Recommended Actions

1. Shadow deploy Reduced Swarm + Current LLM and monitor real traffic.
2. Critic agent: Make conditional.
3. Reflection loop: Disable by default.
4. RRF memory retrieval: Make conditional.
5. Nightly Mutation Engine: Disable by default.
6. CORAL / Meta-LLM: Make conditional.
