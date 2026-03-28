import React, { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE_URL, getToken, apiFetch } from './api'

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
  const bottomRef = useRef(null)
  const refreshTimer = useRef(null)

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
      try {
        const data = await apiFetch(`${API_BASE_URL}/chat/session`, {
          method: 'POST',
          body: JSON.stringify({}),
        })
        if (!cancelled) setSessionId(data.session_id)
      } catch (e) {
        if (!cancelled) setErr(e.message || 'Could not open chat session')
      } finally {
        if (!cancelled) setSessionLoading(false)
      }
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

  const sendMessage = useCallback(async () => {
    const text = input.trim()
    if (!text || !sessionId || busy) return
    setInput('')
    setErr('')
    setMessages((m) => [...m, { role: 'user', content: text }])
    setBusy(true)
    setStreaming('')

    const token = getToken()
    const headers = {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    }

    try {
      const res = await fetch(`${API_BASE_URL}/chat/message`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ session_id: sessionId, message: text, history: messages }),
      })
      if (!res.ok) {
        const msg = await res.text()
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
            if (j.type === 'error') setErr(j.message || 'Stream error')
          } catch {
            /* ignore partial */
          }
        }
      }
      setMessages((m) => [...m, { role: 'assistant', content: cleanText(assistant) || '(no response)' }])
      setStreaming('')
    } catch (e) {
      setErr(e.message || 'Request failed')
    } finally {
      setBusy(false)
    }
  }, [input, sessionId, busy])

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
    if (sessionId) {
      if (refreshTimer.current) clearTimeout(refreshTimer.current)
      refreshTimer.current = setTimeout(() => {
        apiFetch(`${API_BASE_URL}/chat/context/refresh`, {
          method: 'POST',
          body: JSON.stringify({ session_id: sessionId }),
        }).catch(() => {})
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
            <strong>{m.role === 'user' ? 'You' : 'Assistant'}:</strong> {m.content}
          </div>
        ))}
        {streaming && (
          <div style={{ whiteSpace: 'pre-wrap', color: '#e2e8f0', fontSize: 14 }}>
            <strong>Assistant:</strong> {streaming}
            <span className="cursor-blink">▍</span>
          </div>
        )}
        {busy && !streaming && (
          <div style={{ whiteSpace: 'pre-wrap', color: '#94a3b8', fontSize: 14, fontStyle: 'italic', display: 'flex', gap: '4px', alignItems: 'center' }}>
            <strong>Assistant:</strong> 
            <span style={{ animation: 'pulse 1.5s infinite' }}>typing...</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      {err && (
        <div style={{ color: '#f87171', fontSize: 13, marginBottom: 8 }}>{err}</div>
      )}
      <div style={{ display: 'flex', gap: 8 }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={sessionId ? 'Ask about markets, your portfolio, or strategies…' : 'Loading…'}
          disabled={!sessionId || busy}
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
          disabled={!sessionId || busy || !input.trim()}
          style={{
            alignSelf: 'flex-end',
            padding: '10px 18px',
            borderRadius: 10,
            border: 'none',
            background: 'linear-gradient(135deg, #7c3aed, #a78bfa)',
            color: '#fff',
            fontWeight: 700,
            cursor: busy ? 'wait' : 'pointer',
            opacity: !sessionId || busy ? 0.6 : 1,
          }}
        >
          {busy ? '…' : 'Send'}
        </button>
      </div>
    </div>
  )
}
