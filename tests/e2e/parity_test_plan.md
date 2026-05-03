# Financial Data Parity Testing Plan

## Goal
Establish trust in TradeTalk's generated and fetched financial numbers by creating an automated Playwright suite that compares displayed metrics with external source truths like Yahoo Finance.

## Scope
- Test standard tickers on the dashboard or macro views (e.g., AAPL, SPY, Gold).
- Check standard metrics: Current Price, Daily Change (%), Volume.

## Verification Logic
1. Navigate to the TradeTalk app dashboard/macro view.
2. Locate a specific ticker's card/row.
3. Extract the numerical data.
4. Fetch live data for the same ticker via the Yahoo Finance API (e.g. `https://query1.finance.yahoo.com/v8/finance/chart/AAPL`).
5. Assert that the values are exactly equal or within an acceptable micro-percentage threshold (< 0.5% variance) due to API polling delays.
