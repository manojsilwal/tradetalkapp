/**
 * AppAssistantPanel — persistent, app-level AI assistant sliding panel.
 *
 * Lives at the root of the app (rendered in App.jsx), always available on every page.
 * Reads page context from window.__tt_page_context__ and sends it with every message
 * so the LLM knows which page and ticker the user is currently on.
 *
 * Features:
 *  - Sliding drawer from right edge (360 px on desktop, full-width on mobile)
 *  - Icon rail: Expand, Chat, AI Suggest, History — matching the design mockup
 *  - Persistent session across route changes (same localStorage key as ChatUI)
 *  - Token streaming, quote cards, evidence contract chips
 *  - Action dispatch: assistant can navigate the app via ```action blocks
 *  - Quick action suggestions contextual to the current page
 */

import React, {
  useCallback,
  useEffect,
  useRef,
  useState,
  useLayoutEffect,
} from 'react'
import { useNavigate, useLocation, Link } from 'react-router-dom'
import { API_BASE_URL, getToken, apiFetch } from './api'

// ── Session storage key (shared with legacy ChatUI so session survives) ──────
const CHAT_SESSION_STORAGE_KEY = 'tradetalk_chat_session_id'

// ── Tool-use chips config ─────────────────────────────────────────────────────
const CHIP_CONFIG = {
  portfolio:   { label: 'Portfolio',   color: '#34d399', bg: 'rgba(16,185,129,0.12)',  border: 'rgba(52,211,153,0.3)'  },
  macro:       { label: 'Macro',       color: '#60a5fa', bg: 'rgba(59,130,246,0.12)',  border: 'rgba(96,165,250,0.3)'  },
  news:        { label: 'News',        color: '#fb923c', bg: 'rgba(249,115,22,0.12)',  border: 'rgba(251,146,60,0.3)'  },
  quote:       { label: 'Live Quote',  color: '#a78bfa', bg: 'rgba(139,92,246,0.12)',  border: 'rgba(167,139,250,0.3)' },
  rag:         { label: 'Knowledge',   color: '#f9a8d4', bg: 'rgba(236,72,153,0.12)',  border: 'rgba(249,168,212,0.3)' },
  risk:        { label: 'Risk',        color: '#fbbf24', bg: 'rgba(245,158,11,0.12)',  border: 'rgba(251,191,36,0.3)'  },
  backtest:    { label: 'Backtest',    color: '#38bdf8', bg: 'rgba(14,165,233,0.12)',  border: 'rgba(56,189,248,0.3)'  },
  market_data: { label: 'Market Data', color: '#94a3b8', bg: 'rgba(100,116,139,0.12)', border: 'rgba(148,163,184,0.3)' },
  web_search:  { label: 'Web',         color: '#e2e8f0', bg: 'rgba(226,232,240,0.08)', border: 'rgba(226,232,240,0.2)' },
}

// ── Per-page quick action suggestions ────────────────────────────────────────
const PAGE_QUICK_ACTIONS = {
  '/':                  ['What are the top movers today?', 'Give me a quick market summary', 'What is the current market regime?'],
  '/daily-brief':       ['Summarize today\'s key market themes', 'What events should I watch today?', 'Any macro catalysts today?'],
  '/macro':             ['Explain current capital flows', 'What sectors are rotating right now?', 'How does macro affect tech stocks?'],
  '/backtest':          ['What does a 50/200 MA crossover backtest look like?', 'Run a momentum backtest on SPY', 'What strategy works in bull markets?'],
  '/decision-terminal': ['Should I buy TSLA today?', 'Analyze the decision for entering AMZN', 'What is the 21-day outlook for SPY?'],
  '/observer':          ['What traces are running?', 'Show me recent RAG lookups', 'What tools did the last chat turn use?'],
  '/portfolio':         ['How is my paper portfolio performing?', 'What positions should I trim?', 'Add AAPL to my paper portfolio'],
  '/challenge':         ['Give me a trading knowledge quiz', 'What should I learn about options?', 'Explain the Greeks in simple terms'],
  '/learning':          ['What should I learn next?', 'Explain P/E ratio for a beginner', 'What is the Kelly Criterion?'],
}

// ── Text utilities ────────────────────────────────────────────────────────────
const cleanText = (text) =>
  text
    .replace(/【[^】]*】/g, '')
    .replace(/\[\{[^\]]*\}\]/g, '')
    .replace(/\[\d+†[^\]]*\]/g, '')

