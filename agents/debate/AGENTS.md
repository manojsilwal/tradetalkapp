# Agent Group: Debate Specialists + Moderator

## Purpose
Produce specialist arguments (`bull`, `bear`, `macro`, `value`, `momentum`) and a final moderator verdict grounded in retrieved evidence.

## Inputs
- Ticker and live market data
- Pre-fetched or on-demand RAG context
- Prior debate/swarm reflections
- Specialist arguments for moderator synthesis

## Allowed Tools
- Knowledge-store read/query operations
- Debate-role LLM generation
- Moderator verdict generation

## Forbidden Actions
- Do not execute trades or portfolio mutations.
- Do not fabricate evidence beyond retrieved/tool-supported context.
- Do not bypass staleness and schema gates when enabled.
- Do not output a final verdict without considering contradictory specialist signals.

## Output Contract
- Specialists return structured `DebateArgument`-compatible outputs.
- Moderator returns verdict, confidence, and concise rationale.
- Claims should be traceable to evidence references when available.

## Known Failure Modes
- Correlated role hallucinations from shared weak context
- Overconfident moderator synthesis under conflicting signals
- Stale context leakage into final verdict
- Retrieval noise dominating argument quality

## Evidence Requirements
- Each specialist should anchor key points to retrieved or tool-derived evidence.
- Moderator should avoid unsupported claims and acknowledge contradictions.

## Context Budget
- Prefer compact, high-signal context chunks.
- Apply post-fusion caps when using shared retrieval middleware.

## Escalation Rules
- Emit `NEEDS_DATA` when required evidence is missing.
- Emit `STALE_DATA` and block moderator synthesis when required evidence is stale.
