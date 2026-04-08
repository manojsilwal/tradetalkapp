import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { API_BASE_URL, getToken, apiFetch } from './api'

/** Turn URLs and internal /routes into clickable links (assistant + user messages). */
function linkifyContent(text) {
  if (!text) return null
  const re =
    /(https?:\/\/[^\s<]+[^<>\s.,;)]*)|(\/(?:debate|backtest|decision-terminal|portfolio|macro|gold|chat|observer|challenge|learning|academy)(?:\?[^\s<]*)?)/gi
  const out = []
  let last = 0
  let mi = 0
  let m
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      out.push(text.slice(last, m.index))
    }
    const chunk = m[0]
    if (chunk.startsWith('http')) {
      out.push(
        <a
          key={`lnk-${mi++}`}
          href={chunk}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: '#93c5fd', textDecoration: 'underline' }}
        >
          {chunk}
        </a>
      )
    } else {
      out.push(
        <Link key={`lnk-${mi++}`} to={chunk} style={{ color: '#93c5fd', textDecoration: 'underline' }}>
          {chunk}
        </Link>
      )
    }
    last = re.lastIndex
  }
  if (last < text.length) {
    out.push(text.slice(last))
  }
  return out.length ? out : text
}

/**
 * Strip model-emitted citation artifacts like 【{"id":"1",...}】 and 【1†source】
 * These are internal OpenRouter tokens that should never reach the UI.
 */
const cleanText = (text) => text
  .replace(/【[^】]*】/g, '')           // 【...】 style citations (incl. 【get_market_news†L1】)
  .replace(/\[\{[^\]]*\}\]/g, '')      // [{"id":...}] style citations
  .replace(/\[\d+†[^\]]*\]/g, '')      // [1†source] style citations

/**
 * TradeTalk Assistant — session bootstrap, parallel prefetch from App, SSE token stream.
 */