function linkifyContent(text) {
  if (!text) return null
  const re =
    /(https?:\/\/[^\s<]+[^<>\s.,;)]*)|(\/(backtest|decision-terminal|portfolio|macro|chat|observer|challenge|learning|academy|daily-brief|swarm-score|systemmap)(?:\?[^\s<]*)?)/gi
  const out = []
  let last = 0
  let mi = 0
  let m
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const chunk = m[0]
    if (chunk.startsWith('http')) {
      out.push(
        <a key={`lnk-${mi++}`} href={chunk} target="_blank" rel="noopener noreferrer"
           style={{ color: '#93c5fd', textDecoration: 'underline' }}>
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
  if (last < text.length) out.push(text.slice(last))
  return out.length ? out : text
}

/**
 * Parse and strip ```action { ... } ``` blocks from assistant text.
 * Returns { cleanedText, actions[] }
 */
function extractActions(text) {
  const actions = []
  const cleanedText = text.replace(/```action\s*([\s\S]*?)```/g, (_, json) => {
    try {
      const a = JSON.parse(json.trim())
      actions.push(a)
    } catch { /* ignore malformed */ }
    return ''
  }).trim()
  return { cleanedText, actions }
}

function ContextChips({ evidence, meta }) {
  const families = (evidence?.tool_families_used || []).map((f) => f.toLowerCase())
  const hasRag = meta?.rag_nonempty || families.includes('rag')
  const chips = []
  for (const family of families) {
    if (family === 'rag') continue
    const cfg = CHIP_CONFIG[family]
    if (cfg && !chips.some((c) => c.label === cfg.label)) chips.push(cfg)
  }
  if (hasRag && !chips.some((c) => c.label === 'Knowledge')) chips.push(CHIP_CONFIG.rag)
  if (chips.length === 0) return null
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
      {chips.map((chip) => (
        <span key={chip.label}
          style={{
            fontSize: 9, fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase',
            padding: '2px 7px', borderRadius: 20,
            border: `1px solid ${chip.border}`, background: chip.bg, color: chip.color,
          }}>
          {chip.label}
        </span>
      ))}
    </div>
  )
}

// ── Session helpers ───────────────────────────────────────────────────────────
function openSessionRequestBody(forceNew = false) {
  if (forceNew) return {}
  try {
    const rid = localStorage.getItem(CHAT_SESSION_STORAGE_KEY)
    if (rid && rid.length >= 8) return { resume_session_id: rid }
  } catch { /* private mode */ }
  return {}
}

function rememberChatSessionId(id) {
  if (!id) return
  try { localStorage.setItem(CHAT_SESSION_STORAGE_KEY, id) } catch { /* ignore */ }
}

