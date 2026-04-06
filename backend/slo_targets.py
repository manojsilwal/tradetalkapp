"""
Service-level objectives (SLO) for chat and swarm paths.

Used to justify CORAL-style structured hub reads before vector RAG: if observed
p95 latency or embedding cost exceeds these targets, prioritize skills-cache
and query deduplication work.
"""
from __future__ import annotations

# Chat: end-to-end message handling (client-perceived; measure in APM).
CHAT_P95_LATENCY_MS_TARGET = 8_000

# One `chat_rag_context` fan-out hits len(CHAT_RAG_COLLECTIONS) collections in parallel.
# Skills/notes from the coral hub are intended to reduce redundant semantic search
# when the same patterns repeat (documented target, not a hard cap).
CHAT_RAG_COLLECTION_QUERIES_PER_MESSAGE = 4

# Swarm trace: macro fetch + four factor pairs (parallel) + optional synthesis.
SWARM_TRACE_P95_SECONDS_TARGET = 120.0
