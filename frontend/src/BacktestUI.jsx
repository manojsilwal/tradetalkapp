import React, { useState } from 'react';
import {
  FlaskConical, Lightbulb, TrendingUp, TrendingDown, Minus,
  Loader2, AlertTriangle, ChevronDown, ChevronUp, Play, CheckCircle2
} from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import { API_BASE_URL } from './api';

const EXAMPLE_STRATEGIES = [
  "Buy S&P 500 companies with revenue growing faster than 15% per year and hold for 3 years",
  "Buy stocks trading above their 200-day moving average each year and rebalance annually",
  "Buy companies with P/E ratio below 20 and positive free cash flow, rebalance yearly",
  "Buy high dividend yield stocks above 3% with debt-to-equity below 1.0",
];

const ACTION_COLORS = {
  BUY: { bg: 'rgba(16,185,129,0.12)', color: '#10b981', border: 'rgba(16,185,129,0.3)' },
  SELL: { bg: 'rgba(59,130,246,0.12)', color: '#60a5fa', border: 'rgba(59,130,246,0.3)' },
  HOLD_CASH: { bg: 'rgba(100,116,139,0.12)', color: '#94a3b8', border: 'rgba(100,116,139,0.2)' },
  REBALANCE: { bg: 'rgba(245,158,11,0.12)', color: '#f59e0b', border: 'rgba(245,158,11,0.3)' },
};

