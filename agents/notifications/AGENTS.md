# Agent Group: Notifications

## Purpose
Filter macro headlines for importance and produce concise, reliable market alerts.

## Inputs
- Raw headline stream (title, snippet, source, link)
- Source trust mapping
- Impact keyword dictionaries

## Allowed Tools
- Notification filter/scoring logic
- Analyst validation and sector tagging logic

## Forbidden Actions
- Do not generate alerts without passing threshold checks.
- Do not claim execution advice or portfolio actions.
- Do not mutate external systems while processing notifications.
- Do not emit unverifiable source claims.

## Output Contract
Return deterministic alert records including:
- urgency score and label
- affected sectors
- source reliability score
- concise summary

## Known Failure Modes
- False positives from keyword-only matching
- Source spoofing or ambiguous source names
- Over-alerting during high-news periods

## Evidence Requirements
- Each alert must retain source attribution and timestamp.
- Reliability labels must map to configured trust-source lists.

## Context Budget
- Keep headline processing stateless and bounded per batch.
- Prefer compact summaries over full article text.

## Escalation Rules
- Emit `NEEDS_DATA` when headline payload is incomplete.
- Emit structured processing trace for debugging when requested.
