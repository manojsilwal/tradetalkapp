"""
Tests for the Durable Knowledge Layer and Ingestion Agent.
"""
import unittest
import json
import os
import shutil
import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

from backend.ingestion_agent import (
    IngestionCandidate,
    emit_ingestion_candidate,
    process_candidate,
    retrieveContext,
    getSymbolHistory,
    getMacroAround,
    getFlowSnapshot,
    _check_vector_duplicates,
    LOCAL_RAW_DIR,
    INGESTION_QUEUE,
)


class TestDurableKnowledgeLayer(unittest.TestCase):
    def setUp(self):
        # Clean up local raw output directory for tests
        if os.path.exists(LOCAL_RAW_DIR):
            shutil.rmtree(LOCAL_RAW_DIR)
        
        # Clear ingestion queue
        while not INGESTION_QUEUE.empty():
            try:
                INGESTION_QUEUE.get_nowait()
            except asyncio.QueueEmpty:
                break

    def tearDown(self):
        if os.path.exists(LOCAL_RAW_DIR):
            shutil.rmtree(LOCAL_RAW_DIR)

    @patch("backend.ingestion_agent._archive_raw_payload", return_value="local_mock_path")
    def test_emit_ingestion_candidate(self, mock_archive):
        # Test candidate creation and deterministic hashing
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        raw_payload = {"test_key": "test_val"}
        candidate = loop.run_until_complete(
            emit_ingestion_candidate(
                source_type="single_stock_search",
                symbols=["AAPL"],
                triggered_by="user",
                raw_payload=raw_payload,
                user_id="test_user",
                feed_source="test_feed",
                as_of_ts="2026-06-05T00:00:00Z",
            )
        )
        
        self.assertIsNotNone(candidate.candidate_id)
        self.assertEqual(candidate.source_type, "single_stock_search")
        self.assertEqual(candidate.symbols, ["AAPL"])
        self.assertEqual(candidate.triggered_by, "user")
        self.assertEqual(candidate.raw_payload_ref, "local_mock_path")
        self.assertEqual(candidate.feed_source, "test_feed")
        self.assertEqual(candidate.user_id, "test_user")
        
        # Check that the queue has the item
        self.assertEqual(INGESTION_QUEUE.qsize(), 1)
        queued_cand, queued_payload = INGESTION_QUEUE.get_nowait()
        self.assertEqual(queued_cand.candidate_id, candidate.candidate_id)
        self.assertEqual(queued_payload, raw_payload)
        
        loop.close()

    @patch("backend.ingestion_agent.logger")
    def test_raw_payload_local_archiving(self, mock_logger):
        # Verify local file archiving works correctly
        from backend.ingestion_agent import _archive_raw_payload
        
        candidate_id = "test_candidate_123"
        payload = {"price": 150.0, "status": "ok"}
        
        path = _archive_raw_payload("single_stock_search", candidate_id, payload)
        
        self.assertTrue(os.path.exists(path))
        self.assertTrue(path.endswith(f"{candidate_id}.json"))
        
        with open(path, "r") as f:
            saved_payload = json.load(f)
        self.assertEqual(saved_payload, payload)

    @patch("backend.deps.llm_client.generate", new_callable=AsyncMock)
    @patch("backend.mcp_server.backend.backend")
    @patch("backend.deps.knowledge_store")
    def test_process_candidate_stage_a_discard(self, mock_ks, mock_backend_fn, mock_llm_generate):
        # Stage A should discard flat, neutral search traces
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        mock_backend = MagicMock()
        mock_backend_fn.return_value = mock_backend
        
        candidate = IngestionCandidate(
            candidate_id="test_cand_a",
            source_type="single_stock_search",
            triggered_by="user",
            symbols=["MSFT"],
            as_of_ts="2026-06-05T00:00:00Z",
            raw_payload_ref="mock_ref",
            payload_summary="mock summary",
            feed_source="swarm",
        )
        
        # Neutral signal, 0 change, low confidence should not pass Stage A
        payload = {
            "global_verdict": "NEUTRAL",
            "global_signal": 0,
            "confidence": 0.5,
            "consensus_rationale": "Flat market."
        }
        
        # Log write mock for ingestion log
        mock_backend.insert_rows.return_value = 1
        
        loop.run_until_complete(process_candidate(candidate, payload))
        
        # Assert LLM judge was NEVER called because Stage A discarded it
        mock_llm_generate.assert_not_called()
        # Verify it was logged as "discarded"
        mock_backend.insert_rows.assert_any_call("rag_ingestion_log", unittest.mock.ANY)
        log_call = next(
            c for c in mock_backend.insert_rows.call_args_list
            if c[0][0] == "rag_ingestion_log"
        )
        log_row = log_call[0][1][0]
        self.assertEqual(log_row["decision"], "discarded")
        
        loop.close()

    @patch("backend.deps.llm_client.generate", new_callable=AsyncMock)
    @patch("backend.mcp_server.backend.backend")
    @patch("backend.deps.knowledge_store")
    def test_process_candidate_stage_b_keep(self, mock_ks, mock_backend_fn, mock_llm_generate):
        # Stage A passes, Stage B LLM judge decides to keep
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        mock_backend = MagicMock()
        mock_backend_fn.return_value = mock_backend
        
        candidate = IngestionCandidate(
            candidate_id="test_cand_b",
            source_type="single_stock_search",
            triggered_by="user",
            symbols=["AAPL"],
            as_of_ts="2026-06-05T00:00:00Z",
            raw_payload_ref="mock_ref",
            payload_summary="mock summary",
            feed_source="swarm",
        )
        
        payload = {
            "global_verdict": "STRONG BUY",
            "global_signal": 1,
            "confidence": 0.85,
            "consensus_rationale": "High volume catalyst rally on earnings."
        }
        
        # Stage B Mock Response: keep it
        mock_llm_generate.return_value = {
            "keep_as": "both",
            "reusability": 0.9,
            "durability": "long_term",
            "tags": ["earnings", "catalyst"],
            "one_line_reason": "High reusability consensus on AAPL.",
        }
        
        # Mock vector collection add
        mock_col = MagicMock()
        mock_col.count.return_value = 0
        mock_col.query.return_value = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        mock_ks._safe_col.return_value = mock_col
        
        loop.run_until_complete(process_candidate(candidate, payload))
        
        # Verify LLM judge was invoked
        mock_llm_generate.assert_called_once()
        # Verify structured fact upserted
        mock_backend.execute.assert_any_call("DELETE FROM rag_price_facts WHERE symbol = 'AAPL' AND trade_date = '2026-06-05'")
        mock_backend.insert_rows.assert_any_call("rag_price_facts", unittest.mock.ANY)
        
        # Verify vector chunk added
        mock_col.add.assert_called_once()
        vector_meta = mock_col.add.call_args[1]["metadatas"][0]
        self.assertEqual(vector_meta["category"], "consensus")
        self.assertEqual(vector_meta["durability"], "long_term")
        
        loop.close()

    @patch("backend.mcp_server.backend.backend")
    @patch("backend.deps.knowledge_store")
    def test_vector_similarity_deduplication(self, mock_ks, mock_backend_fn):
        # Duplicate check should prevent close documents from being inserted twice
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        mock_col = MagicMock()
        mock_ks._safe_col.return_value = mock_col
        
        # Match with distance 0.05 (cosine similarity 0.95 > 0.92)
        mock_col.query.return_value = {
            "documents": [["existing catalyst text"]],
            "metadatas": [[{"flow_date": "2026-06-05", "as_of_ts": "2026-06-05T00:00:00Z"}]],
            "distances": [[0.05]],
        }
        
        # Date is 2026-06-05
        as_of_date = datetime.fromisoformat("2026-06-05T00:00:00Z").date()
        is_dup = loop.run_until_complete(
            _check_vector_duplicates(mock_ks, "AAPL", "new text", as_of_date)
        )
        self.assertTrue(is_dup)
        
        # Match with distance 0.20 (cosine similarity 0.80 < 0.92)
        mock_col.query.return_value = {
            "documents": [["different catalyst text"]],
            "metadatas": [[{"flow_date": "2026-06-05", "as_of_ts": "2026-06-05T00:00:00Z"}]],
            "distances": [[0.20]],
        }
        is_dup_far = loop.run_until_complete(
            _check_vector_duplicates(mock_ks, "AAPL", "new text", as_of_date)
        )
        self.assertFalse(is_dup_far)
        
        loop.close()

    @patch("backend.mcp_server.backend.backend")
    @patch("backend.deps.knowledge_store")
    def test_retrieval_api_point_in_time_constraint(self, mock_ks, mock_backend_fn):
        # Retrieval should exclude chunks that are strictly newer than decision_time
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        mock_backend = MagicMock()
        mock_backend_fn.return_value = mock_backend
        
        mock_col = MagicMock()
        mock_ks._safe_col.return_value = mock_col
        
        # Query results returned from vector store
        mock_col.query.return_value = {
            "documents": [["past narrative", "future narrative"]],
            "metadatas": [[
                {"as_of_ts": "2026-06-03T00:00:00Z", "symbols": "AAPL"},
                {"as_of_ts": "2026-06-10T00:00:00Z", "symbols": "AAPL"}, # future
            ]],
            "distances": [[0.1, 0.12]],
            "ids": [["id_past", "id_future"]],
        }
        
        # Decision time set to 2026-06-05
        res = loop.run_until_complete(
            retrieveContext(
                query="earnings catalyst",
                symbols=["AAPL"],
                mode="semantic",
                decision_time="2026-06-05T12:00:00Z",
            )
        )
        
        chunks = res["chunks"]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["id"], "id_past")
        self.assertEqual(chunks[0]["document"], "past narrative")
        
        loop.close()


if __name__ == "__main__":
    unittest.main()
