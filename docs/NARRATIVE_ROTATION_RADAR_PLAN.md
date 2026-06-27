# Narrative Rotation Radar — TradeTalk implementation plan

**Status:** Plan / design (no code shipped in this doc).
**Owner surface:** new investor-facing feature, name **Narrative Rotation Radar** (a.k.a. *Theme Lifecycle Engine*).
**Goal:** detect how capital and narrative rotate through market themes/sectors — when a theme is *seeded → accumulated → accelerating → crowded → distributing → exiting* — so an investor can enter early and reduce before the crowd, using signals TradeTalk **already ingests** plus a small set of new connectors.

> **Compliance framing (use verbatim in UI).** *This feature identifies observable market, filing, fund-flow, media, and institutional-positioning signals. It does not infer intent, coordination, or manipulation by any institution. Scores are probabilistic research indicators and may be incomplete or delayed depending on source availability. This is not investment advice.*

This plan deliberately maps the generic "Wall Street narrative engine" idea onto **TradeTalk's existing data pipeline and agent architecture**. The headline finding from the codebase audit:

> **~70% of this feature already exists in the repo.** The work is mostly a **theme-lifecycle aggregation layer** that fuses signals we already compute, plus 2–3 new connectors (ETF prospectus filings, ETF flows, retail-saturation proxy) and one new investor page.

---

## 1. What we are building, in one diagram

```text
                          NARRATIVE ROTATION RADAR
                          (theme-lifecycle layer)
                                   │
        ┌──────────────┬──────────┴───────────┬───────────────┬───────────────┐
        ▼              ▼                      ▼               ▼               ▼
  Narrative /     Productization        Institutional      Market          Reality
  media signal    (ETF pipeline)        footprint          confirmation    (fundamentals)
        │              │                      │               │               │
   REUSE +        NEW (N-1A/S-1)         REUSE 13F        REUSE RRG /     REUSE SEC +
   new social     + ETF flows           leaderboard      momentum        XBRL pipeline
        │              │                      │               │               │
        └──────────────┴──────────┬───────────┴───────────────┴───────────────┘
                                   ▼
                   theme_daily_features  →  theme_scores (phase + confidence)
                                   ▼
                   Decision-Outcome Ledger (decision_type="theme_phase")
                                   ▼
                   outcome_grader → backtest / hit-rate validation
                                   ▼
              /api/narrative-radar/*  →  Narrative Rotation Radar page
```

The lifecycle layer is modeled directly on the existing **Picks & Shovels Momentum Finder** (`backend/picks_shovels/`), which is a *company-level* version of this exact pattern (theme taxonomy → cross-sectional percentile scoring → snapshot store → ledger emit → frontend). We generalize that pattern from *companies within one theme* to *themes within the market*.

---

## 2. Reuse map — existing TradeTalk modules → feature components

This is the most important section: **do not rebuild what exists.** Each row says what to reuse, what to extend, and what is genuinely new.

