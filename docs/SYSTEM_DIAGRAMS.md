# TradeTalk System Diagrams

This document contains simplified diagrams of the TradeTalk architecture and its sub-systems to help understand the flow of data, API usage, and fallback mechanisms.

## 1. Full System Architecture

This diagram shows how a user interacts with the system, from the frontend all the way to external data sources and background jobs.

```mermaid
flowchart TB
    User((User))

    subgraph Frontend["Frontend (React / Vite on Vercel)"]
        SPA["Single Page App\nUI Dashboards"]
    end

    subgraph Backend["FastAPI Backend (GCP Cloud Run)"]
        API["API Routers"]
        Agents["Agent Swarms & Debates"]
        RAG["Knowledge Store (RAG)"]
        LLM_Client["LLM Client Engine\n(model gateway)"]
        Ledger["Decision-Outcome Ledger"]
        Background["Background Tasks\n(News loop, Cron, Grader)"]

        API --> Agents
        API --> Background
        Agents --> RAG
        Agents --> LLM_Client
        Agents --> Ledger
        Background --> Ledger
    end

    subgraph DataStores["Data Stores"]
        SQLite[("SQLite (ephemeral disk)\nAlerts, Users, CORAL, Ledger dev")]
        Supabase[("Supabase (pgvector)\nVector Embeddings + Ledger prod")]
    end

    subgraph External["External APIs & Sources"]
        NVIDIA("NVIDIA Build (LLMs)")
        OpenRouter("OpenRouter (LLMs)")
        Gemini("Google Gemini")
        YFinance("yFinance / APIs")
        HF("Hugging Face Hub")
    end

    User <--> SPA
    SPA <-->|HTTPS| API

    RAG <--> Supabase
    Ledger <--> Supabase
    API <--> SQLite

    LLM_Client --> NVIDIA
    LLM_Client --> OpenRouter
    LLM_Client --> Gemini

    Agents --> YFinance
    Background --> YFinance

    RAG -.->|Optional| HF
```

## 2. LLM Processing and Fallback System

TradeTalk uses a highly resilient LLM system (the Phase F model gateway in `backend/llm_client.py`). The default cascade is NVIDIA Build → (OpenRouter) → Gemini → rule-based fallback; `GEMINI_PRIMARY=1` routes everything through Gemini directly. Default model IDs are centralized in `backend/model_defaults.py`; verdict roles raise `insufficient_data` instead of using templates (truthful-data contract).

```mermaid
flowchart TD
    Request["Agent or User Request"]

    subgraph LLM_Engine ["LLM Client (model gateway)"]
        CheckPrimary{"Is GEMINI_PRIMARY\nenabled?"}

        NvidiaPath["Try NVIDIA Build API\n(PRO model, then FLASH model)"]
        OpenRouterPath["Try OpenRouter API\n(when LLM_HTTP_PROVIDER=openrouter)"]
        GeminiPath["Try Gemini API\n(flash-class default)"]

        CheckRateLimit{"Rate Limited\n(429)?"}
        RetryKeys["Round robin to\nnext API key"]

        Verdict{"Verdict role?"}
        Refuse["Raise InsufficientDataError\n(503 insufficient_data)"]
        Fallback["Rule-based JSON Templates\n(non-verdict roles only)"]
    end

    Response["Structured JSON / Text Response"]

    Request --> CheckPrimary
    CheckPrimary -- "No" --> NvidiaPath
    CheckPrimary -- "Yes" --> GeminiPath

    NvidiaPath --> CheckRateLimit
    OpenRouterPath --> CheckRateLimit
    CheckRateLimit -- "Yes" --> RetryKeys
    RetryKeys --> NvidiaPath

    NvidiaPath -- "No NVIDIA keys" --> OpenRouterPath
    NvidiaPath -- "Failure / Timeout" --> GeminiPath
    OpenRouterPath -- "Failure / Timeout" --> GeminiPath
    GeminiPath -- "Failure" --> Verdict
    Verdict -- "Yes" --> Refuse
    Verdict -- "No" --> Fallback

    NvidiaPath -- "Success" --> Response
    OpenRouterPath -- "Success" --> Response
    GeminiPath -- "Success" --> Response
    Fallback --> Response
```

