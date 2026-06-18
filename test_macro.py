import asyncio
from backend.routers.macro import get_macro_state

async def main():
    print("Testing macro fetch...")
    try:
        res = await asyncio.wait_for(get_macro_state(), timeout=15.0)
        print("Success")
    except Exception as e:
        print("Failed:", e)

if __name__ == "__main__":
    asyncio.run(main())
