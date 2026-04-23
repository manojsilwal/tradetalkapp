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
        if (chartRef.current && chart) {
            mermaid.render(`mermaid-${Math.random().toString(36).substring(7)}`, chart).then((result) => {
                chartRef.current.innerHTML = result.svg;
            }).catch((err) => {
                console.error("Mermaid error", err);
            });
        }
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
        SPA["Single Page App\\nUI Dashboards"]
    end

    subgraph Backend["FastAPI Backend (Render)"]
        API["API Routers"]
        Agents["Agent Swarms & Debates"]
        RAG["Knowledge Store (RAG)"]
        LLM_Client["LLM Client Engine"]
        Background["Background Tasks\\n(News loop, Cron)"]

        API --> Agents
        API --> Background
        Agents --> RAG
        Agents --> LLM_Client
    end

    subgraph DataStores["Data Stores"]
        SQLite[("SQLite (Disk)\\nAlerts, Users")]
        Supabase[("Supabase (pgvector)\\nVector Embeddings")]
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
\`\`\`

## 2. LLM Processing and Fallback System

TradeTalk uses a highly resilient LLM system. By default, it connects to OpenRouter, but it will gracefully degrade to Gemini or hardcoded rule-based responses if APIs fail or rate limits are hit.

\`\`\`mermaid
flowchart TD
    Request["Agent or User Request"]

    subgraph LLM_Engine ["LLM Client"]
        CheckPrimary{"Is GEMINI_PRIMARY\\nenabled?"}

        OpenRouterPath["Try OpenRouter API\\n(Qwen / Llama etc)"]
        GeminiPath["Try Gemini API\\n(Gemini 3.1 Pro/Flash)"]

        CheckRateLimit{"Rate Limited\\n(429)?"}
        RetryKeys["Round Robin to\\nnext API Key"]

        Fallback["Rule-based JSON Templates\\n(Offline mode)"]
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
        NewsLoop(("60s News Loop\\n(Internal)"))
        CronJob(("00:05 UTC Cron\\n(GitHub Actions)"))
    end

    subgraph Connectors ["Data Connectors"]
        RSS["Google News RSS"]
        FRED["FRED Macro Data"]
        YT["YouTube Transcripts"]
        Prices["yFinance Movers"]
    end

    subgraph Processing ["Processing"]
        Summarizer["LLM Summarizer\\n(Extract Insights)"]
        Embedder["Embedding Model\\n(Create Vectors)"]
    end

    subgraph Storage ["Knowledge Store"]
        DB[("Supabase pgvector\\n(Vector Memory)")]
        SQL[("SQLite\\n(Macro Alerts)")]
        SSE(("Real-time SSE\\nNotifications"))
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
\`\`\`

## 4. Hugging Face Integrations

While not the primary database, Hugging Face serves as an optional layer for read-only snapshots, remote embeddings, and data lakes.

\`\`\`mermaid
flowchart TD
    Backend["TradeTalk Backend"]

    subgraph HuggingFace ["Hugging Face Ecosystem"]
        Inference["Inference API\\n(Embeddings)"]
        Datasets["Hub Datasets\\n(JSON / Parquet)"]
        Spaces["HF Spaces\\n(Optional Hosting)"]
    end

    Backend -- "1. Remote Embeddings\\n(If Chroma on Render)" --> Inference
    Backend -- "2. Read-only RAG\\n(VECTOR_BACKEND=hf)" --> Datasets
    Backend -- "3. Data Lake Sync\\n(Historical Prices)" --> Datasets
    Backend -.->|4. Keep-alive ping| Spaces
\`\`\`

## 5. Agent Swarm & Debate Architecture

TradeTalk simulates a Wall Street analyst team. A request goes to multiple parallel agents, each looking at different data, before a Moderator agent resolves their disagreements.

\`\`\`mermaid
flowchart TB
    Input["User: Evaluate AAPL"]

    subgraph Parallel_Agents ["Parallel Specialist Agents"]
        Bull["Bull Agent\\n(Growth & Upside)"]
        Bear["Bear Agent\\n(Risk & Macro)"]
        Value["Value Agent\\n(Fundamentals)"]
        Momentum["Momentum Agent\\n(Price Action)"]
        Macro["Macro Agent\\n(Credit & Rates)"]
    end

    subgraph Knowledge ["Retrieval Augmented Generation"]
        RAG[("Vector Memory\\n(Past lessons, YouTube, Debates)")]
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
        Verdict["Final Investment Verdict\\n(Buy/Hold/Sell)"]
    end

    Moderator --> Verdict
    Verdict --> RAG
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