## 3. Data Ingestion & Scheduled Pipelines

TradeTalk does not just answer questions; it actively reads the market in the background to build a memory. This diagram shows how data flows into the system asynchronously.

```mermaid
flowchart LR
    subgraph Triggers ["Triggers"]
        NewsLoop(("60s News Loop\n(Internal)"))
        CronJob(("00:05 UTC Cron\n(GitHub Actions)"))
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
        SSE(("Real-time SSE\nNotifications"))
    end

    NewsLoop --> RSS
    CronJob --> FRED
    CronJob --> YT
    CronJob --> Prices

    RSS --> Summarizer
    FRED --> Summarizer
    YT --> Summarizer
    Prices --> Summarizer

    Summarizer --> Embedder
    Embedder --> DB

    RSS --> SQL
    RSS --> SSE
```

## 4. Hugging Face Integrations

While not the primary database, Hugging Face serves as an optional layer for read-only snapshots, remote embeddings, and data lakes.

```mermaid
flowchart TD
    Backend["TradeTalk Backend"]

    subgraph HuggingFace ["Hugging Face Ecosystem"]
        Inference["Inference API\n(Embeddings)"]
        Datasets["Hub Datasets\n(JSON / Parquet)"]
        Spaces["HF Spaces\n(Optional Hosting)"]
    end

    Backend -->|"1. Remote Embeddings\n(If Chroma on Render, legacy)"| Inference
    Backend -->|"2. Read-only RAG\n(VECTOR_BACKEND=hf)"| Datasets
    Backend -->|"3. Data Lake Sync\n(Historical Prices)"| Datasets
    Backend -.->|4. Keep-alive ping| Spaces
```

## 5. Agent Swarm & Debate Architecture

TradeTalk simulates a Wall Street analyst team. A request goes to multiple parallel agents, each looking at different data, before a Moderator agent resolves their disagreements.

```mermaid
flowchart TB
    Input["User: Evaluate AAPL"]

    subgraph Parallel_Agents ["Parallel Specialist Agents"]
        Bull["Bull Agent\n(Growth & Upside)"]
        Bear["Bear Agent\n(Risk & Macro)"]
        Value["Value Agent\n(Fundamentals)"]
        Momentum["Momentum Agent\n(Price Action)"]
        Macro["Macro Agent\n(Credit & Rates)"]
    end

    subgraph Knowledge ["Retrieval Augmented Generation"]
        RAG[("Vector Memory\n(Past lessons, YouTube, Debates)")]
    end

    Input --> Bull
    Input --> Bear
    Input --> Value
    Input --> Momentum
    Input --> Macro

    RAG --> Bull
    RAG --> Bear
    RAG --> Value
    RAG --> Momentum
    RAG --> Macro

    Bull --> Moderator
    Bear --> Moderator
    Value --> Moderator
    Momentum --> Moderator
    Macro --> Moderator

    subgraph Synthesis ["Synthesis Layer"]
        Moderator{"Moderator Agent"}
        Verdict["Final Investment Verdict\n(Buy/Hold/Sell)"]
    end

    Moderator --> Verdict
    Verdict --> RAG
```

## 6. Decision Ledger & Outcome Grader Loop

This diagram illustrates how agent decisions are recorded, graded against future market reality, and fed back into the system to improve future performance.

```mermaid
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
```

## 7. SEPL Resource Registry & Tool Evolution

This diagram shows the Self-Evolving Prompts and Logic (SEPL) pipeline. The pipeline iterates over prompts and tools, perturbing them, evaluating them against offline fixtures, and committing improvements while an offline Kill Switch guards against regressions.

```mermaid
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
```

## 8. CORAL Hub & Named Agents

The CORAL Hub provides a central point for named system agents and infrastructure to persist heartbeat notes, share RAG-adjacent skills, and log meta-learning attempts.

```mermaid
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
```
