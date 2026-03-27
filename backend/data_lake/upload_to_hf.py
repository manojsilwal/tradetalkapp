"""
Utility script to programmatically create a Hugging Face Dataset
and upload the raw data_lake_output contents.
"""
import os
import sys
import logging
from huggingface_hub import HfApi

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HF_TOKEN = os.getenv("HF_TOKEN")

def upload_data_lake():
    if not HF_TOKEN:
        logger.error("No HF_TOKEN provided.")
        sys.exit(1)
        
    api = HfApi(token=HF_TOKEN)
    username = api.whoami()["name"]
    repo_id = f"{username}/tradetalk-data"
    
    logger.info(f"Target repository: {repo_id}")
    
    logger.info("Creating repository if it doesn't exist...")
    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True, private=True)
    
    target_folder = os.path.join(os.path.dirname(__file__), "..", "..", "data_lake_output")
    target_folder = os.path.abspath(target_folder)
    
    logger.info(f"Uploading files from {target_folder} to {repo_id}...")
    
    try:
        # Upload daily_prices
        api.upload_folder(
            folder_path=os.path.join(target_folder, "daily_prices"),
            path_in_repo="daily_prices",
            repo_id=repo_id,
            repo_type="dataset",
            allow_patterns="*.parquet"
        )
        logger.info("Uploaded daily_prices.")
        
        # Upload quarterly_financials
        api.upload_folder(
            folder_path=os.path.join(target_folder, "quarterly_financials"),
            path_in_repo="quarterly_financials",
            repo_id=repo_id,
            repo_type="dataset",
            allow_patterns="*.parquet"
        )
        logger.info("Uploaded quarterly_financials.")
        
        # Upload rag_summaries
        api.upload_folder(
            folder_path=os.path.join(target_folder, "rag_summaries"),
            path_in_repo="rag_summaries",
            repo_id=repo_id,
            repo_type="dataset",
            allow_patterns="*.json"
        )
        logger.info("Uploaded rag_summaries.")
        
        logger.info("Successfully pushed data_lake_output to Hugging Face!")
    except Exception as e:
        logger.error(f"Failed to upload: {e}")

if __name__ == "__main__":
    upload_data_lake()
