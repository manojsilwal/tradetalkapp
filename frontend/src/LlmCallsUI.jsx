import React, { useState, useEffect } from 'react'
import { Cpu, Loader2, Search, Filter, Clock, Coins, Database, Activity, RefreshCw } from 'lucide-react'
import { API_BASE_URL, apiFetch } from './api'

export default function LlmCallsUI() {
  const [calls, setCalls] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [modelFilter, setModelFilter] = useState('All')
  const [expandedCallId, setExpandedCallId] = useState(null)

  const fetchCalls = async () => {
    setLoading(true)
    setError(null)
    try {
      // Endpoint added in the debug router
      const data = await apiFetch(`${API_BASE_URL}/llm/calls?limit=100`)
      setCalls(data || [])
    } catch (e) {
      setError(e.message || 'Failed to load LLM call history')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchCalls()
  }, [])

  // Calculate stats
  const totalCalls = calls.length
  const totalCost = calls.reduce((acc, curr) => acc + (curr.cost || 0), 0)
  const avgLatency = totalCalls > 0 
    ? calls.reduce((acc, curr) => acc + (curr.time_taken || 0), 0) / totalCalls 
    : 0

  // Filter models list
  const uniqueModels = ['All', ...new Set(calls.map(c => c.llm_used).filter(Boolean))]

  // Filtered calls
  const filteredCalls = calls.filter(c => {
    const matchesSearch = (c.query_brief || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
                          (c.llm_used || '').toLowerCase().includes(searchTerm.toLowerCase())
    const matchesModel = modelFilter === 'All' || c.llm_used === modelFilter
    return matchesSearch && matchesModel
  })

  const getLatencyColor = (s) => {
    if (s < 1.0) return '#10b981' // Green
    if (s < 3.0) return '#fbbf24' // Yellow
    return '#ef4444' // Red
  }

  const getModelBadgeStyle = (model) => {
    const ml = (model || '').toLowerCase()
    if (ml.includes('gemini')) {
      return { background: 'rgba(56,189,248,0.12)', color: '#38bdf8', border: '1px solid rgba(56,189,248,0.2)' }
    } else if (ml.includes('deepseek')) {
      return { background: 'rgba(167,139,250,0.12)', color: '#a78bfa', border: '1px solid rgba(167,139,250,0.2)' }
    } else if (ml.includes('kimi') || ml.includes('moonshot')) {
      return { background: 'rgba(16,185,129,0.12)', color: '#10b981', border: '1px solid rgba(16,185,129,0.2)' }
    } else {
      return { background: 'rgba(244,63,94,0.12)', color: '#f43f5e', border: '1px solid rgba(244,63,94,0.2)' }
    }
  }

  return (
    <div className="consumer-container fade-in" style={{ padding: '16px 8px 48px' }}>
      {/* Header */}
      <div className="header-section" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <div className="title-group">
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: 0 }}>
            <Cpu size={24} color="#a78bfa" />
            LLM API Calls History
          </h2>
          <p style={{ margin: '4px 0 0', color: '#94a3b8', fontSize: '0.88rem' }}>
            Real-time auditing, latency analysis, and cost tracking of every LLM API query.
          </p>
        </div>
        <button 
          onClick={fetchCalls} 
          disabled={loading}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', borderRadius: 8, background: 'rgba(255,255,255,0.05)',
            border: '1px solid rgba(255,255,255,0.08)', color: '#fff', cursor: 'pointer'
          }}
        >
          {loading ? <Loader2 className="spinner" size={16} /> : <RefreshCw size={16} />}
          Refresh
        </button>
      </div>

      {error && (
        <div style={{ padding: '12px 18px', borderRadius: '10px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.2)', color: '#fca5a5', marginBottom: '20px', fontSize: '0.85rem' }}>
          {error}
        </div>
      )}

      {/* KPI Stats Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px', marginBottom: '24px' }}>
        <div style={statCardStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <Activity size={18} color="#a78bfa" />
            <span style={statLabelStyle}>Total API Calls</span>
          </div>
          <div style={statValueStyle}>{totalCalls}</div>
          <div style={statSubStyle}>recorded in current session</div>
        </div>

        <div style={statCardStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <Coins size={18} color="#10b981" />
            <span style={statLabelStyle}>Estimated Spend</span>
          </div>
          <div style={{ ...statValueStyle, color: '#10b981' }}>${totalCost.toFixed(5)}</div>
          <div style={statSubStyle}>based on model input/output rates</div>
        </div>

        <div style={statCardStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
            <Clock size={18} color="#fbbf24" />
            <span style={statLabelStyle}>Average Latency</span>
          </div>
          <div style={{ ...statValueStyle, color: '#fbbf24' }}>{avgLatency.toFixed(2)}s</div>
          <div style={statSubStyle}>avg round-trip response time</div>
        </div>
      </div>

      {/* Filter and Search Bar */}
      <div className="glass-panel" style={{ padding: '16px 20px', display: 'flex', flexWrap: 'wrap', gap: '16px', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: '260px' }}>
          <Search size={18} color="#64748b" />
          <input 
            type="text" 
            placeholder="Search queries or models..." 
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
            style={{
              background: 'transparent', border: 'none', color: '#fff', outline: 'none',
              width: '100%', fontSize: '0.88rem'
            }}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Filter size={16} color="#64748b" />
          <span style={{ fontSize: '0.82rem', color: '#94a3b8' }}>Filter Model:</span>
          <select 
            value={modelFilter}
            onChange={e => setModelFilter(e.target.value)}
            style={{
              background: 'rgba(15,23,42,0.6)', color: '#fff', border: '1px solid rgba(255,255,255,0.08)',
              padding: '6px 12px', borderRadius: '8px', outline: 'none', fontSize: '0.82rem', cursor: 'pointer'
            }}
          >
            {uniqueModels.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Main Table */}
      <div className="glass-panel" style={{ overflow: 'hidden', padding: 0 }}>
        {loading && calls.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '60px', color: '#94a3b8' }}>
            <Loader2 className="spinner" size={28} style={{ margin: '0 auto 12px' }} />
            <p>Fetching API call history...</p>
          </div>
        ) : filteredCalls.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '60px', color: '#64748b' }}>
            <Database size={40} style={{ marginBottom: '12px', opacity: 0.5 }} />
            <p>No matching LLM calls found.</p>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem', minWidth: '900px' }}>
              <thead>
                <tr style={{ background: 'rgba(15,23,42,0.4)', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                  <th style={thStyle}>Timestamp</th>
                  <th style={thStyle}>LLM Model Used</th>
                  <th style={thStyle}>Latency (s)</th>
                  <th style={thStyle}>Est. Cost (USD)</th>
                  <th style={thStyle}>Query Brief</th>
                </tr>
              </thead>
              <tbody>
                {filteredCalls.map((call) => {
                  const isExpanded = expandedCallId === call.id
                  return (
                    <React.Fragment key={call.id}>
                      <tr 
                        onClick={() => setExpandedCallId(isExpanded ? null : call.id)}
                        style={{ 
                          borderBottom: '1px solid rgba(255,255,255,0.04)',
                          cursor: 'pointer',
                          background: isExpanded ? 'rgba(255,255,255,0.02)' : 'transparent',
                          transition: 'background 0.2s'
                        }}
                      >
                        <td style={tdStyle}>
                          {new Date(call.timestamp * 1000).toLocaleTimeString()}
                          <span style={{ display: 'block', fontSize: '0.7rem', color: '#64748b', marginTop: '2px' }}>
                            {new Date(call.timestamp * 1000).toLocaleDateString()}
                          </span>
                        </td>
                        <td style={tdStyle}>
                          <span style={{ 
                            padding: '3px 8px', borderRadius: '6px', fontSize: '0.75rem', fontWeight: 600,
                            display: 'inline-block', ...getModelBadgeStyle(call.llm_used) 
                          }}>
                            {call.llm_used}
                          </span>
                        </td>
                        <td style={{ ...tdStyle, color: getLatencyColor(call.time_taken), fontWeight: '700' }}>
                          {call.time_taken.toFixed(2)}s
                          {/* Mini visual indicator */}
                          <div style={{ width: '40px', height: '3px', background: 'rgba(255,255,255,0.05)', borderRadius: '2px', marginTop: '4px' }}>
                            <div style={{ 
                              width: `${Math.min(100, (call.time_taken / 5) * 100)}%`, 
                              height: '100%', 
                              background: getLatencyColor(call.time_taken), 
                              borderRadius: '2px' 
                            }} />
                          </div>
                        </td>
                        <td style={{ ...tdStyle, color: '#10b981', fontWeight: '600' }}>
                          ${call.cost.toFixed(5)}
                        </td>
                        <td style={{ ...tdStyle, color: '#e2e8f0', maxWidth: '400px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {call.query_brief}
                          <span style={{ display: 'block', fontSize: '0.7rem', color: '#64748b', marginTop: '2px' }}>
                            Click to expand query brief
                          </span>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr style={{ background: 'rgba(255,255,255,0.01)' }}>
                          <td colSpan="5" style={{ padding: '16px 24px', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                              <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                                Raw Query / Prompt Context
                              </div>
                              <pre style={{ 
                                margin: 0, padding: '14px', borderRadius: '8px', 
                                background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(255,255,255,0.06)',
                                color: '#e2e8f0', fontFamily: 'monospace', fontSize: '0.78rem', 
                                whiteSpace: 'pre-wrap', lineHeight: '1.5' 
                              }}>
                                {call.query_brief}
                              </pre>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// Styles
const thStyle = { textAlign: 'left', padding: '12px 16px', fontWeight: 600, color: '#94a3b8' }
const tdStyle = { padding: '14px 16px', verticalAlign: 'middle', borderBottom: '1px solid rgba(255,255,255,0.02)' }

const statCardStyle = {
  padding: '16px 20px',
  borderRadius: '12px',
  background: 'rgba(255,255,255,0.02)',
  border: '1px solid rgba(255,255,255,0.05)',
  backdropFilter: 'blur(10px)',
}

const statLabelStyle = {
  fontSize: '0.72rem',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  color: '#94a3b8',
}

const statValueStyle = {
  fontSize: '1.6rem',
  fontWeight: 800,
  color: '#fff',
  margin: '4px 0 2px',
}

const statSubStyle = {
  fontSize: '0.7rem',
  color: '#64748b',
}
