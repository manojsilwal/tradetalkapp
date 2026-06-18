import asyncio
from backend.connectors.debate_data import fetch_debate_data

async def main():
    print("Testing debate data connector...")
    try:
        res = await asyncio.wait_for(fetch_debate_data("AAPL"), timeout=15.0)
        print("Success:", res)
    except Exception as e:
        print("Failed:", e)

if __name__ == "__main__":
    asyncio.run(main())
