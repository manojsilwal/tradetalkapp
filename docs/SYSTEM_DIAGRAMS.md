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

    subgraph Backend["FastAPI Backend (Render)"]
        API["API Routers"]
        Agents["Agent Swarms & Debates"]
        RAG["Knowledge Store (RAG)"]
        LLM_Client["LLM Client Engine"]
        Background["Background Tasks\n(News loop, Cron)"]

        API --> Agents
        API --> Background
        Agents --> RAG
        Agents --> LLM_Client
    end

    subgraph DataStores["Data Stores"]
        SQLite[("SQLite (Disk)\nAlerts, Users")]
        Supabase[("Supabase (pgvector)\nVector Embeddings")]
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

    RAG -.->|Optional| HF
```

## 2. LLM Processing and Fallback System

TradeTalk uses a highly resilient LLM system. By default, it connects to OpenRouter, but it will gracefully degrade to Gemini or hardcoded rule-based responses if APIs fail or rate limits are hit.

```mermaid
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

    Backend -->|"1. Remote Embeddings\n(If Chroma on Render)"| Inference
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
