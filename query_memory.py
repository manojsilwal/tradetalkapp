from k2_optimus.memory import DomainBrain
import json

def test_queries():
    brain = DomainBrain(db_path="./.chroma_db")
    print(f"Total Database Size: {brain.collection.count()} Lessons\n")
    
    # Query 1: Find Failed Trades during a Bear Market Stress using RAG
    print("--- Query 1: Show me lessons from Failed Bullish attempts in a Bear Market ---")
    results = brain.query_lessons(
        query_text="high short interest squeeze attempt failed",
        n_results=2,
        metadata_filter={
            "$and": [
                {"market_regime": "BEAR_STRESS"},
                {"outcome": "Failure"}
            ]
        }
    )
    
    if not results['documents'][0]:
        print("No exact matches for Query 1 in this 50-run sample.")
    else:
        for i, doc in enumerate(results['documents'][0]):
            meta = results['metadatas'][0][i]
            dist = results['distances'][0][i] if results['distances'] else 0
            print(f"\n[Match {i+1} - Dist: {dist:.4f}]")
            print(f"Metadata: Regime={meta['market_regime']} | Outcome={meta['outcome']} | Signal={meta['trading_signal']}")
            print(f"Lesson: {doc}")
            
    # Query 2: Find Successful Trades in a Bull Market
    print("\n--- Query 2: Show me Successful trades in a Bull Market ---")
    results_bull = brain.query_lessons(
        query_text="short squeeze successful analysis",
        n_results=2,
        metadata_filter={
            "$and": [
                {"market_regime": "BULL_NORMAL"},
                {"outcome": "Success"}
            ]
        }
    )
    
    if not results_bull['documents'][0]:
        print("No exact matches for Query 2 in this 50-run sample.")
    else:
        for i, doc in enumerate(results_bull['documents'][0]):
            meta = results_bull['metadatas'][0][i]
            dist = results_bull['distances'][0][i] if results_bull['distances'] else 0
            print(f"\n[Match {i+1} - Dist: {dist:.4f}]")
            print(f"Metadata: Regime={meta['market_regime']} | Outcome={meta['outcome']} | Signal={meta['trading_signal']}")
            print(f"Lesson: {doc}")

if __name__ == "__main__":
    test_queries()
