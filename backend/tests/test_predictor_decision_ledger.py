import asyncio
import os
import tempfile
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend import decision_ledger as dl  # noqa: E402


class TestPredictorLedgerEmit(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["DECISIONS_DB_PATH"] = os.path.join(self._tmp.name, "d.db")
        os.environ["DECISION_LEDGER_ENABLE"] = "1"
        os.environ["DECISION_BACKEND"] = "sqlite"
        dl._reset_singleton_for_tests()

    def tearDown(self) -> None:
        dl._reset_singleton_for_tests()
        os.environ.pop("DECISIONS_DB_PATH", None)

    def test_emits_price_forecast_rows(self) -> None:
        from backend.predictor.agent import run_predictor_forecast

        async def _run():
            return await run_predictor_forecast(
                "AAPL",
                horizons=["1d", "63d"],
                tool_registry=None,
                emit_ledger=True,
            )

        os.environ["PREDICTOR_ENABLE"] = "1"
        out = asyncio.run(_run())
        self.assertEqual(out.status, "ok")
        rows = dl.get_ledger().list_decisions_since(0.0, decision_type="price_forecast")
        self.assertGreaterEqual(len(rows), 1)
        # Quantile band must thread into output_json so the outcome grader can
        # score forecast_band_hit / forecast_pinball at T+H.
        for ev in rows:
            self.assertIn("q10_usd", ev.output)
            self.assertIn("q50_usd", ev.output)
            self.assertIn("q90_usd", ev.output)


if __name__ == "__main__":
    unittest.main()