| Feature component | Reuse this existing module | Action |
|---|---|---|
| **Theme taxonomy + seed tickers + keyword dictionaries** | `backend/picks_shovels/themes.py` (12 themes, `THEME_MEMBERS`, `customer_capex_seed`); `backend/macro_flow/taxonomy/seed_taxonomy.py`; `backend/data/supply_chains.json`; `backend/brain/business_classifier.py` (`AI_ACCELERATOR_TICKERS`) | **Extend** to a shared `themes` table; add per-theme keyword dictionaries (Plan §18 of the article). |
| **Cross-sectional percentile scoring (0–100), confidence, coverage, "never fabricate"** | `backend/picks_shovels/scoring.py` (`PercentileContext`, `percentile_rank`, `_blend`, `confidence_level`) | **Reuse directly.** This already implements the article's §7.1 `percentile_score` and renormalize-over-present-components rule. Apply at theme level. |
| **Snapshot persistence + freshness TTL + job-state polling** | `backend/picks_shovels/store.py` (`ps_snapshots`/`ps_rows`), `backend/picks_shovels/engine.py` (`run_scan`, `_set_job`, two-pass flow), `backend/actionable_companies.py` | **Clone** into `narrative_radar/store.py` + `engine.py` (theme snapshots). |
| **Relative strength vs SPY + RS momentum + capital-flow score (sector/theme level)** | `backend/macro_flow/macro_flow_agent.py` (`rs_ratio`, `rs_momentum`, CMF flow), `backend/macro_flow/store.py` (`flow_scores`, `latest_rrg_payload`), `GET /macro/flow/rrg` | **Reuse** as the Market Confirmation + RS axes. The RRG already maps to the article's "RS crosses 1.0 + momentum" early-warning Tier 3. |
| **Sector ETF performance + movers + index levels** | `backend/market_intel.py` (`_SECTOR_ETFS`), `backend/market_l1_cache.py`, `GET /macro` (`MacroDataResponse.sectors`) | **Reuse** for sector-level confirmation + the existing heatmap. |
| **Institutional footprint (13F): ownership breadth, concentration, new/exited positions** | `backend/coral_skills/sec_13f_ingestion.py`, `backend/fund_leaderboard_*` (`thirteen_f_holdings`, `fund_master`), `GET /api/funds/*` | **Reuse + add aggregation:** roll holdings up to theme level → ownership breadth, top-holder crowding, hedge-fund vs long-only. (Article Tier 2 #4.) |
| **SEC filing text (10-K/10-Q/8-K/DEF 14A) + keyword/CAPEX extraction** | `backend/fincrawler_client.py` (`get_sec_filing`), `backend/sec_filing_job.py`, `backend/connectors/backtest_data.py` (XBRL companyfacts EPS), `backend/sec/edgar_client.py` | **Reuse** for narrative-reality alignment (revenue/segment/capex/keyword growth) and evidence events. |
| **Price momentum / breadth math (1/3/6/12M returns, RS, MA distance, RSI)** | `backend/picks_shovels/data.py` (`momentum_from_closes`), `backend/momentum_model.py`, `backend/brain/features.py` (`momentum_features`), `backend/actionable_companies.fetch_chunk_history` | **Reuse** for breadth-quality (% above 50/200DMA, equal- vs cap-weight). |
| **News / media narrative ingestion + sentiment** | `backend/connectors/news_scanner.py` (Google News RSS), `backend/connectors/social.py`, `backend/routers/portfolio_news.py` (NewsAPI + LLM classifier), `backend/fincrawler_client.py` (`/news`) | **Reuse** for headline counts / sentiment / velocity per theme. |
| **Social / influencer signals** | `backend/connectors/youtube.py` (YouTube Data API), `backend/social_sources.py` (YouTube RSS, Reddit, Stocktwits) | **Reuse + aggregate** into Influencer Amplification + Retail Saturation scores. |
| **Evidence timeline + RAG provenance** | `backend/knowledge_store.py` (`query_with_refs`, collections `sp500_sector_analysis`, `sp500_fundamentals_narratives`, `macro_snapshots`, `events_semantic`) | **Reuse** for the evidence timeline + ledger `EvidenceRef`s. Add a new collection or reuse `events_semantic`. |
| **Verdict provenance, prompt/registry stamping, dual-write to CORAL** | `backend/decision_ledger.py` (`emit_decision`, `EvidenceRef`, `FeatureValue`), `backend/decision_ledger_registry.py` (`registry_attribution`) | **Reuse** — emit one `decision_type="theme_phase"` row per theme per snapshot (mandatory per `AGENTS.md`). |
| **Free backtesting / hit-rate validation** | `backend/outcome_grader.py` (daily 02:10 UTC, `excess_return` vs SPY at horizons), `backend/harness/` (replay, hit-rates), `GET /harness/hit-rates` | **Reuse** — the grader already scores any ledger row by forward excess return, so theme-phase calls get a backtest "for free." |
| **Multi-agent orchestration + reflections** | `backend/agents.py` (`AgentPair` nested Analyst→QA loop), `backend/coral_hub.py` (`log_handoff_event`, notes/skills), `backend/coral_heartbeat.py` (4 scheduled agents) | **Reuse** — add a `narrative` CORAL agent reflection + `handoff_theme_phase` event for dreaming. |
| **Scheduler / cron** | `backend/daily_pipeline.py` (APScheduler), cron endpoints in `backend/routers/knowledge.py` (`PIPELINE_CRON_SECRET`), GitHub Actions (`render-daily-pipeline.yml`, `macro-flow-daily.yml`) | **Reuse** — register theme jobs (daily features/scores; weekly ETF flows + social; quarterly 13F + reality). |
| **Frontend charts** | `recharts` (time series), `@nivo/sankey` (flows), `@xyflow/react` (graphs), `components/Sparkline.jsx`, `SemiGauge`, `macro/GlobalCapFlowDashboard.jsx` (sector heatmap, `data-testid="macro-sector-heatmap"`) | **Reuse** — new page assembles these. The orphaned `MacroFlowPanel.jsx` (RRG/sankey, built but unrouted) can be wired in. |
| **API client + data-freshness UX** | `frontend/src/api.js` (`apiFetch`/`apiFetchTimed`), `backend/schemas.py` (`DataFreshness`), `components/Freshness.jsx` | **Reuse** for all new endpoints + freshness badges. |

### Genuinely new work (the ~30%)

1. **ETF productization connector** — SEC **N-1A / S-1** thematic-fund filings (not ingested today; only stripped from headlines in `daily_brief.py`).
2. **ETF flow connector** — weekly fund flows / AUM growth (no source today).
3. **Retail saturation proxy** — aggregate existing YouTube/Reddit/Stocktwits into a theme-level saturation percentile (Google Trends optional, not present today).
4. **Theme-lifecycle aggregation + phase classifier** — the new `narrative_radar` service that fuses all of the above into `theme_daily_features` → `theme_scores` with a deterministic phase classifier.
5. **New investor page** — `Narrative Rotation Radar` + theme detail tabs.

---

## 3. Complete data-point inventory (catch every signal)

This is the "don't miss any data point" checklist, organized by signal family and **annotated with TradeTalk status**:

- `HAVE` — already ingested/computed; just aggregate to theme level.
- `PARTIAL` — partially available (e.g., from yfinance free tier); upgrade later.
- `GAP` — needs a new connector / source.

### 3.1 Narrative & media (is the story early, mainstream, or saturated?)

| Data point | Status | Where it comes from in TradeTalk |
|---|---|---|
| Headline / article count per theme (7d, 30d) | `HAVE` | `news_scanner.py` (Google News RSS), `portfolio_news.py` (NewsAPI), `fincrawler_client` `/news` |
| News sentiment per theme | `HAVE` | LLM classifier in `portfolio_news.py` / `connectors/social.py` |
| Source weight (mainstream vs niche) | `PARTIAL` | tag source domain in news connectors; add a credibility map |
| Narrative keywords / topic clusters | `PARTIAL` | per-theme keyword dictionaries (new, seeded from `themes.py`) + existing LLM classify |
| Narrative velocity / acceleration | `GAP→easy` | compute from stored daily counts (`keyword_velocity` formula, article §7.2) |
| "Best stocks to buy / next Nvidia / supercycle" phrase density | `GAP→easy` | regex/keyword pass over the same news feed |
| Media attention percentile vs history | `HAVE pattern` | `percentile_rank` from `picks_shovels/scoring.py` over stored history |
| Analyst upgrade/downgrade clusters, coverage initiations, PT changes | `PARTIAL` | yfinance `recommendations` (`data_lake/ingest_events.py`); revision breadth needs a vendor later |

### 3.2 Productization (has the theme been turned into a sellable product?)

| Data point | Status | Source |
|---|---|---|
| New thematic **ETF filings** (N-1A / S-1) by theme | `GAP` | **NEW**: EDGAR full-text search for N-1A/S-1; reuse `sec/edgar_client.py` transport |
| ETF launch date / issuer / objective language | `GAP` | **NEW**: parse N-1A objective text → classify to theme |
| Multiple-issuer clustering on one theme | `GAP→derived` | derived from filings connector |
| ETF AUM + AUM growth | `GAP` | **NEW**: ETF flow connector (provider TBD; see §4) |
| ETF flows (weekly inflow/outflow, acceleration, reversal) | `GAP` | **NEW**: §4 |
| ETF holdings / concentration | `PARTIAL` | sector SPDR map exists (`sp500_ingestion_pipeline.py`, `market_intel._SECTOR_ETFS`); thematic holdings need source |
| Late ETF launch after big price move (saturation flag) | `GAP→derived` | derived: launch date vs theme price run |

### 3.3 Institutional footprint (is smart money accumulating or distributing?)

| Data point | Status | Source |
|---|---|---|
| Institutional ownership by ticker, position changes | `HAVE` | `coral_skills/sec_13f_ingestion.py` → `thirteen_f_holdings` |
| New / exited / increased / decreased positions | `HAVE` | same (5y / 20-quarter history) |
| Sector/theme ownership **breadth** (broad vs 1–2 names) | `HAVE→aggregate` | roll `thirteen_f_holdings` up by theme membership |
| Top-holder concentration / crowding | `HAVE→aggregate` | `fund_leaderboard` metrics |
| Hedge-fund vs long-only ownership mix | `PARTIAL` | `fund_universe.yml` classifies funds; extend tagging |
| Insider selling spikes | `PARTIAL` | `sec_filing_job.py` SITG/insider fields (DEF 14A/Form 4 text); Form 4 structured ingest is a later upgrade |
| Block-volume / relative-volume proxy (fast institutional) | `HAVE` | volume z-score from price series (`market_intel`, `mcp_server/feature_mart` `return_zscore_60d`) |
| Options activity proxy | `PARTIAL` | `market_intel._fetch_options_flow` (SPY put/call); per-theme is a later upgrade |

> **Critical design rule (from the article and from our lag reality):** 13F is **45-day lagged**, so it must **confirm**, not lead. Mirror `picks_shovels`/article §7.4: `institutional_conviction = 0.65 * fast_proxy + 0.35 * slow_13f_confirmation`. Fast proxy = ETF flow accel + volume z-score + breadth expansion.

### 3.4 Market confirmation & breadth (is price agreeing?)

| Data point | Status | Source |
|---|---|---|
| RS vs SPY, RS momentum (theme/sector) | `HAVE` | `macro_flow/macro_flow_agent.py` (`rs_ratio`, `rs_momentum`), `flow_scores` table |
| Capital-flow score (CMF) | `HAVE` | `macro_flow` flow component |
| Price momentum 1W/1M/3M/6M/12M | `HAVE` | `picks_shovels/data.py` `momentum_from_closes` |
| Volume z-score (rolling) | `HAVE` | `mcp_server/feature_mart.return_zscore_60d`, derivable from closes |
| % of theme stocks above 50/200DMA | `HAVE→aggregate` | from `momentum_from_closes.above_50/200dma_pct` across members |
| Equal-weight vs cap-weight basket spread (narrowing-leadership flag) | `HAVE→derive` | build both baskets from member closes (article §7.6 red flag) |
| Median stock return, new highs/lows within theme | `HAVE→derive` | from member price series |

### 3.5 Narrative-reality alignment (do fundamentals support the story?)

| Data point | Status | Source |
|---|---|---|
| Revenue / segment growth, acceleration | `HAVE` | XBRL companyfacts (`backtest_data.py`), yfinance quarterly (`picks_shovels/data.fetch_operating_metrics`), `sp500_ingestion_pipeline` narratives |
| CAPEX / R&D growth, opex investment language | `PARTIAL` | 10-K/10-Q text via FinCrawler; `macro_flow/capex_data.py` has hyperscaler capex |
| Backlog / RPO / deferred revenue | `PARTIAL` | not standardized in XBRL; scorer already keeps these neutral until sourced (`picks_shovels/scoring.backlog_rpo_score`) |
| Margin trend by segment | `PARTIAL` | yfinance margins (levels), trend needs quarterly history |
| Guidance changes (8-K) | `HAVE` | `data_lake/ingest_daily_events._fetch_sec_8k_events` |
| Estimate revisions (EPS/rev) | `PARTIAL` | yfinance earnings/recommendations; vendor upgrade later |
| Filing keyword frequency change QoQ/YoY (theme terms) | `GAP→easy` | keyword pass over FinCrawler 10-K/10-Q text using theme dictionaries |

### 3.6 Retail saturation & exit risk

| Data point | Status | Source |
|---|---|---|
| Mainstream media frequency percentile | `HAVE` | §3.1 |
| Social mention velocity (YouTube/Reddit/Stocktwits) | `HAVE` | `social_sources.py`, `connectors/youtube.py` |
| YouTube finance-video count/views per theme | `HAVE` | `connectors/youtube.py` (Data API) + RSS |
| Google Trends | `GAP→optional` | not present; optional `pytrends`, compliance-permitting |
| Late-stage "buy now" article density | `GAP→easy` | §3.1 phrase pass |
| Retail ETF inflow acceleration | `GAP` | §4 ETF flows |
| Valuation stretch percentile | `HAVE` | `picks_shovels/scoring.valuation_risk_score`, `metric_primitives` |
| Short interest / days-to-cover | `PARTIAL` | `connectors/shorts.py` (yfinance + stockanalysis.com) |
| Insider selling spike | `PARTIAL` | §3.3 |

### 3.7 Macro regime context

| Data point | Status | Source |
|---|---|---|
| 10Y/2Y yields, curve, fed funds, DXY, oil, credit spreads, CPI, PMI, unemployment, M2 | `HAVE` | `connectors/fred.py`, `fred_series.py`, `data_lake/ingest_macro.py` |
| VIX, market breadth | `HAVE` | `market_intel.py`, macro snapshot |
| Regime label (risk-on / rate-cut beneficiary / liquidity / AI-capex supercycle …) | `PARTIAL` | `MarketRegime` schema + `macro_flow`; extend regime taxonomy from article §5.8 |

**Bottom line:** every Tier-1→Tier-4 signal in the article is either already in the repo or a thin derivation, **except** ETF productization (N-1A/S-1), ETF flows, and Google Trends. Those three are the only genuine new external dependencies.

---

## 4. Where to find each data set (source map, incl. the "media narrative start / influencer" asks)

The user specifically asked where to find: *media narrative starting to build, influencers setting sentiment, and the product pipeline*. Concrete sources, preferring what's already wired and free/compliant:

| Signal the user named | Best source(s) | In repo today? | How to access |
|---|---|---|---|
| **Media narrative starting to build** (early seeding) | Google News RSS query per theme keyword; FinCrawler `/news`; NewsAPI (`NEWSAPI_KEY`) | `news_scanner.py`, `fincrawler_client.py`, `portfolio_news.py` | Query each theme's keyword dictionary daily; store counts; compute velocity/acceleration percentile. Earliest seeding shows as low-base count starting to rise. |
| **Influencers setting sentiment** | YouTube Data API v3 (video count + view growth + title keywords per theme); YouTube channel RSS; Reddit `.json`; Stocktwits symbol stream | `connectors/youtube.py`, `social_sources.py` | Aggregate per theme → Influencer Amplification Score + Retail Saturation Score. Weight by channel finance-influence (subscriber/view tiers). |
| **Sell-side thematic research / analyst push** | yfinance recommendations/upgrades; news headlines mentioning bank research | `data_lake/ingest_events.py`, news connectors | Upgrade-cluster count in 7/14/30d; "late upgrade after big run" exit flag. Full FactSet/Bloomberg revision breadth is a paid upgrade. |
| **Product pipeline being prepared (ETF filings)** | **SEC EDGAR full-text search** for `N-1A` and `S-1` thematic funds; issuer = BlackRock/Vanguard/State Street/etc. | **NEW** (transport exists: `sec/edgar_client.py`) | Poll EDGAR FTS API (`efts.sec.gov/LATEST/search-index?...`) for new N-1A/S-1; classify objective text to a theme; multiple issuers on one theme = manufacturing signal. |
| **ETF flows / AUM (distribution underway)** | Provider options: ETF.com / VettaFi, Nasdaq, or issuer sites; some free weekly aggregates via ICI; yfinance gives ETF price/volume but **not** flows | **NEW** | Add `connectors/etf_flows.py`. MVP can proxy flow with ETF **shares-outstanding change × price** if a clean flow feed is unavailable; flag as `PARTIAL` confidence. |
| **13F institutional accumulation/distribution** | SEC EDGAR 13F (already fully ingested) | `coral_skills/sec_13f_ingestion.py`, `fund_leaderboard_*` | Aggregate to theme; breadth + concentration; **confirm only (45-day lag)**. |
| **Conference-circuit / analyst-hiring leading indicators** (article Tier 1 #2/#3) | Not machine-ingestable cheaply; LinkedIn/job-board scraping is **disallowed** | `GAP / out of scope` | Document as a manual analyst input or a later licensed-data add-on. **Do not scrape protected sites.** |
| **Macro regime** | FRED (free), yfinance VIX | `connectors/fred.py`, `market_intel.py` | Already available; extend regime classifier. |
| **Relative strength / rotation confirmation** | Internal price data → RRG | `macro_flow` | Already computed (`rs_ratio`, `rs_momentum`). |
| **Google Trends (optional retail proxy)** | `pytrends` (unofficial) | `GAP / optional` | Optional; rate-limited and ToS-sensitive — gate behind a flag, low confidence weight. |

> **Compliance guardrail (already a repo rule in `AGENTS.md`/RAG policy):** do not scrape sources that prohibit it or require bypassing bot/CAPTCHA protections (LinkedIn, paywalled media). Prefer official APIs and EDGAR. ETF flows and analyst revisions are the most likely paid-data upgrades.

---

## 5. Lifecycle phases & scores (theme level)

Reuse the article's 8-phase model and the deterministic classifier, but compute every sub-score with the **existing** `percentile_rank` / `_blend` / `confidence_level` helpers from `backend/picks_shovels/scoring.py` so behavior matches the rest of the app (never fabricate; renormalize over present components).

**Phases:** `DISCOVERY_SEEDING → EARLY_ACCUMULATION → ACCELERATION → MAINSTREAM_MOMENTUM → SATURATION_CROWDING → DISTRIBUTION_RISK → EXIT_ROTATION_AWAY → DORMANT_REBASE`, plus `LOW_CONFIDENCE_WATCHLIST`.

**Top-level scores (all 0–100):** Formation, Accumulation, Acceleration, Distribution-Risk, Exit-Risk, Institutional-Conviction, Retail-Saturation, Narrative-Reality-Alignment, Productization, Market-Confirmation, Breadth-Quality, Macro-Tailwind, Confidence.

**Phase-aware weighting** (do not use one static weight): see article §8. Implement the deterministic `classify_theme_phase(scores)` (article §8.5) first; add ML calibration only after backtests exist.

**Score → existing-signal wiring:**

```text
market_confirmation  ← macro_flow rs_ratio/rs_momentum/flow + volume z-score + breadth
breadth_quality      ← % members >50/200DMA, equal- vs cap-weight spread, median return
institutional_conv.  ← 0.65*fast_proxy(flows,volume,breadth) + 0.35*13F(breadth,concentration)
narrative            ← news velocity/accel, sentiment, attention percentile (theme keywords)
productization       ← N-1A/S-1 filings, launch cadence, issuer quality, AUM/flow growth
retail_saturation    ← social velocity (YT/Reddit/Stocktwits) + media freq + "buy now" density
reality_alignment    ← revenue accel, capex, guidance(8-K), keyword growth in 10-K/10-Q
macro_tailwind       ← FRED regime + sector fit
confidence           ← data coverage + signal agreement + history depth + freshness
```

---

## 6. Data model (SQLite-first, Postgres dual-write — match repo convention)

The repo uses **plain SQL via the migration runner** (`backend/migrations/runner.py`, `_schema_migrations`), SQLite locally and Postgres when `postgres_enabled()`. **Do not** introduce an ORM. Two storage options:

- **MVP (recommended):** clone the Picks & Shovels snapshot store — one `nr_snapshots` row + N `nr_theme_rows` (payload JSON), exactly like `ps_snapshots`/`ps_rows`. Fastest path, reuses freshness/job patterns, no new migration group needed beyond a new SQLite file (`narrative_radar.db`, resolved via `TRADETALK_DATA_DIR`).
- **Scale-up:** add a `migrations/narrative_radar/` group with the normalized tables below (adapted from article §6 to our conventions — `TEXT` ids, no `gen_random_uuid()` dependency; UUIDs via `decision_ledger.new_decision_id()` style hex).

Normalized tables (scale-up): `themes`, `theme_stock_exposure`, `theme_daily_features`, `theme_scores`, `company_theme_scores`, `evidence_events`, `theme_alerts`, `theme_backtests`. Column lists follow article §6; key adaptations:

- `id` columns are `TEXT` (hex), not Postgres `UUID`, so the same DDL runs on SQLite and Postgres.
- `evidence_events.source_type` enum should include our real sources: `SEC_10K|SEC_10Q|SEC_8K|SEC_DEF14A|SEC_13F|SEC_N1A|ETF_FLOW|NEWS|ANALYST|SOCIAL|YOUTUBE|MARKET_DATA|MACRO|RRG|BACKTEST|SYSTEM`.
- Prefer writing evidence into the existing `knowledge_store` collection (`events_semantic` or a new `theme_evidence`) so the **evidence timeline reuses `query_with_refs`** and threads into ledger `EvidenceRef`s.

---

## 7. Agent architecture & pipeline integration

1. **New service package `backend/narrative_radar/`** mirroring `picks_shovels/`:
   `themes.py` (extended taxonomy + keyword dicts), `data.py` (signal fetchers — reuse macro_flow, 13F, news, social, SEC), `features.py` (build `theme_daily_features`), `scoring.py` (phase-aware scores via `picks_shovels.scoring` helpers), `lifecycle.py` (`classify_theme_phase`), `store.py`, `engine.py` (two-pass scan + snapshot), `ledger.py` (emit), `explain.py` (deterministic explanation JSON), `alerts.py`.

2. **Decision ledger (mandatory per `AGENTS.md`).** On each snapshot, emit one `decision_type="theme_phase"` row per theme:
   - `symbol` = theme slug; `horizon_hint` = `21d` (rotation cadence); `verdict` = phase label; `confidence` = confidence_score/100.
   - `features` = the 13 top-level scores as `FeatureValue`s + `market_regime`.
   - `evidence` = `EvidenceRef`s from `knowledge_store.query_with_refs(...)`.
   - `prompt_versions`/`registry_snapshot_id` from `registry_attribution()`.
   - Wrap in try/except (ledger failure must never break the scan) — copy `picks_shovels/ledger.py` verbatim shape.

3. **Backtesting for free.** `outcome_grader` already grades any ledger row by forward **excess return vs SPY** at `1d/5d/21d/63d/252d`. Because theme verdicts carry a direction (accelerating = bullish, distribution/exit = bearish), the grader produces hit-rate/lead-time automatically; surface via `GET /harness/hit-rates` and the new backtest tab. Seed historical cycles (AI/Nvidia, clean energy, EV, GLP-1, uranium, regional banks) per article §16.

4. **CORAL hub.** Add a `narrative` reflection agent to `coral_heartbeat.py` and emit `handoff_theme_phase` events via `log_handoff_event` so nightly **dreaming** (`coral_dreaming.py`) can summarize rotation shifts. `decision_emitted` dual-write is automatic from the ledger.

5. **Scheduler (`backend/daily_pipeline.py` + cron endpoints).**
   - **Daily** (00:00 UTC pipeline): market confirmation, breadth, narrative velocity, recompute scores + phase + alerts.
   - **Weekly:** ETF flows + AUM; social/influencer aggregation; ETF N-1A/S-1 filing poll.
   - **Quarterly:** 13F theme aggregation refresh; fundamentals reality refresh; theme-exposure re-evaluation.
   - **Event-driven:** new N-1A filing, RRG breakout/breakdown, exit-risk threshold cross → `theme_alerts` + notification.
   - Add `POST /knowledge/narrative-radar-run` cron (guarded by `PIPELINE_CRON_SECRET`) and a GitHub Actions schedule mirroring `render-daily-pipeline.yml`.

6. **Feature flag + off-switch.** `NARRATIVE_RADAR_ENABLE` (mirror `PICKS_SHOVELS_ENABLE`); honor `DECISION_LEDGER_ENABLE`. Page hidden when off.

---

## 8. API design (follow existing router conventions)

New router `backend/routers/narrative_radar.py`, registered in `main.py` alongside `picks_shovels_router`. Models in `backend/schemas.py` (reuse `DataFreshness`). Endpoints mirror the article §10 + the Picks & Shovels job/poll pattern:

| Method + path | Purpose | Source |
|---|---|---|
| `GET /narrative-radar/themes?sort=&phase=&minConfidence=&limit=` | Radar overview (one row per theme: phase + 13 scores + summary) | DB snapshot |
| `GET /narrative-radar/themes/{slug}` | Theme detail (scores, history, exposure, productization, institutional, media, exit-risk, freshness) | DB |
| `GET /narrative-radar/themes/{slug}/timeline?from=&to=` | Evidence timeline | `evidence_events` / RAG |
| `GET /narrative-radar/themes/{slug}/stocks?sort=beneficiaryQuality` | Beneficiary table (reuse `company_theme_scores` / picks-shovels rows) | DB |
| `GET /narrative-radar/themes/{slug}/backtests` | Historical cycle results | `theme_backtests` + ledger outcomes |
| `GET /narrative-radar/alerts?severity=&limit=` | Alert center | `theme_alerts` |
| `POST /narrative-radar/refresh` (+ `GET /status`) | Trigger/poll scan (job-state like `/picks-shovels/refresh` + `/status`) | engine |

Every score payload includes the **explanation JSON** (top positive/negative drivers + data freshness) per article §15, and the compliance disclaimer string.

---

## 9. Frontend & visualization plan (reuse charts)

New route `/narrative-radar` (lazy component `NarrativeRadarUI.jsx`), wired in `frontend/src/App.jsx`, called via `frontend/src/api.js` helpers. Visualization reuses the libraries already in `frontend/package.json` (`recharts`, `@nivo/sankey`, `@xyflow/react`, `lucide-react`) and existing custom components (`Sparkline`, `SemiGauge`, the `GlobalCapFlowDashboard` heatmap).

**Main page components:**

1. **Theme lifecycle cards** — phase badge + confidence + the 5 headline scores (Momentum, Institutional Conviction, Retail Saturation, Reality Alignment, Exit Risk) + one-line summary. (New card, styled like existing dashboard panels.)
2. **Lifecycle heatmap** — rows = themes, columns = Formation/Accumulation/Acceleration/Retail-Saturation/Reality/Distribution/Exit/Confidence. **Inverse color** for saturation & exit (high = red). Reuse the `heatmapTone()` pattern from `macro/GlobalCapFlowDashboard.jsx`.
3. **Rotation quadrant (RRG)** — reuse `macro_flow` RRG payload (`rs_ratio` vs `rs_momentum`) via a Recharts scatter; this is the "money rotating out of Tech into Healthcare/Industrials" picture from the article. The orphaned `MacroFlowPanel.jsx` already renders flow visuals and can be linked.
4. **Emerging-themes panel** (Formation high, Saturation low) and **Distribution/Exit-warnings panel** (Exit-Risk ≥ 70).
5. **Macro regime banner** + **data-freshness badges** (`Freshness.jsx`).

**Theme detail tabs** (article §11.3): Overview · Evidence Timeline · Stocks · Institutional Footprint · ETF/Product Pipeline · Narrative & Media · Fundamentals Reality Check · Breadth & Market · Exit Risk · Backtest History. Charts: Recharts line (score history), Nivo Sankey (capital flow), xyflow (supply-chain beneficiary graph — reuse `supplyChain/` + `macro_flow/stock_graph.py`), Sparkline (per-metric), SemiGauge (composite).

**Recommendation labels (compliance-safe):** Early Watchlist · Accumulation Candidate · Confirmed Momentum · Crowded Momentum · Distribution Risk · Exit / Avoid Chase · Dormant / Rebase · Low Confidence. Avoid buy/sell.

---

## 10. Phased delivery (with offline tests per `AGENTS.md`)

Tests must be **offline** and seed a temp `DECISIONS_DB_PATH` / `NARRATIVE_RADAR_DB_PATH`, then assert ledger rows appear (`backend/tests/test_decision_ledger_producers.py` is the reference shape). Run via `./scripts/run_backend_tests.sh` (Python 3.10+) and `PYTHONPATH=. python -m unittest backend.tests.test_narrative_radar -v`.

| Phase | Scope | Reuses | Acceptance |
|---|---|---|---|
| **NR-1 Taxonomy + store + scaffolding** | `narrative_radar/` package, extended themes + keyword dicts, snapshot store, engine skeleton, feature flag | `picks_shovels/*`, `themes.py` | Can create themes; snapshot persists; `/status` polls. Unit test: taxonomy validates. |
| **NR-2 Market confirmation + breadth** | Theme baskets (equal/cap weight), RS, RS-momentum, volume z, % >50/200DMA, breadth-quality | `macro_flow`, `picks_shovels/data.py`, `market_intel` | Each theme has market-confirmation + breadth features. |
| **NR-3 Scoring + lifecycle classifier + ledger** | 13 scores, `classify_theme_phase`, confidence, explanation JSON, `decision_type="theme_phase"` emit | `picks_shovels/scoring.py`, `decision_ledger`, `registry_attribution` | Every active theme scored daily + phase + emit. Test asserts a `decision_events` row + features. |
| **NR-4 API + frontend MVP** | Overview/detail/timeline/stocks endpoints, `NarrativeRadarUI` page, cards + heatmap + freshness | `schemas.py`, `api.js`, recharts, `GlobalCapFlowDashboard` heatmap | Investor sees themes by phase, clicks into evidence. E2E smoke spec under `e2e/`. |
| **NR-5 Institutional footprint** | 13F theme aggregation (breadth/concentration), fast-proxy blend | `fund_leaderboard_*`, 13F ingestion | Footprint tab shows broadening vs narrowing. |
| **NR-6 Productization** | N-1A/S-1 connector, classify to theme, launch cadence, productization score, evidence events | `sec/edgar_client.py`, `knowledge_store` | App flags new thematic products + multi-issuer clustering. |
| **NR-7 Narrative/media + retail saturation** | Theme keyword news velocity/sentiment, social aggregation, saturation score, phrase density | `news_scanner`, `social_sources`, `connectors/youtube.py` | App distinguishes early vs mainstream vs saturated. |
| **NR-8 Reality alignment** | Revenue accel, capex, guidance(8-K), filing keyword growth | FinCrawler SEC, XBRL, `data_lake/ingest_daily_events` | Hype-risk themes flagged (narrative high, reality weak). |
| **NR-9 ETF flows + alerts + backtests** | ETF flow connector, alert rules (article §14), seed historical cycles, surface grader hit-rates | `connectors/etf_flows.py` (new), `outcome_grader`, `harness` | Alerts with evidence; backtest tab shows hit-rate/lead-time. |

**MVP cut = NR-1 → NR-4** (taxonomy, market+breadth, scoring+phase+ledger, page). That already answers: *which themes are emerging vs crowded vs exiting, why, with confidence and freshness* — using only data we have today, zero new external dependencies. Productization/flows/social are additive.

---

## 11. Risks, guardrails, and what NOT to do

- **13F lag:** never let 13F lead detection (45-day lag). Fast proxy leads; 13F confirms.
- **Don't fabricate:** keep the `picks_shovels` rule — components with no data stay neutral and the blend renormalizes; lower confidence instead of inventing numbers (truthful-data contract, repo-wide).
- **Don't scrape protected sources** (LinkedIn hiring, paywalled media). Use EDGAR + official APIs.
- **Compliance copy mandatory** on every surface; no "manipulation/exit-liquidity/scam" labels — use "narrative-driven capital rotation", "institutional participation weakening", "late-cycle retail saturation risk".
- **Off-switch:** `NARRATIVE_RADAR_ENABLE=0` hides the page and no-ops the jobs; ledger respects `DECISION_LEDGER_ENABLE`.
- **Cost/rate limits:** reuse the bounded thread-pool + chunked-fetch + inter-chunk delay pattern from `picks_shovels/engine.py` to avoid Yahoo/EDGAR rate-limiting on Cloud Run.

---

## 12. Success criteria (investor can answer)

What theme is emerging · why we detect it · which evidence supports it · which companies are real beneficiaries · is it early/confirmed/crowded/exiting · do fundamentals confirm · is institutional participation broadening or weakening · is retail saturation dangerous · how fresh/reliable is the data · how did similar historical signals perform.

---

### Appendix A — file-path index (where each piece lives / will live)

- Theme taxonomy: `backend/picks_shovels/themes.py` (extend) → `backend/narrative_radar/themes.py`
- Scoring helpers: `backend/picks_shovels/scoring.py` (reuse) → `backend/narrative_radar/scoring.py`
- RS/rotation: `backend/macro_flow/macro_flow_agent.py`, `backend/macro_flow/store.py`, `GET /macro/flow/rrg`
- 13F: `backend/coral_skills/sec_13f_ingestion.py`, `backend/fund_leaderboard_*`, `GET /api/funds/*`
- SEC text/XBRL: `backend/fincrawler_client.py`, `backend/connectors/backtest_data.py`, `backend/sec/edgar_client.py`, `backend/sec_filing_job.py`
- News/social: `backend/connectors/news_scanner.py`, `backend/connectors/social.py`, `backend/social_sources.py`, `backend/connectors/youtube.py`, `backend/routers/portfolio_news.py`
- Ledger/grading: `backend/decision_ledger.py`, `backend/decision_ledger_registry.py`, `backend/outcome_grader.py`, `backend/harness/`
- RAG/evidence: `backend/knowledge_store.py` (`query_with_refs`)
- Scheduler/cron: `backend/daily_pipeline.py`, `backend/routers/knowledge.py`, `.github/workflows/render-daily-pipeline.yml`
- Frontend charts: `frontend/src/macro/GlobalCapFlowDashboard.jsx`, `frontend/src/macroFlow/MacroFlowPanel.jsx`, `frontend/src/components/{Sparkline,Freshness}.jsx`, `frontend/src/api.js`
- New code: `backend/narrative_radar/*`, `backend/routers/narrative_radar.py`, `frontend/src/NarrativeRadarUI.jsx`
