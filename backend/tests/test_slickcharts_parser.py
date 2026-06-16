"""Unit tests for Slickcharts HTML parsing (offline fixtures)."""

import unittest

from backend.connectors.slickcharts import (
    parse_etf_benchmarks,
    parse_mover_rows,
    slickcharts_rows_to_brief_rows,
)

GAINERS_SNIPPET = """
<tbody>
<tr><td style="max-width: 120px;"><a href="/symbol/WDC">Western Digital Corp</a></td><td><a href="/symbol/WDC">WDC</a></td><td class="text-nowrap"><div><img src="/img/up-arrow.svg"/><span>697.00</span></div></td><td class="text-nowrap" style="color: green">43.47</td><td class="text-nowrap" style="color: green">6.65%</td></tr>
<tr><td style="max-width: 120px;"><a href="/symbol/LUV">Southwest Airlines Co</a></td><td><a href="/symbol/LUV">LUV</a></td><td class="text-nowrap"><div><span>48.42</span></div></td><td class="text-nowrap" style="color: green">2.34</td><td class="text-nowrap" style="color: green">5.07%</td></tr>
<tr><td class="text-nowrap"><a href="/symbol/SPY">SPY</a></td><td class="text-nowrap">S&amp;P 500 ETF</td><td class="text-nowrap"><div><span>753.21</span></div></td><td class="text-nowrap" style="color: red">-1.63</td><td class="text-nowrap" style="color: red">-0.22%</td></tr>
<tr><td class="text-nowrap"><a href="/symbol/QQQ">QQQ</a></td><td class="text-nowrap">Nasdaq 100 ETF</td><td class="text-nowrap"><div><span>737.86</span></div></td><td class="text-nowrap" style="color: red">-6.14</td><td class="text-nowrap" style="color: red">-0.82%</td></tr>
<tr><td class="text-nowrap"><a href="/symbol/DIA">DIA</a></td><td class="text-nowrap">Dow Jones ETF</td><td class="text-nowrap"><div><span>521.66</span></div></td><td class="text-nowrap" style="color: green">3.22</td><td class="text-nowrap" style="color: green">0.62%</td></tr>
</tbody>
"""

LOSERS_SNIPPET = """
<tr><td style="max-width: 120px;"><a href="/symbol/LITE">Lumentum Holdings</a></td><td><a href="/symbol/LITE">LITE</a></td><td class="text-nowrap"><div><span>881.77</span></div></td><td class="text-nowrap" style="color: red">-75.47</td><td class="text-nowrap" style="color: red">-7.88%</td></tr>
<tr><td style="max-width: 120px;"><a href="/symbol/CBOE">Cboe Global Markets Inc</a></td><td><a href="/symbol/CBOE">CBOE</a></td><td class="text-nowrap"><div><span>272.16</span></div></td><td class="text-nowrap" style="color: red">-20.75</td><td class="text-nowrap" style="color: red">-7.08%</td></tr>
"""


class TestSlickchartsParser(unittest.TestCase):
    def test_parse_gainers(self):
        rows = parse_mover_rows(GAINERS_SNIPPET, bucket="gainer", limit=5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol"], "WDC")
        self.assertAlmostEqual(rows[0]["daily_return_pct"], 6.65)
        self.assertEqual(rows[1]["symbol"], "LUV")

    def test_parse_losers(self):
        rows = parse_mover_rows(LOSERS_SNIPPET, bucket="loser", limit=5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["symbol"], "LITE")
        self.assertAlmostEqual(rows[0]["daily_return_pct"], -7.88)

    def test_parse_etf_benchmarks(self):
        etfs = parse_etf_benchmarks(GAINERS_SNIPPET)
        symbols = [e["symbol"] for e in etfs]
        self.assertEqual(symbols, ["SPY", "QQQ", "DIA"])
        spy = etfs[0]
        self.assertAlmostEqual(spy["daily_return_pct"], -0.22)
        self.assertEqual(spy["name"], "S&P 500 ETF")

    def test_rows_to_brief_shape(self):
        gainers = parse_mover_rows(GAINERS_SNIPPET, bucket="gainer", limit=2)
        losers = parse_mover_rows(LOSERS_SNIPPET, bucket="loser", limit=2)
        mapped = slickcharts_rows_to_brief_rows(gainers, losers)
        self.assertEqual(len(mapped), 4)
        self.assertEqual(mapped[0][0], "loser")
        self.assertEqual(mapped[-1][0], "gainer")


if __name__ == "__main__":
    unittest.main()
