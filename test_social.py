import asyncio
from backend.connectors.social import SocialSentimentConnector

async def main():
    print("Testing social connector...")
    connector = SocialSentimentConnector()
    try:
        res = await asyncio.wait_for(connector.fetch_data("AAPL"), timeout=15.0)
        print("Success:", res)
    except Exception as e:
        print("Failed:", e)

if __name__ == "__main__":
    asyncio.run(main())
