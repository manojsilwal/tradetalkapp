# Agent Group: Swarm Trace Factors

## Purpose
Evaluate factor-level signals (short interest, social sentiment, polymarket, fundamentals) and support robust swarm synthesis.

## Inputs
- Market state and ticker
- Factor connector outputs
- Prior lessons/reflections
- Optional peer summaries

## Allowed Tools
- Factor-specific connectors
- Reflection retrieval from knowledge/coral stores
- Swarm analyst and synthesis LLM roles

## Forbidden Actions
- Do not execute portfolio or broker actions.
- Do not skip verification status flow for factor outputs.
- Do not synthesize final global verdict when required factor evidence is stale.
- Do not invent factor metrics absent from connector data.

## Output Contract
- Factor agents return `FactorResult`-compatible structured outputs.
- Synthesis returns verdict/confidence/rationale only after required checks.

## Known Failure Modes
- Missing factor coverage causing unstable global verdicts
- Latency spikes from connector or synthesis retries
- Conflicting factor outputs without explicit contradiction handling
- Stale macro/factor inputs used in synthesis

## Evidence Requirements
- Factor rationales should cite connector metrics and relevant priors.
- Synthesis should reference factor outputs, not hidden assumptions.

## Context Budget
- Keep factor history compact and scoped to current cycle.
- Prefer summarized peer highlights over raw history dumps.

## Escalation Rules
- Emit `NEEDS_DATA` when one or more required factors are unavailable.
- Emit `STALE_DATA` when macro/factor freshness violates policy.
