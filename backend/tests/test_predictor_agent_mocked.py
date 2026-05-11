import asyncio
import os
import unittest


class TestPredictorGoldenPath(unittest.TestCase):
    def setUp(self) -> None:
        self._pe = os.environ.get("PREDICTOR_ENABLE")

    def tearDown(self) -> None:
        if self._pe is None:
            os.environ.pop("PREDICTOR_ENABLE", None)
        else:
            os.environ["PREDICTOR_ENABLE"] = self._pe

    def test_ok_path_monotonic_quantiles(self) -> None:
        os.environ["PREDICTOR_ENABLE"] = "1"
        from backend.predictor.agent import run_predictor_forecast

        async def _run():
            return await run_predictor_forecast(
                "NVDA",
                horizons=["1d", "5d", "21d", "63d"],
                tool_registry=None,
                emit_ledger=False,
            )

        out = asyncio.run(_run())
        self.assertEqual(out.status, "ok")
        for b in out.horizon_bands_usd:
            self.assertIsNotNone(b.q10_usd)
            self.assertIsNotNone(b.q50_usd)
            self.assertIsNotNone(b.q90_usd)
            self.assertLessEqual(b.q10_usd, b.q50_usd)
            self.assertLessEqual(b.q50_usd, b.q90_usd)


if __name__ == "__main__":
    unittest.main()
