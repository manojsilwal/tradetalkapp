# Agent: Strategy Parser

## Purpose
Convert plain-English strategy requests into validated `StrategyRules` for backtesting.

## Inputs
- Strategy prompt text
- Date range
- Knowledge-store context for similar strategies/reflections
- LLM parser output

## Allowed Tools
- Strategy parser LLM role
- Strategy-related knowledge retrieval
- Heuristic fallback parser

## Forbidden Actions
- Do not output invalid metrics/operators outside supported schema.
- Do not bypass `StrategyRules` validation.
- Do not execute backtests directly in parsing stage.
- Do not infer unsupported universe members not in configured datasets.

## Output Contract
Return a validated `StrategyRules` object with:
- normalized filters and sell filters
- bounded holding/rebalance settings
- strategy type and resolved universe

## Known Failure Modes
- Empty or low-quality LLM extraction
- Unsupported metric aliases leaking into final output
- Excessively broad universe selection
- Invalid date ranges or out-of-bounds configuration

## Evidence Requirements
- Parsing decisions should be explainable from user prompt and retrieved strategy context.
- Fallback path must remain deterministic and reproducible.

## Context Budget
- Keep retrieval context small and strategy-specific.
- Prefer structured filters over narrative interpretation.

## Escalation Rules
- Emit `NEEDS_DATA` when strategy intent is too ambiguous to produce safe rules.
- Emit validation failure details when schema checks cannot be satisfied.