export default function ChatUI({ prefetch = null }) {
  const [bootstrap, setBootstrap] = useState(prefetch?.boot ?? null)
  const [userCtx, setUserCtx] = useState(prefetch?.user ?? null)
  const [sessionId, setSessionId] = useState(null)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([])
  const [streaming, setStreaming] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [quoteCards, setQuoteCards] = useState([])
  const [evidenceContract, setEvidenceContract] = useState(null)
  const [exportBusy, setExportBusy] = useState(false)
  const bottomRef = useRef(null)
  const refreshTimer = useRef(null)
  const sessionIdRef = useRef(null)

  useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  useEffect(() => {
    if (prefetch?.boot) {
      setBootstrap(prefetch.boot)
    }
    if (prefetch?.user) {
      setUserCtx(prefetch.user)
    }
  }, [prefetch])

  useEffect(() => {
    if (!bootstrap) {
      fetch(`${API_BASE_URL}/chat/bootstrap`, { credentials: 'omit' })
        .then((r) => r.json())
        .then(setBootstrap)
        .catch(() => setBootstrap({}))
    }
    const token = getToken()
    const headers = token ? { Authorization: `Bearer ${token}` } : {}
    if (!userCtx) {
      fetch(`${API_BASE_URL}/chat/user-context`, { headers })
        .then((r) => r.json())
        .then(setUserCtx)
        .catch(() => setUserCtx({ authenticated: false }))
    }
  }, [bootstrap, userCtx])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const backoffMs = [0, 1200, 2800]
      let lastErr = null
      for (let i = 0; i < backoffMs.length; i++) {
        if (i > 0) await new Promise((r) => setTimeout(r, backoffMs[i]))
        if (cancelled) return
        try {
          const data = await apiFetch(`${API_BASE_URL}/chat/session`, {
            method: 'POST',
            body: JSON.stringify({}),
          })
          if (!cancelled) {
            setSessionId(data.session_id)
            setErr('')
          }
          lastErr = null
          break
        } catch (e) {
          lastErr = e
        }
      }
      if (!cancelled && lastErr) {
        setErr(lastErr.message || 'Could not open chat session')
      }
      if (!cancelled) setSessionLoading(false)
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const l1AgeSec = bootstrap?.l1_updated_at
    ? Math.max(0, Date.now() / 1000 - bootstrap.l1_updated_at)
    : null
  const staleBanner =
    l1AgeSec != null && l1AgeSec > 1200 ? (
      <div
        style={{
          fontSize: 11,
          color: '#fbbf24',
          padding: '6px 10px',
          background: 'rgba(251,191,36,0.08)',
          borderRadius: 8,
          marginBottom: 8,
        }}
      >
        Market snapshot is {Math.round(l1AgeSec / 60)}m old — refreshing in background.
      </div>
    ) : null

  const createChatSession = useCallback(async () => {
    const data = await apiFetch(`${API_BASE_URL}/chat/session`, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    return data.session_id
  }, [])

  const exportEvidenceMemo = useCallback(async () => {
    const sid = sessionIdRef.current
    if (!sid || busy) return
    setExportBusy(true)
    setErr('')
    try {
      const token = getToken()
      const headers = {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      }
      const res = await fetch(`${API_BASE_URL}/chat/evidence-export`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ session_id: sid }),
      })
      if (!res.ok) {
        let msg = await res.text()
        try {
          const j = JSON.parse(msg)
          if (typeof j.detail === 'string') msg = j.detail
        } catch {
          /* keep raw */
        }
        throw new Error(msg || `HTTP ${res.status}`)
      }
      const j = await res.json()
      const blob = new Blob([j.markdown || ''], { type: 'text/markdown;charset=utf-8' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `tradetalk-evidence-${String(sid).slice(0, 8)}.md`
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) {
      setErr(e.message || 'Export failed')
    } finally {
      setExportBusy(false)
    }
  }, [busy])

  const sendMessage = useCallback(async () => {
    const text = input.trim()
    if (!text || busy) return

    let activeSid = sessionId
    if (!activeSid) {
      setErr('Opening chat session…')
      try {
        activeSid = await createChatSession()
        setSessionId(activeSid)
        setErr('')
      } catch (e) {
        setErr(e.message || 'Could not open chat session')
        return
      }
    }

    setInput('')
    setErr('')
    setMessages((m) => [...m, { role: 'user', content: text }])
    setBusy(true)
    setStreaming('')
    setQuoteCards([])
    setEvidenceContract(null)

    const token = getToken()
    const headers = {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    }
    const chatAbort = new AbortController()
    const chatTimer = setTimeout(() => chatAbort.abort(), 120000)

    const historyPayload = messages

    const postMessage = (sid) =>
      fetch(`${API_BASE_URL}/chat/message`, {
        method: 'POST',
        headers,
        signal: chatAbort.signal,
        body: JSON.stringify({
          session_id: sid,
          message: text,
          history: historyPayload,
        }),
      })

    try {
      let sid = activeSid
      let res = await postMessage(sid)

      if (res.status === 404 || res.status === 410) {
        const fresh = await createChatSession()
        setSessionId(fresh)
        sid = fresh
        res = await postMessage(sid)
      }

      if (!res.ok) {
        let msg = await res.text()
        try {
          const j = JSON.parse(msg)
          if (typeof j.detail === 'string') msg = j.detail
          else if (j.detail !== undefined) msg = JSON.stringify(j.detail)
        } catch {
          /* keep raw */
        }
        throw new Error(msg || `HTTP ${res.status}`)
      }
      const reader = res.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      let assistant = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const parts = buf.split('\n')
        buf = parts.pop() || ''
        for (const line of parts) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6).trim()
          if (payload === '[DONE]') continue
          try {
            const j = JSON.parse(payload)
            if (j.type === 'token' && j.text) {
              assistant += cleanText(j.text)
              setStreaming(assistant)
            }
            if (j.type === 'quote_card' && j.ticker && j.body) {
              setQuoteCards((qc) => [...qc, { ticker: j.ticker, body: j.body }])
            }
            if (j.type === 'error') setErr(j.message || 'Stream error')
            if (j.type === 'evidence_contract' && j.data) setEvidenceContract(j.data)
          } catch {
            /* ignore partial */
          }
        }
      }
      setMessages((m) => [...m, { role: 'assistant', content: cleanText(assistant) || '(no response)' }])
      setStreaming('')
    } catch (e) {
      const msg = e.name === 'AbortError' ? 'Chat timed out after 120s — try a shorter question.' : (e.message || 'Request failed')
      setErr(msg)
    } finally {
      clearTimeout(chatTimer)
      setBusy(false)
    }
  }, [input, sessionId, busy, messages, createChatSession])

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
    const sid = sessionIdRef.current
    if (sid) {
      if (refreshTimer.current) clearTimeout(refreshTimer.current)
      refreshTimer.current = setTimeout(async () => {
        const token = getToken()
        const headers = {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        }
        try {
          let res = await fetch(`${API_BASE_URL}/chat/context/refresh`, {
            method: 'POST',
            headers,
            body: JSON.stringify({ session_id: sessionIdRef.current }),
          })
          if (res.status === 404 || res.status === 410) {
            const data = await apiFetch(`${API_BASE_URL}/chat/session`, {
              method: 'POST',
              body: JSON.stringify({}),
            })
            setSessionId(data.session_id)
          }
        } catch {
          /* ignore — background refresh */
        }
      }, 800)
    }
  }

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: '24px 16px' }}>
      <h1 style={{ fontSize: '1.35rem', fontWeight: 700, marginBottom: 8, color: '#e2e8f0' }}>
        TradeTalk Assistant
      </h1>
      <p style={{ fontSize: 13, color: '#94a3b8', marginBottom: 16 }}>
        Context is prefetched on app load; session opens with market + portfolio snapshot. Responses stream token-by-token.
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
        {[
          { to: '/debate', label: 'Debate' },
          { to: '/backtest', label: 'Backtest' },
          { to: '/decision-terminal', label: 'Decision' },
          { to: '/portfolio', label: 'Portfolio' },
          { to: '/macro', label: 'Macro' },
        ].map((a) => (
          <Link
            key={a.to}
            to={a.to}
            style={{
              fontSize: 12,
              padding: '6px 12px',
              borderRadius: 8,
              border: '1px solid rgba(148,163,184,0.25)',
              color: '#cbd5e1',
              textDecoration: 'none',
              background: 'rgba(30,41,59,0.5)',
            }}
          >
            {a.label}
          </Link>
        ))}
      </div>
      {staleBanner}
      {sessionLoading && (
        <div style={{ color: '#94a3b8', fontSize: 13 }}>Preparing session…</div>
      )}
      {userCtx?.authenticated && (
        <div style={{ fontSize: 12, color: '#64748b', marginBottom: 8 }}>
          Signed in — portfolio context {userCtx.context?.open_positions?.length ? 'loaded' : 'empty'}.
        </div>
      )}
      <div
        style={{
          border: '1px solid rgba(148,163,184,0.2)',
          borderRadius: 12,
          padding: 16,
          minHeight: 320,
          background: 'rgba(15,23,42,0.5)',
          marginBottom: 12,
          overflowY: 'auto',
          maxHeight: '55vh',
        }}
      >
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              marginBottom: 12,
              whiteSpace: 'pre-wrap',
              color: m.role === 'user' ? '#a5b4fc' : '#e2e8f0',
              fontSize: 14,
              lineHeight: 1.5,
            }}
          >
            <strong>{m.role === 'user' ? 'You' : 'Assistant'}:</strong> {linkifyContent(m.content)}
          </div>
        ))}
        {quoteCards.map((q, i) => (
          <div
            key={`qc-${q.ticker}-${i}`}
            style={{
              marginBottom: 12,
              padding: 12,
              borderRadius: 10,
              border: '1px solid rgba(16,185,129,0.35)',
              background: 'rgba(16,185,129,0.08)',
              fontSize: 13,
              lineHeight: 1.45,
              color: '#e2e8f0',
              whiteSpace: 'pre-wrap',
            }}
            data-testid="quote-card"
          >
            <div style={{ fontSize: 11, fontWeight: 700, color: '#34d399', marginBottom: 6, letterSpacing: '0.04em' }}>
              LIVE QUOTE · {q.ticker}
            </div>
            {q.body}
          </div>
        ))}
        {streaming && (
          <div style={{ whiteSpace: 'pre-wrap', color: '#e2e8f0', fontSize: 14 }}>
            <strong>Assistant:</strong> {linkifyContent(streaming)}
            <span className="cursor-blink">▍</span>
          </div>
        )}
        {busy && !streaming && (
          <div
            style={{
              whiteSpace: 'pre-wrap',
              color: '#94a3b8',
              fontSize: 14,
              display: 'flex',
              gap: 8,
              alignItems: 'center',
              flexWrap: 'wrap',
            }}
          >
            <strong style={{ color: '#c4b5fd' }}>Assistant:</strong>
            <span style={{ color: '#94a3b8' }}>Thinking</span>
            <span className="chat-typing-indicator" aria-hidden>
              <span />
              <span />
              <span />
            </span>
          </div>
        )}
        {evidenceContract && (
          <details
            data-testid="evidence-contract"
            style={{
              marginBottom: 12,
              padding: '10px 12px',
              borderRadius: 10,
              border: '1px solid rgba(148,163,184,0.25)',
              background: 'rgba(15,23,42,0.65)',
              fontSize: 12,
              color: '#cbd5e1',
            }}
          >
            <summary style={{ cursor: 'pointer', fontWeight: 600, color: '#94a3b8' }}>
              Sources & confidence
            </summary>
            <pre
              style={{
                margin: '10px 0 0',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                fontSize: 11,
                lineHeight: 1.45,
                color: '#e2e8f0',
              }}
            >
              {JSON.stringify(evidenceContract, null, 2)}
            </pre>
          </details>
        )}
        <div ref={bottomRef} />
      </div>
      {err && (
        <div style={{ color: '#f87171', fontSize: 13, marginBottom: 8 }}>{err}</div>
      )}
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <button
          type="button"
          onClick={() => exportEvidenceMemo()}
          disabled={busy || exportBusy || sessionLoading || !sessionId || !evidenceContract}
          title="Download Markdown memo for the last completed turn (sources + evidence contract)"
          style={{
            padding: '10px 14px',
            borderRadius: 10,
            border: '1px solid rgba(148,163,184,0.35)',
            background: 'rgba(30,41,59,0.75)',
            color: '#cbd5e1',
            fontSize: 13,
            fontWeight: 600,
            cursor: exportBusy ? 'wait' : 'pointer',
            opacity: busy || !evidenceContract ? 0.5 : 1,
          }}
        >
          {exportBusy ? '…' : 'Export memo'}
        </button>
        <textarea
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={
            sessionLoading && !sessionId
              ? 'Preparing session…'
              : 'Ask about markets, your portfolio, or strategies…'
          }
          disabled={busy || (sessionLoading && !sessionId)}
          rows={2}
          style={{
            flex: 1,
            resize: 'vertical',
            borderRadius: 10,
            border: '1px solid rgba(148,163,184,0.25)',
            background: 'rgba(30,41,59,0.6)',
            color: '#e2e8f0',
            padding: '10px 12px',
            fontSize: 14,
          }}
        />
        <button
          type="button"
          onClick={sendMessage}
          disabled={busy || (sessionLoading && !sessionId) || !input.trim()}
          style={{
            alignSelf: 'flex-end',
            padding: '10px 18px',
            borderRadius: 10,
            border: 'none',
            background: 'linear-gradient(135deg, #7c3aed, #a78bfa)',
            color: '#fff',
            fontWeight: 700,
            cursor: busy ? 'wait' : 'pointer',
            opacity: busy || (sessionLoading && !sessionId) ? 0.6 : 1,
          }}
        >
          {busy ? '…' : 'Send'}
        </button>
      </div>
    </div>
  )
}
