from backend.main import app
import json
import asyncio

async def test_traces():
    print("--- Test 1: Normal Bull Market (Credit Stress = 1.0) ---")
    result_bull = await get_agent_trace(ticker="GME", credit_stress=1.0)
    print(json.dumps(result_bull.model_dump(), indent=2))
    print("\n")
    
    print("--- Test 2: Bear Market Stress (Credit Stress = 1.2) ---")
    result_bear = await get_agent_trace(ticker="GME", credit_stress=1.2)
    print(json.dumps(result_bear.model_dump(), indent=2))

if __name__ == "__main__":
    asyncio.run(test_traces())