function StatCard({ label, value, benchmark, unit = '%', higherIsBetter = true }) {
  const isNumber = typeof benchmark === 'number';
  const outperforms = isNumber
    ? (higherIsBetter ? value > benchmark : value < benchmark)
    : null;
  const color = outperforms === null ? '#94a3b8' : outperforms ? '#10b981' : '#ef4444';

  return (
    <div style={{
      background: 'rgba(15,23,42,0.6)', borderRadius: 10, padding: '16px 18px',
      border: '1px solid rgba(255,255,255,0.06)',
    }}>
      <div style={{ color: '#475569', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ color, fontSize: '1.6rem', fontWeight: 800, letterSpacing: '-0.02em' }}>
        {typeof value === 'number' ? `${value > 0 && higherIsBetter ? '+' : ''}${value.toFixed(1)}${unit}` : value}
      </div>
      {isNumber && (
        <div style={{ color: '#334155', fontSize: '0.72rem', marginTop: 4 }}>
          vs SPY {benchmark > 0 ? '+' : ''}{benchmark.toFixed(1)}{unit}
          {outperforms !== null && (
            <span style={{ color, marginLeft: 4 }}>
              {outperforms ? '▲ outperforms' : '▼ underperforms'}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function FilterChip({ filter }) {
  const metricLabel = filter.metric.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  return (
    <span style={{
      background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.25)',
      borderRadius: 6, padding: '4px 10px', fontSize: '0.75rem', color: '#818cf8',
    }}>
      {metricLabel} {filter.op} {filter.value}
    </span>
  );
}

function ActionBadge({ action }) {
  const style = ACTION_COLORS[action] || ACTION_COLORS.BUY;
  return (
    <span style={{
      background: style.bg, color: style.color, border: `1px solid ${style.border}`,
      borderRadius: 5, padding: '2px 8px', fontSize: '0.68rem', fontWeight: 700,
      letterSpacing: '0.04em', whiteSpace: 'nowrap',
    }}>
      {action}
    </span>
  );
}

// Group actions by year
function groupByYear(actions) {
  const groups = {};
  for (const action of actions) {
    const year = action.date?.slice(0, 4) || 'Unknown';
    if (!groups[year]) groups[year] = [];
    groups[year].push(action);
  }
  return groups;
}

function ActionTimeline({ actions }) {
  const [openYears, setOpenYears] = useState({});
  const groups = groupByYear(actions);
  const years = Object.keys(groups).sort().reverse();

  const toggle = (y) => setOpenYears(prev => ({ ...prev, [y]: !prev[y] }));

  return (
    <div>
      <div style={{ color: '#64748b', fontSize: '0.75rem', marginBottom: 12 }}>
        {actions.length} total trade decisions
      </div>
      {years.map(year => (
        <div key={year} style={{ marginBottom: 8 }}>
          <button
            onClick={() => toggle(year)}
            style={{
              display: 'flex', alignItems: 'center', gap: 8, width: '100%',
              background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.05)',
              borderRadius: 8, padding: '9px 14px', cursor: 'pointer', color: '#cbd5e1',
              fontSize: '0.82rem', fontWeight: 600,
            }}
          >
            <span style={{ flex: 1, textAlign: 'left' }}>{year}</span>
            <span style={{ color: '#475569', fontSize: '0.72rem' }}>{groups[year].length} actions</span>
            {openYears[year] ? <ChevronUp size={13} color="#475569" /> : <ChevronDown size={13} color="#475569" />}
          </button>

          {openYears[year] && (
            <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2 }}>
              {groups[year].map((action, i) => (
                <div key={i} style={{
                  display: 'grid',
                  gridTemplateColumns: '80px 70px 60px 60px 1fr auto',
                  gap: 10, alignItems: 'center',
                  background: 'rgba(15,23,42,0.5)',
                  borderRadius: 6, padding: '7px 14px',
                  fontSize: '0.78rem',
                }}>
                  <span style={{ color: '#475569' }}>{action.date}</span>
                  <ActionBadge action={action.action} />
                  <span style={{ color: '#94a3b8', fontWeight: 600 }}>{action.ticker}</span>
                  <span style={{ color: '#64748b' }}>${action.price?.toFixed(2)}</span>
                  <span style={{ color: '#334155', fontSize: '0.7rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {action.reason}
                  </span>
                  {action.action === 'SELL' && (
                    <span style={{ color: action.return_pct >= 0 ? '#10b981' : '#ef4444', fontWeight: 700, whiteSpace: 'nowrap' }}>
                      {action.return_pct >= 0 ? '+' : ''}{action.return_pct?.toFixed(1)}%
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// Recharts custom tooltip
function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: 'rgba(15,23,42,0.95)', border: '1px solid rgba(255,255,255,0.1)',
      borderRadius: 8, padding: '10px 14px', fontSize: '0.78rem',
    }}>
      <div style={{ color: '#64748b', marginBottom: 4 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, marginBottom: 2 }}>
          {p.name}: ${p.value?.toLocaleString(undefined, { maximumFractionDigits: 0 })}
        </div>
      ))}
    </div>
  );
}

export default function BacktestUI() {
  const [strategyText, setStrategyText] = useState('');
  const [startDate, setStartDate]       = useState('2019-01-01');
  const [endDate, setEndDate]           = useState('2024-01-01');
  const [loading, setLoading]           = useState(false);
  const [result, setResult]             = useState(null);
  const [error, setError]               = useState('');
  const [showExamples, setShowExamples] = useState(false);

  const runBacktest = async () => {
    if (!strategyText.trim()) return;
    setLoading(true);
    setResult(null);
    setError('');
    try {
      const res = await fetch(`${API_BASE_URL}/backtest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy: strategyText, start_date: startDate, end_date: endDate }),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Server error ${res.status}: ${text}`);
      }
      setResult(await res.json());
    } catch (e) {
      setError(e.message || 'Backtest failed.');
    } finally {
      setLoading(false);
    }
  };

  // Merge portfolio + benchmark into chart data
  const chartData = result ? (() => {
    const portMap = Object.fromEntries(result.portfolio_value_series.map(p => [p.date, p.value]));
    const benchMap = Object.fromEntries(result.benchmark_value_series.map(p => [p.date, p.value]));
    const allDates = [...new Set([
      ...result.portfolio_value_series.map(p => p.date),
      ...result.benchmark_value_series.map(p => p.date),
    ])].sort();
    return allDates.map(date => ({
      date: date.slice(0, 7),
      Portfolio: portMap[date],
      'S&P 500 (SPY)': benchMap[date],
    })).filter(d => d.Portfolio || d['S&P 500 (SPY)']);
  })() : [];

  const inputStyle = {
    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8, padding: '9px 12px', color: '#e2e8f0', fontSize: '0.85rem',
    outline: 'none', width: '100%', boxSizing: 'border-box',
  };

  return (
    <div style={{ padding: '24px 0', maxWidth: 900, margin: '0 auto' }}>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
        input[type="date"]::-webkit-calendar-picker-indicator { filter: invert(0.4); cursor: pointer; }
      `}</style>

      {/* ── Header ──────────────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14, marginBottom: 22,
      }}>
        <div style={{
          width: 44, height: 44, borderRadius: 10,
          background: 'rgba(99,102,241,0.12)', border: '1px solid rgba(99,102,241,0.2)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <FlaskConical size={22} color="#818cf8" />
        </div>
        <div>
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.25rem', fontWeight: 700 }}>Strategy Lab</h2>
          <p style={{ margin: 0, color: '#475569', fontSize: '0.78rem' }}>
            Describe your investing strategy in plain English and test it against history
          </p>
        </div>
      </div>

      {/* ── Input Panel ──────────────────────────────────────────────────────── */}
      <div style={{
        background: 'rgba(15,23,42,0.7)', borderRadius: 14, padding: '22px 24px',
        border: '1px solid rgba(255,255,255,0.07)', marginBottom: 20,
      }}>
        <label style={{ color: '#94a3b8', fontSize: '0.78rem', display: 'block', marginBottom: 8, fontWeight: 600 }}>
          STRATEGY DESCRIPTION
        </label>
        <textarea
          value={strategyText}
          onChange={e => setStrategyText(e.target.value)}
          placeholder="e.g. Buy S&P 500 companies with revenue growing faster than 15% and hold for 3 years..."
          rows={4}
          style={{
            ...inputStyle, resize: 'vertical', lineHeight: 1.6, minHeight: 90,
            fontFamily: 'inherit',
          }}
        />

        {/* Date range + run button */}
        <div style={{ display: 'flex', gap: 12, marginTop: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div style={{ flex: '1 1 130px' }}>
            <label style={{ color: '#475569', fontSize: '0.72rem', display: 'block', marginBottom: 4 }}>FROM</label>
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} style={inputStyle} />
          </div>
          <div style={{ flex: '1 1 130px' }}>
            <label style={{ color: '#475569', fontSize: '0.72rem', display: 'block', marginBottom: 4 }}>TO</label>
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} style={inputStyle} />
          </div>
          <button
            onClick={runBacktest}
            disabled={loading || !strategyText.trim()}
            style={{
              flex: '2 1 200px', background: loading ? 'rgba(99,102,241,0.25)' : 'linear-gradient(135deg, #6366f1, #8b5cf6)',
              border: 'none', borderRadius: 8, padding: '10px 20px',
              color: '#fff', fontWeight: 700, fontSize: '0.88rem',
              cursor: loading || !strategyText.trim() ? 'not-allowed' : 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
              boxShadow: loading ? 'none' : '0 4px 14px rgba(99,102,241,0.35)',
            }}
          >
            {loading
              ? <><Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} /> Running Backtest...</>
              : <><Play size={15} /> Run Backtest</>
            }
          </button>
        </div>

        {/* Example strategies */}
        <div style={{ marginTop: 14 }}>
          <button
            onClick={() => setShowExamples(v => !v)}
            style={{
              background: 'none', border: 'none', color: '#475569', fontSize: '0.75rem',
              cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: 0,
            }}
          >
            {showExamples ? <ChevronUp size={13} /> : <ChevronDown size={13} />} Example strategies
          </button>
          {showExamples && (
            <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
              {EXAMPLE_STRATEGIES.map((s, i) => (
                <button key={i} onClick={() => { setStrategyText(s); setShowExamples(false); }} style={{
                  background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
                  borderRadius: 7, padding: '8px 12px', color: '#64748b',
                  fontSize: '0.78rem', textAlign: 'left', cursor: 'pointer',
                  transition: 'all 0.15s',
                }}>
                  "{s}"
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div style={{
          background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
          borderRadius: 10, padding: '12px 16px', marginBottom: 20,
          display: 'flex', alignItems: 'flex-start', gap: 10, color: '#f87171', fontSize: '0.83rem',
        }}>
          <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
          {error}
        </div>
      )}

      {/* ── Results ──────────────────────────────────────────────────────────── */}
      {result && !loading && (
        <div style={{ animation: 'fadeIn 0.5s ease-out both' }}>
          {/* Parsed strategy card */}
          <div style={{
            background: 'rgba(15,23,42,0.6)', borderRadius: 12, padding: '18px 22px',
            border: '1px solid rgba(99,102,241,0.15)', marginBottom: 20,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
              <CheckCircle2 size={15} color="#10b981" />
              <span style={{ color: '#94a3b8', fontSize: '0.78rem', fontWeight: 600 }}>PARSED STRATEGY</span>
            </div>
            <div style={{ color: '#e2e8f0', fontWeight: 700, fontSize: '1rem', marginBottom: 10 }}>
              {result.strategy.name}
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
              {result.strategy.filters.map((f, i) => <FilterChip key={i} filter={f} />)}
            </div>
            <div style={{ color: '#475569', fontSize: '0.75rem' }}>
              {result.strategy.start_date} → {result.strategy.end_date} ·
              {' '}{result.strategy.holding_period_months}mo holding · {result.strategy.rebalance_months}mo rebalance ·
              {' '}{result.strategy.universe.length} stocks screened
            </div>
          </div>

          {/* Performance dashboard */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 20 }}>
            {/* Stats */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <StatCard label="CAGR" value={result.cagr} benchmark={result.benchmark_cagr} />
              <StatCard label="Sharpe Ratio" value={result.sharpe_ratio} benchmark={1.0} unit="" />
              <StatCard label="Max Drawdown" value={result.max_drawdown} benchmark={-20} unit="%" higherIsBetter={false} />
              <StatCard label="Win Rate" value={result.win_rate} benchmark={null} unit="%" />
            </div>

            {/* Chart */}
            <div style={{
              background: 'rgba(15,23,42,0.5)', borderRadius: 12, padding: '16px 12px',
              border: '1px solid rgba(255,255,255,0.06)',
            }}>
              <div style={{ color: '#64748b', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10, paddingLeft: 4 }}>
                Portfolio vs S&P 500 · Starting $10,000
              </div>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={chartData} margin={{ top: 0, right: 8, left: -10, bottom: 0 }}>
                  <defs>
                    <linearGradient id="portGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="spyGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.15} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                  <XAxis dataKey="date" tick={{ fill: '#334155', fontSize: 10 }} tickLine={false} interval="preserveStartEnd" />
                  <YAxis tick={{ fill: '#334155', fontSize: 10 }} tickLine={false} tickFormatter={v => `$${(v/1000).toFixed(0)}k`} />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend wrapperStyle={{ fontSize: '0.72rem', color: '#475569' }} />
                  <Area type="monotone" dataKey="Portfolio" stroke="#8b5cf6" fill="url(#portGrad)" strokeWidth={2} dot={false} />
                  <Area type="monotone" dataKey="S&P 500 (SPY)" stroke="#3b82f6" fill="url(#spyGrad)" strokeWidth={1.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Best/Worst periods */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 20 }}>
            {[
              { label: 'Best Period', value: result.best_period, color: '#10b981', Icon: TrendingUp },
              { label: 'Worst Period', value: result.worst_period, color: '#ef4444', Icon: TrendingDown },
            ].map(({ label, value, color, Icon }) => (
              <div key={label} style={{
                background: 'rgba(15,23,42,0.5)', borderRadius: 9, padding: '12px 14px',
                border: '1px solid rgba(255,255,255,0.05)', display: 'flex', alignItems: 'center', gap: 10,
              }}>
                <Icon size={16} color={color} />
                <div>
                  <div style={{ color: '#475569', fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
                  <div style={{ color, fontWeight: 600, fontSize: '0.85rem', marginTop: 1 }}>{value}</div>
                </div>
              </div>
            ))}
          </div>

          {/* Action Timeline */}
          <div style={{
            background: 'rgba(15,23,42,0.6)', borderRadius: 12, padding: '18px 22px',
            border: '1px solid rgba(255,255,255,0.06)', marginBottom: 20,
          }}>
            <h3 style={{ margin: '0 0 14px', color: '#94a3b8', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
              Action Timeline
            </h3>
            <ActionTimeline actions={result.actions} />
          </div>

          {/* Gemini Explanation */}
          <div style={{
            background: 'rgba(15,23,42,0.6)', borderRadius: 12, padding: '20px 24px',
            border: '1px solid rgba(245,158,11,0.15)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
              <div style={{
                width: 32, height: 32, borderRadius: 8,
                background: 'rgba(245,158,11,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <Lightbulb size={16} color="#f59e0b" />
              </div>
              <div>
                <div style={{ color: '#e2e8f0', fontWeight: 700, fontSize: '0.95rem' }}>Why This Strategy Performed This Way</div>
                <div style={{ color: '#475569', fontSize: '0.72rem' }}>AI analysis by Gemini</div>
              </div>
            </div>
            <p style={{ color: '#94a3b8', fontSize: '0.86rem', lineHeight: 1.7, margin: '0 0 12px' }}>
              {result.gemini_explanation}
            </p>
            {result.knowledge_context && result.knowledge_context !== 'No relevant historical context found.' && (
              <div style={{ color: '#334155', fontSize: '0.72rem', borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: 10 }}>
                Informed by previous backtests in the knowledge base
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
