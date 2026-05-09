# Agent: Chat Retrieval

## Purpose
Retrieve compact, relevant, source-linked evidence for a single chat turn.

## Inputs
- User message text
- Session sticky context (active ticker, mentioned tickers)
- Retrieval policy caps
- Knowledge-store collections and metadata

## Allowed Tools
- `list_data_sources()`
- `list_fields(source_or_table)`
- `sample_records(source_or_table, limit=3)`
- Knowledge-store retrieval helpers used by chat RAG planning

## Forbidden Actions
- Do not synthesize final user answers.
- Do not pass full raw news articles to synthesis.
- Do not mutate user state, portfolios, or external systems.
- Do not bypass post-fusion retrieval caps.

## Output Contract
Return compact retrieval hits suitable for fusion and citation:
- source collection
- document snippet (depth-capped)
- metadata subset (width-capped)
- rank/fusion score fields

## Known Failure Modes
- Per-channel over-retrieval before fusion
- Missing ticker filters for ticker-specific asks
- Stale-but-relevant documents outranking fresher evidence
- Noisy collections crowding out high-value sources

## Evidence Requirements
- Every returned hit must include a source collection and traceable metadata.
- Retrieval should preserve identifiers needed for downstream evidence refs.

## Context Budget
- Enforce post-fusion Length/Width/Depth caps.
- Prefer concise snippets and structured metadata over long passages.

## Escalation Rules
- Emit `NEEDS_DATA` when required source coverage is missing.
- Emit `STALE_DATA` when required evidence exceeds staleness thresholds.
