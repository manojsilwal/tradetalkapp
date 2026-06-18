import asyncio
from backend.connectors.momentum_data import fetch_momentum_inputs
from backend.momentum_model import analyze_momentum

async def main():
    try:
        stock_df, spy_df, sector_df, mom_meta = await fetch_momentum_inputs(
            "AAPL",
            {"sector": "Technology", "industry": "Consumer Electronics", "marketCap": 1e12, "beta": 1.0}
        )
        print("Data fetched:", len(stock_df), len(spy_df), len(sector_df))
        res = analyze_momentum(stock_df, spy_df, sector_df, mom_meta)
        print("Result:", res)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
