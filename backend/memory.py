import chromadb
from chromadb.config import Settings
import uuid
import datetime
import os
from typing import Dict, Any, List

from .schemas import MarketState, FactorResult

class DomainBrain:
    """
    Manages the ChromaDB instance for storing and retrieving Agent Post-Mortem lessons.
    """
    def __init__(self, db_path: str = "./.chroma_db"):
        os.makedirs(db_path, exist_ok=True)
        self.client = chromadb.PersistentClient(path=db_path)
        self.collection = self.client.get_or_create_collection(name="swarm_lessons")
        
    def add_lesson(self, document: str, metadata: Dict[str, Any]):
        """
        Inserts a single lesson document and its associated metadata into the vector DB.
        """
        doc_id = str(uuid.uuid4())
        self.collection.add(
            documents=[document],
            metadatas=[metadata],
            ids=[doc_id]
        )
        return doc_id
        
    def query_lessons(self, query_text: str, n_results: int = 3, metadata_filter: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Retrieves n_results closest to query_text, optionally filtering by metadata.
        """
        kwargs = {
            "query_texts": [query_text],
            "n_results": n_results
        }
        if metadata_filter:
            kwargs["where"] = metadata_filter
            
        results = self.collection.query(**kwargs)
        return results

class PostMortemAgent:
    """
    Converts a completed Factor debate and its eventual outcome into an explicit lesson 
    for the DomainBrain's vector memory.
    """
    def __init__(self, brain: DomainBrain):
        self.brain = brain
        
    def extract_and_store_lesson(self, result: FactorResult, market_state: MarketState, trade_outcome: str):
        """
        Synthesizes the history array into a single lesson string and commits it to memory.
        """
        
        # 1. Synthesize History (Mock LLM Summarization logic)
        analyst_notes = " ".join([m["content"] for m in result.history if "Analyst" in m["role"]])
        qa_notes = " ".join([m["content"] for m in result.history if "QA" in m["role"]])
        
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        lesson = (
            f"Date: {date_str}. Under {market_state.market_regime} conditions with Credit Stress {market_state.credit_stress_index}, "
            f"Analyst identified: '{analyst_notes[:100]}...'. QA reacted: '{qa_notes[:100]}...'. "
            f"The final signal was {result.trading_signal}. Subsequent Outcome was: {trade_outcome}."
            f"Takeaway: {'Validate constraints strictly' if trade_outcome == 'Failure' else 'Strategy holds edge'}."
        )
        
        # 2. Build Metadata Dictionary for filtering
        metadata = {
            "factor_name": result.factor_name,
            "market_regime": market_state.market_regime.value,
            "credit_stress": market_state.credit_stress_index,
            "trading_signal": result.trading_signal,
            "verification_status": result.status.value,
            "outcome": trade_outcome
        }
        
        # 3. Store into ChromaDB
        doc_id = self.brain.add_lesson(document=lesson, metadata=metadata)
        return doc_id
