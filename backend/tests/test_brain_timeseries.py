"""TimesFM time-series head integration tests (offline)."""
import unittest

from backend.brain import FEATURE_LIST, SIGNAL_GROUPS
from backend.brain import timeseries as ts
from backend.brain.timesfm_adapter import bands_from_response


class TestTimeseriesBands(unittest.TestCase):
    def setUp(self):
        self.bands = [
            {"horizon": "1d", "q10": 99.0, "q50": 100.5, "q90": 102.0},
            {"horizon": "63d", "q10": 118.0, "q50": 140.0, "q90": 165.0},
        ]

    def test_bands_for_horizon(self):
        b = ts.bands_for_horizon(self.bands, "63d")
        self.assertEqual(b["q50"], 140.0)
        self.assertIsNone(ts.bands_for_horizon(self.bands, "999d"))

    def test_forward_metrics(self):
        m = ts.forward_metrics(118.0, 140.0, 165.0, price=125.0)
        self.assertAlmostEqual(m["expected_return"], 140.0 / 125.0 - 1.0)
        self.assertAlmostEqual(m["downside_return"], 118.0 / 125.0 - 1.0)
        self.assertAlmostEqual(m["upside_return"], 165.0 / 125.0 - 1.0)
        self.assertGreater(m["band_width"], 0)
        self.assertTrue(0.0 <= m["prob_up"] <= 1.0)

    def test_forward_metrics_bad_price(self):
        self.assertIsNone(ts.forward_metrics(1, 2, 3, price=0))

    def test_expected_return_falls_as_price_rises(self):
        # The headline behaviour: bands fixed, price up -> forward return shrinks.
        low = ts.forward_metrics(118, 140, 165, price=125.0)["expected_return"]
        high = ts.forward_metrics(118, 140, 165, price=156.25)["expected_return"]
        self.assertGreater(low, high)
        self.assertLess(high, 0)  # price ran past the q50 target

    def test_band_width_is_price_independent(self):
        a = ts.forward_metrics(118, 140, 165, price=125.0)["band_width"]
        b = ts.forward_metrics(118, 140, 165, price=156.0)["band_width"]
        self.assertAlmostEqual(a, b)

    def test_to_brain_features(self):
        feats = ts.to_brain_features(self.bands, price=125.0)
        self.assertIn("tsfm_expected_return", feats)
        self.assertIn("tsfm_band_width", feats)
        self.assertAlmostEqual(feats["tsfm_expected_return"], 140.0 / 125.0 - 1.0)

    def test_to_brain_features_no_band(self):
        feats = ts.to_brain_features([], price=125.0)
        self.assertIsNone(feats["tsfm_expected_return"])
        self.assertIsNone(feats["tsfm_band_width"])

    def test_forecast_block(self):
        block = ts.forecast_block(self.bands, 125.0, model_version="timesfm-2.5")
        self.assertEqual(block["source"], "timesfm")
        self.assertEqual(block["model_version"], "timesfm-2.5")
        self.assertEqual(block["horizon"], "63d")
        self.assertEqual(block["q50"], 140.0)


class TestContractWiring(unittest.TestCase):
    def test_tsfm_features_in_contract(self):
        self.assertIn("tsfm_expected_return", FEATURE_LIST)
        self.assertIn("tsfm_band_width", FEATURE_LIST)

    def test_timeseries_signal_group(self):
        self.assertIn("timeseries", SIGNAL_GROUPS)


class TestAdapterConversion(unittest.TestCase):
    def test_bands_from_dict_response(self):
        resp = {
            "model_version": "timesfm-2.5-200m",
            "horizon_bands_usd": [
                {"horizon": "63d", "q10_usd": 118.0, "q50_usd": 140.0, "q90_usd": 165.0},
                {"horizon": "21d", "q10_usd": 110.0, "q50_usd": None, "q90_usd": 130.0},  # skipped
            ],
        }
        bands, mv = bands_from_response(resp)
        self.assertEqual(mv, "timesfm-2.5-200m")
        self.assertEqual(len(bands), 1)  # the None-q50 band is dropped
        self.assertEqual(bands[0]["q50"], 140.0)

    def test_bands_from_object_response(self):
        class Band:
            def __init__(self, h, a, b, c):
                self.horizon, self.q10_usd, self.q50_usd, self.q90_usd = h, a, b, c

        class Resp:
            model_version = "timesfm-2.5-200m"
            horizon_bands_usd = [Band("63d", 118.0, 140.0, 165.0)]

        bands, mv = bands_from_response(Resp())
        self.assertEqual(bands[0]["horizon"], "63d")
        self.assertEqual(mv, "timesfm-2.5-200m")

    def test_empty_response(self):
        bands, mv = bands_from_response({})
        self.assertEqual(bands, [])
        self.assertIsNone(mv)


if __name__ == "__main__":
    unittest.main()
