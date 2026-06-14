import React, { useEffect, useRef } from 'react';
import mermaid from 'mermaid';
import ReactMarkdown from 'react-markdown';

mermaid.initialize({
    startOnLoad: false,
    suppressErrorRendering: true,
    theme: 'dark',
    securityLevel: 'loose',
});

function MermaidChart({ chart }) {
    const chartRef = useRef(null);

    useEffect(() => {
        if (!chart) return;
        const el = chartRef.current;
        if (!el) return;
        const id = `mermaid-${Math.random().toString(36).substring(7)}`;
        let cancelled = false;
        mermaid.render(id, chart).then((result) => {
            if (!cancelled && chartRef.current === el) {
                el.innerHTML = result.svg;
            }
        }).catch((err) => {
            console.error("Mermaid error", err);
        });
        return () => {
            cancelled = true;
        };
    }, [chart]);

    return (
        <div style={{ display: 'flex', justifyContent: 'center', margin: '20px 0', padding: '20px', background: 'rgba(15,23,42,0.92)', borderRadius: '10px', border: '1px solid rgba(148,163,184,0.14)', overflowX: 'auto' }}>
            <div ref={chartRef} />
        </div>
    );
}

const markdownContent = `
# TradeTalk System Diagrams

This document contains simplified diagrams of the TradeTalk architecture and its sub-systems to help understand the flow of data, API usage, and fallback mechanisms.

## 1. Full System Architecture

This diagram shows how a user interacts with the system, from the frontend all the way to external data sources and background jobs.

\`\`\`mermaid
flowchart TB
    User((User))

    subgraph Frontend["Frontend (React / Vite on Vercel)"]
        SPA["Single Page App\nUI Dashboards"]
    end

    subgraph Backend["FastAPI Backend (Render)"]
        API["API Routers\n+ /mcp/sp500"]
        Agents["Agent Swarms & Debates"]
        RAG["Knowledge Store (RAG)"]
        LLM_Client["LLM Client Engine"]
        DTL["Data Trust Layer\nfreshness.py + market_calendar"]
        LiveQuote["Live Quote Engine\nconnectors/live_quote.py"]
        Background["Background Tasks\n(News loop, Cron, sp500-ingest)"]

        API --> Agents
        API --> DTL
        API --> LiveQuote
        API --> Background
        Agents --> RAG
        Agents --> LLM_Client
        LiveQuote --> DTL
    end

    subgraph DataStores["Data Stores"]
        SQLite[("SQLite (Disk)\nAlerts, Users, Ledger")]
        Supabase[("Supabase (pgvector)\nVector Embeddings")]
        DataLake[("Data Lake\nParquet / BigQuery\ndaily_prices")]
    end

    subgraph External["External APIs & Sources"]
        OpenRouter("OpenRouter (LLMs)")
        Gemini("Google Gemini")
        YFinance("yFinance / APIs")
        HF("Hugging Face Hub")
    end

    User <--> SPA
    SPA <-->|HTTPS| API

    RAG <--> Supabase
    API <--> SQLite

    LLM_Client --> OpenRouter
    LLM_Client --> Gemini

    Agents --> YFinance
    Background --> YFinance
    LiveQuote --> YFinance
    Background --> DataLake
    LiveQuote -.->|EOD fallback| DataLake

    RAG -.->|Optional| HF
\`\`\`

## 2. LLM Processing and Fallback System

TradeTalk uses a highly resilient LLM system. By default, it connects to OpenRouter, but it will gracefully degrade to Gemini or hardcoded rule-based responses if APIs fail or rate limits are hit.

\`\`\`mermaid
flowchart TD
    Request["Agent or User Request"]

    subgraph LLM_Engine ["LLM Client"]
        CheckPrimary{"Is GEMINI_PRIMARY\nenabled?"}

        OpenRouterPath["Try OpenRouter API\n(Qwen / Llama etc)"]
        GeminiPath["Try Gemini API\n(Gemini 3.1 Pro/Flash)"]

        CheckRateLimit{"Rate Limited\n(429)?"}
        RetryKeys["Round Robin to\nnext API Key"]

        Fallback["Rule-based JSON Templates\n(Offline mode)"]
    end

    Response["Structured JSON / Text Response"]

    Request --> CheckPrimary
    CheckPrimary -- "No" --> OpenRouterPath
    CheckPrimary -- "Yes" --> GeminiPath

    OpenRouterPath --> CheckRateLimit
    CheckRateLimit -- "Yes" --> RetryKeys
    RetryKeys --> OpenRouterPath

    OpenRouterPath -- "Failure / Timeout" --> GeminiPath
    GeminiPath -- "Failure" --> Fallback

    OpenRouterPath -- "Success" --> Response
    GeminiPath -- "Success" --> Response
    Fallback --> Response
\`\`\`

## 3. Data Ingestion & Scheduled Pipelines

TradeTalk does not just answer questions; it actively reads the market in the background to build a memory. This diagram shows how data flows into the system asynchronously.

\`\`\`mermaid
flowchart LR
    subgraph Triggers ["Triggers"]
        NewsLoop(("60s News Loop\n(Internal)"))
        CronJob(("00:05 UTC Cron\n(GitHub Actions)"))
        SP500Ingest(("sp500-ingest\n(Cron / startup)"))
    end

    subgraph Connectors ["Data Connectors"]
        RSS["Google News RSS"]
        FRED["FRED Macro Data"]
        YT["YouTube Transcripts"]
        Prices["yFinance Movers"]
    end

    subgraph Processing ["Processing"]
        Summarizer["LLM Summarizer\n(Extract Insights)"]
        Embedder["Embedding Model\n(Create Vectors)"]
    end

    subgraph Storage ["Knowledge Store"]
        DB[("Supabase pgvector\n(Vector Memory)")]
        SQL[("SQLite\n(Macro Alerts)")]
        Lake[("Data Lake\ndaily_prices Parquet")]
        SSE(("Real-time SSE\nNotifications"))
    end

    NewsLoop --> RSS
    CronJob --> FRED
    CronJob --> YT
    CronJob --> Prices
    SP500Ingest --> Prices

    RSS --> Summarizer
    FRED --> Summarizer
    YT --> Summarizer
    Prices --> Summarizer
    SP500Ingest --> Lake

    Summarizer --> Embedder
    Embedder --> DB

    RSS --> SQL
    RSS --> SSE
\`\`\`

## 4. Hugging Face Integrations

While not the primary database, Hugging Face serves as an optional layer for read-only snapshots, remote embeddings, and data lakes.

\`\`\`mermaid
flowchart TD
    Backend["TradeTalk Backend"]

    subgraph HuggingFace ["Hugging Face Ecosystem"]
        Inference["Inference API\n(Embeddings)"]
        Datasets["Hub Datasets\n(JSON / Parquet)"]
        Spaces["HF Spaces\n(Optional Hosting)"]
    end

    Backend -->|"1. Remote Embeddings\n(If Chroma on Render)"| Inference
    Backend -->|"2. Read-only RAG\n(VECTOR_BACKEND=hf)"| Datasets
    Backend -->|"3. Data Lake Sync\n(Historical Prices)"| Datasets
    Backend -.->|4. Keep-alive ping| Spaces
\`\`\`

## 5. Analysis Pipeline: Swarm + Debate (Consolidated)

TradeTalk simulates a Wall Street analyst team. The dashboard issues a **single** \`/decision-terminal\` request; the backend runs the swarm factor agents and then the 5-agent investment-committee debate **once**, and embeds both results (plus the roadmap) in one payload — so the Trace and Debate tabs render from the same call instead of re-running the pipeline. Every LLM call flows through one gateway bounded by a global concurrency semaphore (\`LLM_MAX_CONCURRENCY\`, default 6) with a per-call HTTP timeout and an NVIDIA-to-Gemini fallback.

\`\`\`mermaid
flowchart TB
    Input["Dashboard: Evaluate AAPL\n(single /decision-terminal call)"]

    subgraph Swarm ["1. Swarm Factor Agents (parallel)"]
        Short["Short Interest"]
        Social["Social Sentiment"]
        Poly["Polymarket"]
        Fund["Fundamental Health"]
    end

    subgraph Debate ["2. IC Debate Agents (parallel)"]
        Bull["Bull (Growth)"]
        Bear["Bear (Risk)"]
        Value["Value (Fundamentals)"]
        Momentum["Momentum (Price)"]
        Macro["Macro (Credit & Rates)"]
    end

    subgraph Gateway ["LLM Gateway"]
        Sem{"Concurrency semaphore\nLLM_MAX_CONCURRENCY=6\n+ per-call HTTP timeout"}
        Fallback["NVIDIA then Gemini fallback"]
    end

    RAG[("Vector Memory (RAG)\nPast lessons, YouTube, Debates")]
    Moderator{"Moderator Agent"}
    Payload["DecisionTerminalPayload\nverdict + roadmap +\nembedded swarm + debate"]

    Input --> Swarm
    Swarm -->|swarm_context| Debate
    RAG --> Debate
    Bull --> Moderator
    Bear --> Moderator
    Value --> Moderator
    Momentum --> Moderator
    Macro --> Moderator
    Swarm -.->|LLM calls| Sem
    Debate -.->|LLM calls| Sem
    Sem --> Fallback
    Moderator --> Payload
    Payload --> RAG
\`\`\`

## 6. Decision Ledger & Outcome Grader Loop

This diagram illustrates how agent decisions are recorded, graded against future market reality, and fed back into the system to improve future performance.

\`\`\`mermaid
flowchart TD
    subgraph Producers ["Decision Producers"]
        Factor["Swarm Factor Agents\n(AgentPair)"]
        Debate["IC Debate\n(Moderator)"]
        Chat["Chat Turns\n(Routers)"]
    end

    subgraph Ledger ["Decision Ledger (SQLite/Supabase)"]
        Events[("decision_events\n(Verdicts, RAG Refs, Prompts)")]
        Features[("feature_snapshots\n(Market Regime, Inputs)")]
        Outcomes[("outcome_observations\n(Returns, Grades)")]
        Violations[("contract_violations\n(Schema Drift)")]
    end

    subgraph Grader ["Outcome Grader (Scheduled)"]
        MarketTruth["Market Truth\n(yFinance / Prices)"]
        Evaluator["Grader Script\n(02:10 UTC)"]
    end

    subgraph Consumers ["Consumers (Feedback Loop)"]
        Stats["Feature Correlations\n(Hit Rates)"]
        SEPL["SEPL Reflection Source\n(Evolution)"]
        Replay["Model Swap Replay\n(Candidate Testing)"]
    end

    Factor -->|emit_decision| Events
    Debate -->|emit_decision| Events
    Chat -->|emit_decision| Events

    Factor -.->|inputs| Features
    Debate -.->|inputs| Features

    Events -.->|read pending| Evaluator
    MarketTruth --> Evaluator
    Evaluator -->|write grades| Outcomes

    Events --> Stats
    Features --> Stats
    Outcomes --> Stats

    Events --> SEPL
    Outcomes --> SEPL

    Events --> Replay
\`\`\`

## 7. SEPL Resource Registry & Tool Evolution

This diagram shows the Self-Evolving Prompts and Logic (SEPL) pipeline. The pipeline iterates over prompts and tools, perturbing them, evaluating them against offline fixtures, and committing improvements while an offline Kill Switch guards against regressions.

\`\`\`mermaid
flowchart TD
    subgraph Inputs ["Signals"]
        Reflections["Ledger Outcomes\n(Failure/Success)"]
        Fixtures["Offline JSON Fixtures\n(Test Cases)"]
    end

    subgraph Registry ["Resource Registry"]
        Prompts[("PROMPT YAMLs\n(Learnable)")]
        Tools[("TOOL Configs\n(Tiers 0-3)")]
    end

    subgraph Pipeline ["SEPL Finite State Machine"]
        Reflect["1. Reflect\n(Aggregate Lessons)"]
        Select["2. Select\n(Pick Weakest Resource)"]
        Improve["3. Improve\n(LLM for Prompts,\nMath for Tools)"]
        Evaluate["4. Evaluate\n(Score Candidate vs Active)"]
        Commit{"5. Commit\n(Margin > Threshold?)"}
    end

    subgraph Protection ["Kill Switch (Auto-Rollback)"]
        Verify["Check Post-Commit\nReflections vs Prior"]
        Restore{"Is Regression\n> Margin?"}
    end

    Reflections --> Reflect
    Reflect --> Select
    Prompts --> Select
    Tools --> Select
    Select --> Improve
    Improve --> Evaluate
    Fixtures --> Evaluate
    Evaluate --> Commit

    Commit -- "Yes" --> Registry

    Commit -.-> Verify
    Verify --> Restore
    Restore -- "Yes" --> Registry
\`\`\`

## 8. CORAL Hub & Named Agents

The CORAL Hub provides a central point for named system agents and infrastructure to persist heartbeat notes, share RAG-adjacent skills, and log meta-learning attempts.

\`\`\`mermaid
flowchart TD
    subgraph Schedulers ["Scheduled Triggers"]
        Heartbeat["Global Heartbeat\n(Every 30m)"]
        Reflections["Agent Reflections\n(Every 30m)"]
    end

    subgraph Agents ["Named Finance Agents"]
        Ingest["data_ingest\n(Freshness / MIL)"]
        Technical["technical\n(L1 Quotes, VIX)"]
        Sentiment["sentiment\n(Headlines)"]
        Gold["gold_analysis\n(GLD / UUP)"]
    end

    subgraph Hub ["CORAL Hub (SQLite)"]
        Notes[("TTL Notes")]
        Skills[("Reusable Skills")]
        Attempts[("Task Attempts")]
    end

    subgraph Infrastructure ["Legacy / Infrastructure"]
        Trace["swarm_trace"]
        Dream["dream_synthesizer"]
        OldHeartbeat["heartbeat"]
    end

    Heartbeat --> OldHeartbeat
    Reflections --> Ingest
    Reflections --> Technical
    Reflections --> Sentiment
    Reflections --> Gold

    Ingest -->|add_note| Notes
    Technical -->|add_note| Notes
    Sentiment -->|add_note| Notes
    Gold -->|add_note| Notes
    OldHeartbeat -->|add_note| Notes

    Trace -->|record_attempt| Attempts
    Dream -->|add_skill| Skills
\`\`\`

## 9. Data Trust Layer (Freshness & Truthful "As Of")

The **Data Trust Layer** ensures every market surface is honest about data age. A shared `market_calendar` computes the real last completed US session (holiday-aware, never-expiring). The `freshness.py` policy registry stamps a `DataFreshness` envelope on API payloads (`tier`, `is_stale`, `degraded`, `source`). The frontend renders `FreshnessBadge`, `StaleValue`, and `DataTrustBanner` across Macro, Dashboard, Decision Terminal, Daily Brief, Backtest, Chat quote cards, and the live-quote widget. Observability: `GET /health/data-freshness`.

\`\`\`mermaid
flowchart TD
    subgraph Producers ["API producers (envelope at boundary)"]
        Brief["/daily-brief\n/morning-brief"]
        Macro["/macro\n/stock-fundamentals"]
        DT["/decision-terminal"]
        MCP["/mcp/sp500/live-quote"]
        Other["/backtest, /scorecard,\n/predictor/forecast, chat quote_card"]
    end

    Calendar["market_calendar.py\nlast_completed_session\n(holiday-aware)"]
    Registry["freshness.py\nassess / assess_spot\n(age vs session policies)"]
    Envelope["DataFreshness envelope\ntier, is_stale, degraded, source"]
    Health["GET /health/data-freshness"]

    subgraph BriefFlow ["Daily brief: live-first strategy"]
        Stale{"stored data\nis_stale?"}
        LiveMovers["market_intel live movers"]
        Stored["stored snapshot\n+ staleness warning"]
    end

    subgraph UI ["Frontend trust components"]
        Badge["FreshnessBadge"]
        StaleVal["StaleValue\n(strict hides stale prices)"]
        Banner["DataTrustBanner"]
    end

    Brief --> Stale
    Calendar --> Registry
    Stale -- "fresh" --> Registry
    Stale -- "stale" --> LiveMovers
    LiveMovers -- "ok" --> Registry
    LiveMovers -- "fail" --> Stored --> Registry

    Macro --> Registry
    DT --> Registry
    MCP --> Registry
    Other --> Registry
    Registry --> Envelope
    Registry --> Health
    Envelope --> Badge
    Envelope --> StaleVal
    Envelope --> Banner
\`\`\`

## 10. MCP S&P 500 Live Quote Surface

The `/mcp/sp500` server historically served **historical** data-lake queries (`price-window`, `movement-context`, …). It now also exposes **live** quotes via a hedged keyless engine: Yahoo `fast_info` primary (250ms hedge), then parallel Yahoo chart / Stooq / FinCrawler, then EOD fallback from `daily_prices` populated by `sp500-ingest`. The home-page `LiveQuoteWidget` calls `GET /mcp/sp500/live-quote` and renders price/change with trust badges.

\`\`\`mermaid
flowchart TD
    Widget["LiveQuoteWidget\n(DailyBrief home page)"]
    Route["GET /mcp/sp500/live-quote\nGET /live-quotes"]
    Engine["connectors/live_quote.py"]
    Gate{"S&P 500\nuniverse gate"}
    Cache{"TTL cache\nLIVE_QUOTE_TTL_SEC"}

    Primary["Primary: yahoo fast_info\n(wait LIVE_QUOTE_HEDGE_DELAY_MS)"]
    FanOut["Fan-out parallel:\nyahoo_chart, stooq, fincrawler\nfirst valid wins"]
    Lake["EOD fallback:\ndaily_prices last close\n(sp500-ingest cron)"]
    None503["503 insufficient_data\n(live + lake both fail)"]

    FreshLive["assess_spot\nlive / delayed / degraded"]
    FreshEod["assess session_pct\nEOD / stale"]
    TrustUI["StaleValue + FreshnessBadge"]

    Widget --> Route --> Gate --> Engine
    Engine --> Cache
    Cache -->|miss| Primary
    Primary -->|valid| FreshLive
    Primary -->|timeout or fail| FanOut
    FanOut -->|valid| FreshLive
    FanOut -->|all fail| Lake
    Lake -->|has close| FreshEod
    Lake -->|missing| None503
    FreshLive --> TrustUI
    FreshEod --> TrustUI
\`\`\`

`;

