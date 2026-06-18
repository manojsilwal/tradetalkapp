import asyncio
from backend.decision_terminal import run_decision_terminal_request
from backend.routers.analysis import _execute_analyze
from backend.tool_registry import get_global_tool_registry
from backend.connectors.poly import PolymarketConnector
from backend.llm_client import LLMClient

async def main():
    registry = get_global_tool_registry()
    poly = PolymarketConnector()
    llm = LLMClient(provider="none")
    try:
        res = await run_decision_terminal_request(
            ticker="AAPL",
            credit_stress=None,
            auth_user=None,
            execute_analyze=_execute_analyze,
            tool_registry=registry,
            poly_connector=poly,
            llm_client=llm,
            force=True
        )
        print("Models:", res.valuation.models)
    except Exception as e:
        print("Failed:", e)
        import traceback
        traceback.print_exc()

asyncio.run(main())
