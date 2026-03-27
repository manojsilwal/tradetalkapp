"""Unit tests for HF Hub backtest warehouse reader (no network)."""
import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pandas as pd


class TestBacktestDataHub(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_hub_fixture_tmp"
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self):
        import shutil

        if self.tmp.exists():
            shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_symbol(self, root: Path, sym: str) -> None:
        u = sym.upper()
        prices = pd.DataFrame(
            [
                {
                    "date": "2020-01-02",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 1_000_000,
                },
                {
                    "date": "2020-06-15",
                    "open": 12.0,
                    "high": 13.0,
                    "low": 11.0,
                    "close": 12.5,
                    "volume": 2_000_000,
                },
            ]
        )
        p = root / "prices" / f"symbol={u}"
        p.mkdir(parents=True, exist_ok=True)
        prices.to_parquet(p / "data.parquet", index=False)

        qe = pd.DataFrame([{"date": "2020-03-31", "eps": 1.25}])
        q = root / "quarterly_eps" / f"symbol={u}"
        q.mkdir(parents=True, exist_ok=True)
        qe.to_parquet(q / "data.parquet", index=False)

        af = pd.DataFrame([{"year": 2020, "total_revenue": 100.0, "net_income": 10.0}])
        a = root / "annual_financials" / f"symbol={u}"
        a.mkdir(parents=True, exist_ok=True)
        af.to_parquet(a / "data.parquet", index=False)

        info = {"trailingPE": 20.5, "marketCap": 999}
        ij = pd.DataFrame([{"info_json": json.dumps(info)}])
        i = root / "info" / f"symbol={u}"
        i.mkdir(parents=True, exist_ok=True)
        ij.to_parquet(i / "data.parquet", index=False)

    def test_assemble_from_hub_filters_dates(self):
        from backend.connectors import backtest_data_hub as hub

        self._write_symbol(self.tmp, "ZZTEST")

        def fake_download(repo_id, rel_path, revision, token):
            parts = Path(rel_path).parts
            local = self.tmp / rel_path
            if local.is_file():
                return local
            return None

        with patch.object(hub, "download_hub_file", side_effect=fake_download):
            out, nbytes, rev = hub.assemble_from_hub(
                ["ZZTEST"],
                "2020-01-01",
                "2020-03-30",
                repo_id="dummy/ds",
                revision="main",
                token=None,
            )

        self.assertEqual(rev, "main")
        self.assertGreater(nbytes, 0)
        self.assertIn("ZZTEST", out)
        self.assertEqual(len(out["ZZTEST"]["prices"]), 1)
        self.assertEqual(out["ZZTEST"]["prices"][0]["date"], "2020-01-02")
        self.assertEqual(out["ZZTEST"]["quarterly_eps"][0]["eps"], 1.25)
        self.assertIn("2020", out["ZZTEST"]["annual_financials"])
        self.assertEqual(out["ZZTEST"]["info"].get("trailingPE"), 20.5)

    def test_fetch_backtest_data_live_source_only(self):
        from backend.connectors import backtest_data as bd

        stub = {
            "AAPL": {
                "prices": [{"date": "2020-01-02", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
                "quarterly_eps": [],
                "annual_financials": {},
                "info": {},
            }
        }

        async def run():
            with patch.dict(os.environ, {"BACKTEST_DATA_SOURCE": "live"}, clear=False):
                with patch.object(bd, "fetch_backtest_data_live", new_callable=AsyncMock) as m:
                    m.return_value = stub
                    return await bd.fetch_backtest_data(["AAPL"], "2020-01-01", "2020-02-01")

        data = asyncio.run(run())
        self.assertEqual(data["AAPL"]["prices"][0]["close"], 1)

    def test_fetch_backtest_data_hub_falls_back_when_empty(self):
        from backend.connectors import backtest_data as bd

        live_stub = {
            "AAPL": {
                "prices": [{"date": "2020-01-02", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1}],
                "quarterly_eps": [],
                "annual_financials": {},
                "info": {},
            }
        }

        async def run():
            with patch.dict(
                os.environ,
                {"BACKTEST_DATA_SOURCE": "hub", "HF_DATASET_REPO": "org/dataset"},
                clear=False,
            ):
                with patch(
                    "backend.connectors.backtest_data_hub.assemble_from_hub",
                    return_value=({}, 0, "abc123"),
                ):
                    with patch.object(bd, "fetch_backtest_data_live", new_callable=AsyncMock) as m:
                        m.return_value = live_stub
                        return await bd.fetch_backtest_data(["AAPL"], "2020-01-01", "2020-02-01")

        data = asyncio.run(run())
        self.assertEqual(data["AAPL"]["prices"][0]["close"], 2)

    def test_fetch_backtest_data_hub_no_live_when_complete(self):
        from backend.connectors import backtest_data as bd

        hub_bundle = {
            "AAPL": {
                "prices": [{"date": "2020-01-02", "open": 3, "high": 3, "low": 3, "close": 3, "volume": 1}],
                "quarterly_eps": [{"date": "2020-03-31", "eps": 1.0}],
                "annual_financials": {"2020": {"total_revenue": 1.0}},
                "info": {"x": 1},
            }
        }

        async def run():
            with patch.dict(
                os.environ,
                {"BACKTEST_DATA_SOURCE": "hub", "HF_DATASET_REPO": "org/dataset"},
                clear=False,
            ):
                with patch(
                    "backend.connectors.backtest_data_hub.assemble_from_hub",
                    return_value=(hub_bundle, 500, "rev1"),
                ):
                    with patch.object(bd, "fetch_backtest_data_live", new_callable=AsyncMock) as m:
                        out = await bd.fetch_backtest_data(["AAPL"], "2020-01-01", "2020-02-01")
                        m.assert_not_awaited()
                        return out

        data = asyncio.run(run())
        self.assertEqual(data["AAPL"]["prices"][0]["close"], 3)


if __name__ == "__main__":
    unittest.main()