export default function SystemDiagramsUI() {
    return (
        <div style={{ padding: '24px 28px', maxWidth: 1100, margin: '0 auto', color: '#cbd5e1' }}>
            <ReactMarkdown
                components={{
                    code({ node, inline, className, children, ...props }) {
                        const match = /language-(\w+)/.exec(className || '')
                        const isMermaid = match && match[1] === 'mermaid'

                        if (!inline && isMermaid) {
                            return <MermaidChart chart={String(children).replace(/\n$/, '')} />
                        }

                        return !inline ? (
                            <pre style={{ background: 'rgba(255,255,255,0.05)', padding: '12px', borderRadius: '8px', overflowX: 'auto' }}>
                                <code className={className} {...props}>
                                    {children}
                                </code>
                            </pre>
                        ) : (
                            <code style={{ background: 'rgba(255,255,255,0.1)', padding: '2px 4px', borderRadius: '4px' }} className={className} {...props}>
                                {children}
                            </code>
                        )
                    },
                    h1: ({node, ...props}) => <h1 style={{ color: '#f1f5f9', fontSize: '28px', marginBottom: '20px', fontWeight: '800' }} {...props} />,
                    h2: ({node, ...props}) => <h2 style={{ color: '#f1f5f9', fontSize: '22px', marginTop: '30px', marginBottom: '15px', fontWeight: '700' }} {...props} />,
                    p: ({node, ...props}) => <p style={{ color: '#94a3b8', fontSize: '14px', lineHeight: '1.6', marginBottom: '15px' }} {...props} />
                }}
            >
                {markdownContent}
            </ReactMarkdown>
        </div>
    );
}
