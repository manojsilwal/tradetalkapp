import os
import sys

# Ensure backend modules can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ["VECTOR_BACKEND"] = "hf"
os.environ["HF_DATASET_ID"] = "manojsilwal44/tradetalk-data"
os.environ["DATA_LAKE_SOURCE"] = "hf"
os.environ["HF_TOKEN"] = os.getenv("HF_TOKEN", "")

print("--- Testing Knowledge Store (Chroma) Init ---")
try:
    from backend.deps import knowledge_store
    stats = knowledge_store.stats()
    print("Stats:", stats)
except Exception as e:
    print("Knowledge store init failed:", e)

print("\n--- Testing Data Lake (Parquet) Reads ---")
try:
    from backend.decision_terminal import _get_historical_cagr_3y, _get_historical_quality_metrics
    cagr = _get_historical_cagr_3y("AAPL")
    quality = _get_historical_quality_metrics("AAPL")
    print(f"AAPL 3Y CAGR: {cagr}%")
    print(f"AAPL Quality metrics: {quality}")
except Exception as e:
    print("Data lake reads failed:", e)