// ── Tab icons (inline SVG so no import needed) ───────────────────────────────
function IconChat({ size = 18, active = false }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={active ? '#a78bfa' : 'currentColor'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
    </svg>
  )
}
function IconSparkles({ size = 18, active = false }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={active ? '#f9a8d4' : 'currentColor'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5L12 3z"/>
      <path d="M5 17l.7 2.1L7.8 20l-2.1.7L5 22.8l-.7-2.1L2.2 20l2.1-.7L5 17z"/>
      <path d="M19 3l.5 1.5 1.5.5-1.5.5L19 7l-.5-1.5L17 5l1.5-.5L19 3z"/>
    </svg>
  )
}
function IconHistory({ size = 18, active = false }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={active ? '#60a5fa' : 'currentColor'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="12 8 12 12 14 14"/>
      <path d="M3.05 11a9 9 0 1 1 .5 4m-.5 5v-5h5"/>
    </svg>
  )
}
function IconExpand({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 3 21 3 21 9"/>
      <polyline points="9 21 3 21 3 15"/>
      <line x1="21" y1="3" x2="14" y2="10"/>
      <line x1="3" y1="21" x2="10" y2="14"/>
    </svg>
  )
}
function IconClose({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18"/>
      <line x1="6" y1="6" x2="18" y2="18"/>
    </svg>
  )
}
function IconSend({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13"/>
      <polygon points="22 2 15 22 11 13 2 9 22 2"/>
    </svg>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function AppAssistantPanel({ prefetch = null }) {
  const navigate = useNavigate()
  const location = useLocation()

  // Panel open/close + tab state
  const [open, setOpen]     = useState(false)
  const [tab, setTab]       = useState('chat')    // 'chat' | 'suggest' | 'history'
  const [expanded, setExpanded] = useState(false) // wide mode

  // Chat state (mirrors ChatUI logic)
  const [sessionId, setSessionId]         = useState(null)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [input, setInput]                 = useState('')
  const [messages, setMessages]           = useState([])
  const [streaming, setStreaming]         = useState('')
  const [busy, setBusy]                   = useState(false)
  const [err, setErr]                     = useState('')
  const [quoteCards, setQuoteCards]       = useState([])
  const [evidenceContract, setEvidenceContract] = useState(null)
  const [lastMeta, setLastMeta]           = useState(null)
  const [bootstrap, setBootstrap]         = useState(prefetch?.boot ?? null)
  const [exportBusy, setExportBusy]       = useState(false)

  const bottomRef    = useRef(null)
  const inputRef     = useRef(null)
  const sessionIdRef = useRef(null)
  const refreshTimer = useRef(null)

  useEffect(() => { sessionIdRef.current = sessionId }, [sessionId])
  useEffect(() => {
    if (prefetch?.boot) setBootstrap(prefetch.boot)
  }, [prefetch])

  // Class toggles on document body for shifting main content when open/expanded
  useEffect(() => {
    if (open) {
      document.body.classList.add('assistant-open');
      if (expanded) {
        document.body.classList.add('assistant-expanded');
      } else {
        document.body.classList.remove('assistant-expanded');
      }
    } else {
      document.body.classList.remove('assistant-open');
      document.body.classList.remove('assistant-expanded');
    }
    return () => {
      document.body.classList.remove('assistant-open');
      document.body.classList.remove('assistant-expanded');
    };
  }, [open, expanded]);

  // Auto-scroll chat to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streaming])

  // Focus input when panel opens
  useLayoutEffect(() => {
    if (open && tab === 'chat') {
      setTimeout(() => inputRef.current?.focus(), 120)
    }
  }, [open, tab])

  // Bootstrap + session open on mount
  useEffect(() => {
    if (!bootstrap) {
      fetch(`${API_BASE_URL}/chat/bootstrap`, { credentials: 'omit' })
        .then((r) => r.json())
        .then(setBootstrap)
        .catch(() => setBootstrap({}))
    }
  }, [bootstrap])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const backoffs = [0, 1200, 2800]
      let lastErr = null
      for (let i = 0; i < backoffs.length; i++) {
        if (i > 0) await new Promise((r) => setTimeout(r, backoffs[i]))
        if (cancelled) return
        try {
          const data = await apiFetch(`${API_BASE_URL}/chat/session`, {
            method: 'POST',
            body: JSON.stringify(openSessionRequestBody(false)),
          })
          if (!cancelled) {
            setSessionId(data.session_id)
            rememberChatSessionId(data.session_id)
            setErr('')
          }
          lastErr = null
          break
        } catch (e) { lastErr = e }
      }
      if (!cancelled && lastErr) setErr(lastErr.message || 'Could not open chat session')
      if (!cancelled) setSessionLoading(false)
    })()
    return () => { cancelled = true }
  }, [])

  // ── Page context reader ───────────────────────────────────────────────────
  const getPageContext = useCallback(() => {
    try {
      const ctx = window.__tt_page_context__ || {}
      const pageName = ctx.page || location.pathname.replace('/', '') || 'dashboard'
      const ticker   = ctx.ticker   || null
      const extra    = ctx.extra    || null
      let note = `[User is on the ${pageName} page.`
      if (ticker) note += ` Currently viewing ticker: ${ticker}.`
      if (extra)  note += ` Context: ${extra}.`
      note += ']'
      return note
    } catch { return '' }
  }, [location.pathname])

  // ── Action dispatcher ─────────────────────────────────────────────────────
  const dispatchAction = useCallback((action) => {
    if (!action?.type) return
    try {
      if (action.type === 'navigate' && action.to) {
        let path = action.to
        if (action.ticker) path += `?ticker=${encodeURIComponent(action.ticker)}`
        navigate(path)
        setOpen(false)
        return
      }
      if (action.type === 'run-analysis') {
        window.dispatchEvent(new CustomEvent('tt:run-analysis', { detail: action }))
        return
      }
      if (action.type === 'open-panel') {
        window.dispatchEvent(new CustomEvent('tt:open-panel', { detail: action }))
        return
      }
    } catch (e) {
      console.warn('[AppAssistantPanel] action dispatch error', e)
    }
  }, [navigate])

  // ── Session helpers ───────────────────────────────────────────────────────
  const createChatSession = useCallback(async (forceNew = false) => {
    const data = await apiFetch(`${API_BASE_URL}/chat/session`, {
      method: 'POST',
      body: JSON.stringify(openSessionRequestBody(forceNew)),
    })
    if (data.session_id) rememberChatSessionId(data.session_id)
    return data.session_id
  }, [])

  // ── Send message ──────────────────────────────────────────────────────────
  const sendMessage = useCallback(async (overrideText) => {
    const text = (overrideText ?? input).trim()
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

    if (!overrideText) setInput('')
    setErr('')
    const pageCtx = getPageContext()
    const userMsgDisplay = text
    const userMsgSent = pageCtx ? `${pageCtx}\n\n${text}` : text

    setMessages((m) => [...m, { role: 'user', content: userMsgDisplay }])
    setTab('chat')
    setBusy(true)
    setStreaming('')
    setQuoteCards([])
    setEvidenceContract(null)
    setLastMeta(null)

    const token = getToken()
    const headers = {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    }
    const chatAbort = new AbortController()
    const chatTimer = setTimeout(() => chatAbort.abort(), 120000)

    const postMsg = (sid) =>
      fetch(`${API_BASE_URL}/chat/message`, {
        method: 'POST',
        headers,
        signal: chatAbort.signal,
        body: JSON.stringify({
          session_id: sid,
          message: userMsgSent,
          history: messages,
          page_context: getPageContext(),
        }),
      })

    try {
      let sid = activeSid
      let res = await postMsg(sid)

      if (res.status === 404 || res.status === 410) {
        try { localStorage.removeItem(CHAT_SESSION_STORAGE_KEY) } catch { /* ignore */ }
        const fresh = await createChatSession(true)
        setSessionId(fresh)
        sid = fresh
        res = await postMsg(sid)
      }

      if (!res.ok) {
        let msg = await res.text()
        try {
          const j = JSON.parse(msg)
          if (typeof j.detail === 'string') msg = j.detail
          else if (j.detail !== undefined) msg = JSON.stringify(j.detail)
        } catch { /* keep raw */ }
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
            if (j.type === 'meta'     && j.data) setLastMeta(j.data)
            if (j.type === 'token'    && j.text) {
              assistant += cleanText(j.text)
              setStreaming(assistant)
            }
            if (j.type === 'quote_card' && j.ticker && j.body) {
              setQuoteCards((qc) => [...qc, { ticker: j.ticker, body: j.body }])
            }
            if (j.type === 'error')             setErr(j.message || 'Stream error')
            if (j.type === 'evidence_contract' && j.data) setEvidenceContract(j.data)
          } catch { /* ignore partial */ }
        }
      }

      const rawAssistant = cleanText(assistant) || '(no response)'
      const { cleanedText, actions } = extractActions(rawAssistant)
      setMessages((m) => [...m, { role: 'assistant', content: cleanedText }])
      setStreaming('')
      actions.forEach(dispatchAction)
    } catch (e) {
      const msg = e.name === 'AbortError'
        ? 'Chat timed out after 120s — try a shorter question.'
        : (e.message || 'Request failed')
      setErr(msg)
    } finally {
      clearTimeout(chatTimer)
      setBusy(false)
    }
  }, [input, sessionId, busy, messages, createChatSession, getPageContext, dispatchAction])

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
    // Background session keep-alive
    const sid = sessionIdRef.current
    if (sid) {
      if (refreshTimer.current) clearTimeout(refreshTimer.current)
      refreshTimer.current = setTimeout(async () => {
        const tk = getToken()
        const hdrs = { 'Content-Type': 'application/json', ...(tk ? { Authorization: `Bearer ${tk}` } : {}) }
        try {
          const res = await fetch(`${API_BASE_URL}/chat/context/refresh`, {
            method: 'POST', headers: hdrs,
            body: JSON.stringify({ session_id: sessionIdRef.current }),
          })
          if (res.status === 404 || res.status === 410) {
            try { localStorage.removeItem(CHAT_SESSION_STORAGE_KEY) } catch { /* ignore */ }
            const data = await apiFetch(`${API_BASE_URL}/chat/session`, {
              method: 'POST', body: JSON.stringify({}),
            })
            setSessionId(data.session_id)
            rememberChatSessionId(data.session_id)
          }
        } catch { /* background — ignore */ }
      }, 800)
    }
  }

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
        method: 'POST', headers,
        body: JSON.stringify({ session_id: sid }),
      })
      if (!res.ok) {
        let msg = await res.text()
        try {
          const j = JSON.parse(msg)
          if (typeof j.detail === 'string') msg = j.detail
        } catch { /* raw */ }
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

  // ── Stale data banner ─────────────────────────────────────────────────────
  const l1AgeSec = bootstrap?.l1_updated_at
    ? Math.max(0, Date.now() / 1000 - bootstrap.l1_updated_at)
    : null
  const isStale = l1AgeSec != null && l1AgeSec > 1200

  // ── Quick suggestions for current page ───────────────────────────────────
  const pageSuggestions = PAGE_QUICK_ACTIONS[location.pathname] || PAGE_QUICK_ACTIONS['/']

  // ── History (last 5 user messages) ───────────────────────────────────────
  const historyMessages = messages.filter((m) => m.role === 'user').slice(-8).reverse()

  // ── Panel width ───────────────────────────────────────────────────────────
  const panelWidth = expanded ? 'min(720px, 95vw)' : '360px'

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <>
      {/* ── Floating toggle button (always visible, right edge) ─────────── */}
      <button
        id="assistant-panel-trigger"
        aria-label={open ? 'Close assistant' : 'Open assistant'}
        onClick={() => setOpen((v) => !v)}
        style={{
          position: 'fixed',
          right: open ? `calc(${panelWidth} + 12px)` : '12px',
          bottom: '28px',
          zIndex: 10001,
          width: 48,
          height: 48,
          borderRadius: '50%',
          border: 'none',
          background: open
            ? 'rgba(30,41,59,0.95)'
            : 'linear-gradient(135deg, #8b5cf6, #a78bfa)',
          boxShadow: open
            ? '0 4px 20px rgba(0,0,0,0.4)'
            : '0 6px 24px rgba(139,92,246,0.5)',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#fff',
          transition: 'right 0.35s cubic-bezier(0.4, 0, 0.2, 1), background 0.2s',
        }}
      >
        {open ? <IconClose size={18} /> : <IconChat size={20} active />}
        {/* Unread indicator */}
        {!open && messages.length > 0 && (
          <span style={{
            position: 'absolute', top: 4, right: 4, width: 10, height: 10,
            background: '#a78bfa', borderRadius: '50%',
            border: '2px solid #0f111a',
          }} />
        )}
      </button>

      {/* ── Backdrop (mobile) ─────────────────────────────────────────────── */}
      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{
            display: 'none', // shown via media query in CSS
            position: 'fixed', inset: 0, zIndex: 9999,
            background: 'rgba(0,0,0,0.5)',
          }}
          className="assistant-backdrop"
          aria-hidden
        />
      )}

      {/* ── Sliding panel ─────────────────────────────────────────────────── */}
      <div
        id="app-assistant-panel"
        role="complementary"
        aria-label="TradeTalk AI Assistant"
        className={`app-assistant-panel ${open ? 'open' : ''}`}
        style={{ '--panel-width': panelWidth }}
      >
        {/* Panel header */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 16px 12px',
          borderBottom: '1px solid rgba(255,255,255,0.06)',
          background: 'rgba(15,23,42,0.6)',
          backdropFilter: 'blur(16px)',
          flexShrink: 0,
        }}>
          {/* Logo + title */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{
              width: 30, height: 30, borderRadius: 8,
              background: 'linear-gradient(135deg, rgba(139,92,246,0.6), rgba(167,139,250,0.3))',
              border: '1px solid rgba(167,139,250,0.3)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <IconSparkles size={16} active />
            </div>
            <div>
              <div style={{ fontWeight: 700, fontSize: '0.9rem', color: '#e2e8f0' }}>
                TradeTalk Assistant
              </div>
              <div style={{ fontSize: '0.7rem', color: '#64748b' }}>
                {sessionLoading ? 'Initializing…' : sessionId ? 'Session active' : 'Ready'}
              </div>
            </div>
          </div>

          {/* Tab icons + expand */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            {[
              { id: 'chat',    Icon: IconChat,     label: 'Chat' },
              { id: 'suggest', Icon: IconSparkles, label: 'Suggestions' },
              { id: 'history', Icon: IconHistory,  label: 'History' },
            ].map(({ id, Icon, label }) => (
              <button
                key={id}
                aria-label={label}
                title={label}
                onClick={() => setTab(id)}
                style={{
                  background: tab === id ? 'rgba(167,139,250,0.15)' : 'transparent',
                  border: tab === id ? '1px solid rgba(167,139,250,0.3)' : '1px solid transparent',
                  borderRadius: 8, padding: '6px 7px',
                  cursor: 'pointer', color: tab === id ? '#a78bfa' : '#64748b',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  transition: 'all 0.15s',
                }}
              >
                <Icon size={16} active={tab === id} />
              </button>
            ))}
            <button
              aria-label={expanded ? 'Collapse' : 'Expand'}
              title={expanded ? 'Collapse' : 'Expand panel'}
              onClick={() => setExpanded((v) => !v)}
              style={{
                background: 'transparent', border: '1px solid transparent',
                borderRadius: 8, padding: '6px 7px',
                cursor: 'pointer', color: '#64748b',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
            >
              <IconExpand size={14} />
            </button>
            <button
              aria-label="Close assistant"
              onClick={() => setOpen(false)}
              style={{
                background: 'transparent', border: '1px solid transparent',
                borderRadius: 8, padding: '6px 7px',
                cursor: 'pointer', color: '#64748b',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}
            >
              <IconClose size={14} />
            </button>
          </div>
        </div>

        {/* Page context ribbon */}
        {open && (
          <div style={{
            padding: '6px 16px',
            background: 'rgba(59,130,246,0.06)',
            borderBottom: '1px solid rgba(59,130,246,0.1)',
            fontSize: '0.72rem', color: '#60a5fa',
            display: 'flex', alignItems: 'center', gap: 6,
            flexShrink: 0,
          }}>
            <span style={{ opacity: 0.7 }}>📍</span>
            <span>
              {location.pathname === '/' ? 'Dashboard' : location.pathname.replace('/', '').replace('-', ' ')}
              {window.__tt_page_context__?.ticker ? ` · ${window.__tt_page_context__.ticker}` : ''}
            </span>
            {isStale && (
              <span style={{ marginLeft: 'auto', color: '#fbbf24', fontSize: '0.68rem' }}>
                ⚠ Market data {Math.round(l1AgeSec / 60)}m old
              </span>
            )}
          </div>
        )}

        {/* ── TAB: CHAT ──────────────────────────────────────────────────── */}
        {tab === 'chat' && (
          <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
            {/* Messages area */}
            <div style={{
              flex: 1, overflowY: 'auto', padding: '14px 14px 0',
              display: 'flex', flexDirection: 'column', gap: 0,
            }}>
              {messages.length === 0 && !streaming && (
                <div style={{ textAlign: 'center', padding: '24px 16px' }}>
                  <div style={{
                    width: 48, height: 48, borderRadius: 12, margin: '0 auto 12px',
                    background: 'linear-gradient(135deg, rgba(139,92,246,0.3), rgba(167,139,250,0.1))',
                    border: '1px solid rgba(167,139,250,0.2)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}>
                    <IconSparkles size={22} active />
                  </div>
                  <div style={{ color: '#e2e8f0', fontWeight: 600, fontSize: '0.9rem', marginBottom: 6 }}>
                    Your AI Super-Assistant
                  </div>
                  <div style={{ color: '#64748b', fontSize: '0.78rem', lineHeight: 1.5, marginBottom: 16 }}>
                    Ask about markets, get analysis, navigate the app, or run strategies — all from here.
                  </div>
                  {/* Inline quick starts */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {pageSuggestions.slice(0, 3).map((s) => (
                      <button key={s} onClick={() => sendMessage(s)} style={{
                        background: 'rgba(30,41,59,0.6)', border: '1px solid rgba(255,255,255,0.07)',
                        borderRadius: 8, padding: '8px 12px', cursor: 'pointer',
                        color: '#94a3b8', fontSize: '0.78rem', textAlign: 'left',
                        transition: 'all 0.15s',
                      }}
                        onMouseEnter={(e) => { e.currentTarget.style.color = '#e2e8f0'; e.currentTarget.style.borderColor = 'rgba(167,139,250,0.3)' }}
                        onMouseLeave={(e) => { e.currentTarget.style.color = '#94a3b8'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.07)' }}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {messages.map((m, i) => (
                <div key={i} style={{
                  marginBottom: 10, display: 'flex',
                  justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
                }}>
                  <div style={{
                    maxWidth: '88%', padding: '10px 13px',
                    borderRadius: m.role === 'user' ? '14px 14px 2px 14px' : '14px 14px 14px 2px',
                    fontSize: '0.83rem', lineHeight: 1.5, whiteSpace: 'pre-wrap',
                    color: '#e2e8f0',
                    background: m.role === 'user'
                      ? 'linear-gradient(180deg, rgba(71,85,105,0.7), rgba(30,41,59,0.6))'
                      : 'linear-gradient(180deg, rgba(30,41,59,0.8), rgba(15,23,42,0.75))',
                    border: m.role === 'user'
                      ? '1px solid rgba(148,163,184,0.2)'
                      : '1px solid rgba(148,163,184,0.1)',
                  }}>
                    {linkifyContent(m.content)}
                  </div>
                </div>
              ))}

              {quoteCards.map((q, i) => (
                <div key={`qc-${q.ticker}-${i}`} style={{
                  marginBottom: 10, padding: 10, borderRadius: 10,
                  border: '1px solid rgba(16,185,129,0.35)',
                  background: 'rgba(16,185,129,0.07)',
                  fontSize: '0.78rem', lineHeight: 1.4, color: '#e2e8f0', whiteSpace: 'pre-wrap',
                }} data-testid="quote-card">
                  <div style={{ fontSize: 10, fontWeight: 700, color: '#34d399', marginBottom: 5, letterSpacing: '0.04em' }}>
                    LIVE QUOTE · {q.ticker}
                  </div>
                  {q.body}
                </div>
              ))}

              {streaming && (
                <div style={{ marginBottom: 10, display: 'flex', justifyContent: 'flex-start' }}>
                  <div style={{
                    maxWidth: '88%', padding: '10px 13px', borderRadius: '14px 14px 14px 2px',
                    fontSize: '0.83rem', lineHeight: 1.5, whiteSpace: 'pre-wrap', color: '#e2e8f0',
                    background: 'linear-gradient(180deg, rgba(30,41,59,0.8), rgba(15,23,42,0.75))',
                    border: '1px solid rgba(148,163,184,0.1)',
                  }}>
                    {linkifyContent(streaming)}
                    <span className="cursor-blink">▍</span>
                  </div>
                </div>
              )}

              {busy && !streaming && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8, color: '#94a3b8',
                  fontSize: '0.8rem', padding: '4px 0 8px',
                }}>
                  <span style={{ color: '#c4b5fd', fontWeight: 600 }}>Assistant:</span>
                  <span>Thinking</span>
                  <span className="chat-typing-indicator" aria-hidden>
                    <span /><span /><span />
                  </span>
                </div>
              )}

              {evidenceContract && (
                <>
                  <ContextChips evidence={evidenceContract} meta={lastMeta} />
                  <details data-testid="evidence-contract" style={{
                    marginBottom: 10, padding: '8px 10px', borderRadius: 8,
                    border: '1px solid rgba(148,163,184,0.2)',
                    background: 'rgba(15,23,42,0.5)', fontSize: '0.72rem', color: '#cbd5e1',
                  }}>
                    <summary style={{ cursor: 'pointer', fontWeight: 600, color: '#94a3b8' }}>
                      Sources &amp; confidence
                    </summary>
                    <pre style={{ margin: '8px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 10, color: '#e2e8f0' }}>
                      {JSON.stringify(evidenceContract, null, 2)}
                    </pre>
                  </details>
                </>
              )}

              {err && (
                <div style={{ color: '#f87171', fontSize: '0.78rem', marginBottom: 8, padding: '0 2px' }}>
                  {err}
                </div>
              )}

              <div ref={bottomRef} />
            </div>

            {/* Input area */}
            <div style={{
              padding: '10px 12px 14px',
              borderTop: '1px solid rgba(255,255,255,0.06)',
              background: 'rgba(15,23,42,0.5)',
              flexShrink: 0,
            }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                <textarea
                  ref={inputRef}
                  data-testid="assistant-panel-input"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={onKeyDown}
                  placeholder={
                    sessionLoading && !sessionId
                      ? 'Preparing session…'
                      : 'Ask anything about markets…'
                  }
                  disabled={busy || (sessionLoading && !sessionId)}
                  rows={2}
                  style={{
                    flex: 1, resize: 'none', borderRadius: 10,
                    border: '1px solid rgba(148,163,184,0.18)',
                    background: 'rgba(15,23,42,0.8)',
                    color: '#e2e8f0', padding: '10px 12px',
                    fontSize: '0.83rem', lineHeight: 1.4,
                    outline: 'none', fontFamily: 'inherit',
                  }}
                />
                <button
                  type="button"
                  onClick={() => sendMessage()}
                  disabled={busy || (sessionLoading && !sessionId) || !input.trim()}
                  style={{
                    alignSelf: 'flex-end', padding: '11px 13px', borderRadius: 10,
                    border: 'none',
                    background: busy || !input.trim()
                      ? 'rgba(139,92,246,0.3)'
                      : 'linear-gradient(135deg, #8b5cf6, #a78bfa)',
                    boxShadow: input.trim() && !busy ? '0 4px 16px rgba(139,92,246,0.4)' : 'none',
                    color: '#fff', cursor: busy ? 'wait' : 'pointer',
                    opacity: busy || (sessionLoading && !sessionId) ? 0.6 : 1,
                    transition: 'all 0.2s',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >
                  {busy ? '…' : <IconSend size={16} />}
                </button>
              </div>

              {/* Export memo + session info */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 6 }}>
                <span style={{ fontSize: '0.68rem', color: '#475569' }}>
                  Enter to send · Shift+Enter for new line
                </span>
                {evidenceContract && (
                  <button
                    type="button"
                    onClick={exportEvidenceMemo}
                    disabled={busy || exportBusy}
                    style={{
                      background: 'none', border: 'none', cursor: 'pointer',
                      color: '#64748b', fontSize: '0.68rem', padding: 0,
                    }}
                    title="Export evidence memo as Markdown"
                  >
                    {exportBusy ? '…' : '⬇ Export memo'}
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {/* ── TAB: SUGGEST ───────────────────────────────────────────────── */}
        {tab === 'suggest' && (
          <div style={{ flex: 1, overflowY: 'auto', padding: '16px 14px' }}>
            <div style={{ fontSize: '0.72rem', color: '#64748b', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 12 }}>
              Suggestions for this page
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {pageSuggestions.map((s) => (
                <button key={s} onClick={() => { sendMessage(s) }}
                  style={{
                    background: 'rgba(30,41,59,0.6)', border: '1px solid rgba(255,255,255,0.07)',
                    borderRadius: 10, padding: '12px 14px', cursor: 'pointer',
                    color: '#94a3b8', fontSize: '0.82rem', textAlign: 'left', lineHeight: 1.4,
                    transition: 'all 0.15s',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = '#e2e8f0'; e.currentTarget.style.background = 'rgba(139,92,246,0.12)'; e.currentTarget.style.borderColor = 'rgba(167,139,250,0.3)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = '#94a3b8'; e.currentTarget.style.background = 'rgba(30,41,59,0.6)'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.07)' }}
                >
                  {s}
                </button>
              ))}
            </div>

            <div style={{ fontSize: '0.72rem', color: '#64748b', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', margin: '20px 0 12px' }}>
              App capabilities
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {[
                { label: '🧭 Navigate', desc: 'Say "take me to backtest" or "open the macro page"' },
                { label: '📊 Live quotes', desc: 'Ask "What is AAPL trading at?"' },
                { label: '📰 News', desc: 'Ask "Any news on NVDA today?"' },
                { label: '⚡ Run backtest', desc: 'Ask "Run a momentum backtest on SPY"' },
                { label: '🔍 RAG Knowledge', desc: 'Searches the app\'s market knowledge base' },
                { label: '💼 Portfolio', desc: 'Ask "How is my paper portfolio doing?"' },
              ].map((cap) => (
                <div key={cap.label} style={{
                  padding: '8px 12px', borderRadius: 8,
                  background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(255,255,255,0.05)',
                }}>
                  <div style={{ fontWeight: 600, fontSize: '0.8rem', color: '#e2e8f0', marginBottom: 2 }}>{cap.label}</div>
                  <div style={{ fontSize: '0.72rem', color: '#64748b' }}>{cap.desc}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── TAB: HISTORY ───────────────────────────────────────────────── */}
        {tab === 'history' && (
          <div style={{ flex: 1, overflowY: 'auto', padding: '16px 14px' }}>
            <div style={{ fontSize: '0.72rem', color: '#64748b', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 12 }}>
              Recent questions
            </div>
            {historyMessages.length === 0 ? (
              <div style={{ color: '#475569', fontSize: '0.82rem', textAlign: 'center', padding: '24px 0' }}>
                No messages yet in this session
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {historyMessages.map((m, i) => (
                  <button key={i} onClick={() => { setTab('chat'); sendMessage(m.content) }}
                    style={{
                      background: 'rgba(30,41,59,0.5)', border: '1px solid rgba(255,255,255,0.06)',
                      borderRadius: 8, padding: '10px 12px', cursor: 'pointer',
                      color: '#94a3b8', fontSize: '0.8rem', textAlign: 'left',
                      transition: 'all 0.15s', lineHeight: 1.4,
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = '#e2e8f0'; e.currentTarget.style.borderColor = 'rgba(167,139,250,0.3)' }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = '#94a3b8'; e.currentTarget.style.borderColor = 'rgba(255,255,255,0.06)' }}
                    title="Ask again"
                  >
                    🔄 {m.content.length > 80 ? m.content.slice(0, 80) + '…' : m.content}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </>
  )
}
