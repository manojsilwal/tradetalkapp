# Weekly Swarm Effectiveness Report

Date: 2026-06-21
Run ID: eval_20260621_162839
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
| Production 5-Agent Swarm | 92.1 | 93.0 | 97.0 | 91.0 | 90.0 | 86.0 | 99.0 | 86.0 | 4800.0 | 0.1 | ok |
| Single-Agent RAG | 82.2 | 85.0 | 93.0 | 74.0 | 62.0 | 82.0 | 95.0 | 84.0 | 3300.0 | 0.06 | ok |
| Planner-Executor | 81.05 | 84.0 | 85.0 | 79.0 | 65.0 | 79.0 | 91.5 | 80.0 | 3900.0 | 0.08 | ok |
| Reduced Swarm + Current LLM | 84.05 | 87.0 | 89.0 | 83.0 | 73.0 | 79.0 | 93.0 | 78.0 | 4100.0 | 0.08 | ok |
| Production swarm without critic | 81.0 | 84.0 | 88.0 | 80.0 | 73.0 | 71.0 | 91.5 | 74.0 | 5000.0 | 0.1 | ok |
| Production swarm without reflection loop | 81.75 | 85.0 | 88.0 | 82.0 | 70.0 | 73.0 | 91.5 | 76.0 | 4700.0 | 0.095 | ok |
| Production swarm without RRF memory retrieval | 81.9 | 84.0 | 84.0 | 86.0 | 70.0 | 74.0 | 93.0 | 74.0 | 5100.0 | 0.1 | ok |
| Production swarm without Nightly Mutation Engine | 82.05 | 85.0 | 89.0 | 86.0 | 68.0 | 70.0 | 93.0 | 73.0 | 5000.0 | 0.1 | ok |
| Production swarm without CORAL / Meta-LLM | 80.55 | 83.0 | 87.0 | 80.0 | 71.0 | 72.0 | 90.5 | 76.0 | 4800.0 | 0.095 | ok |

## 3. Swarm Advantage

Production Swarm Score: 92.1
Best Simpler Baseline Score: 84.05
Swarm Advantage Score: 8.05

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
| Critic agent | 92.1 | 81.0 | 11.1 | Keep always-on |
| Reflection loop | 92.1 | 81.75 | 10.35 | Keep always-on |
| RRF memory retrieval | 92.1 | 81.9 | 10.2 | Keep always-on |
| Nightly Mutation Engine | 92.1 | 82.05 | 10.05 | Keep always-on |
| CORAL / Meta-LLM | 92.1 | 80.55 | 11.55 | Keep always-on |

## 6. Safety and Tool-Call Findings

Hallucination Rate: 0.018
Critical Hallucinations: 0
Fabricated Tool-Call Claims: 0
Tool-Call Validity: 0.99
Citation Validity: 0.98

## 7. Dashboard Notification

Status: pass
Dashboard Badge: Eval: Pass
Summary File: /public/dashboard/eval-summary.json

## 8. Missing Data / Skipped Tests

- none

## 9. Recommended Actions

1. Keep production swarm as baseline until a candidate clearly wins.
