import asyncio
import os
import unittest


class TestPredictorKillSwitch(unittest.TestCase):
    def setUp(self) -> None:
        self._old = os.environ.get("PREDICTOR_ENABLE")

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("PREDICTOR_ENABLE", None)
        else:
            os.environ["PREDICTOR_ENABLE"] = self._old

    def test_disabled_payload(self) -> None:
        os.environ["PREDICTOR_ENABLE"] = "0"
        from backend.predictor.agent import run_predictor_forecast

        async def _run():
            return await run_predictor_forecast("MSFT", tool_registry=None, emit_ledger=False)

        out = asyncio.run(_run())
        self.assertEqual(out.status, "disabled")
        self.assertFalse(out.executed)


if __name__ == "__main__":
    unittest.main()
