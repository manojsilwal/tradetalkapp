import React, { useState, useEffect } from 'react';
import {
  FlaskConical, Lightbulb, TrendingUp, TrendingDown,
  Loader2, AlertTriangle, ChevronDown, ChevronUp, Play,
  CheckCircle2, Trophy, DollarSign, BarChart3, Info,
} from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip, Legend } from 'recharts';
import { API_BASE_URL } from './api';
import { EducationTooltip } from './components/EducationLink.jsx';

const EXAMPLE_STRATEGIES = [
  "Buy Mag7 stocks (AAPL, MSFT, GOOGL, META, AMZN, NVDA, TSLA) when PE ratio is below 25, sell when PE ratio exceeds 35",
  "Buy S&P 500 companies with revenue growing faster than 15% per year and hold for 3 years",
  "Buy stocks trading above their 200-day moving average each year and rebalance annually",
  "Buy companies with P/E ratio below 20 and positive free cash flow, rebalance yearly",
  "Buy high dividend yield stocks above 3% with debt-to-equity below 1.0",
];

const MIN_BACKTEST_DATE = "2010-01-01";

const ACTION_COLORS = {
  BUY:       { bg: 'rgba(16,185,129,0.12)',  color: '#10b981', border: 'rgba(16,185,129,0.3)' },
  SELL:      { bg: 'rgba(59,130,246,0.12)',  color: '#60a5fa', border: 'rgba(59,130,246,0.3)' },
  HOLD_CASH: { bg: 'rgba(100,116,139,0.12)', color: '#94a3b8', border: 'rgba(100,116,139,0.2)' },
};

// ── Sub-components ────────────────────────────────────────────────────────────

function StatCard({ label, value, benchmark, unit = '%', higherIsBetter = true }) {
  const isNumber  = typeof benchmark === 'number';
  const outperforms = isNumber ? (higherIsBetter ? value > benchmark : value < benchmark) : null;
  const color = outperforms === null ? '#94a3b8' : outperforms ? '#10b981' : '#ef4444';
  return (
    <div style={{
      background: 'rgba(15,23,42,0.6)', borderRadius: 10, padding: '16px 18px',
      border: '1px solid rgba(255,255,255,0.06)',
    }}>
      <div style={{ color: '#475569', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ color, fontSize: '1.5rem', fontWeight: 800, letterSpacing: '-0.02em' }}>
        {typeof value === 'number' ? `${value > 0 && higherIsBetter ? '+' : ''}${value.toFixed(1)}${unit}` : value}
      </div>
      {isNumber && (
        <div style={{ color: '#334155', fontSize: '0.72rem', marginTop: 4 }}>
          vs SPY {benchmark > 0 ? '+' : ''}{benchmark.toFixed(1)}{unit}
          {outperforms !== null && (
            <span style={{ color, marginLeft: 4 }}>
              {outperforms ? '▲ beats' : '▼ lags'}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function FilterChip({ filter, variant = 'buy' }) {
  const label = filter.metric.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const colors = variant === 'sell'
    ? { bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.2)', color: '#f87171' }
    : { bg: 'rgba(99,102,241,0.1)', border: 'rgba(99,102,241,0.25)', color: '#818cf8' };
  return (
    <span style={{
      background: colors.bg, border: `1px solid ${colors.border}`,
      borderRadius: 6, padding: '3px 10px', fontSize: '0.74rem', color: colors.color,
    }}>
      {variant === 'sell' ? '↑ SELL: ' : '↓ BUY: '}{label} {filter.op} {filter.value}
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

function groupByYear(actions) {
  const groups = {};
  for (const a of actions) {
    const year = a.date?.slice(0, 4) || 'Unknown';
    if (!groups[year]) groups[year] = [];
    groups[year].push(a);
  }
  return groups;
}

function ActionTimeline({ actions }) {
  const [openYears, setOpenYears] = useState({});
  const groups = groupByYear(actions);
  const years  = Object.keys(groups).sort().reverse();
  const toggle = y => setOpenYears(p => ({ ...p, [y]: !p[y] }));

  // Year-level sell stats
  const yearStats = year => {
    const sells = groups[year].filter(a => a.action === 'SELL');
    const totalPnl = sells.reduce((s, a) => s + (a.profit_loss_dollars || 0), 0);
    const wins = sells.filter(a => a.return_pct > 0).length;
    return { sells: sells.length, totalPnl, wins };
  };

  return (
    <div>
      <div style={{ color: '#64748b', fontSize: '0.74rem', marginBottom: 12 }}>
        {actions.length} total decisions · {actions.filter(a => a.action === 'SELL').length} closed trades
      </div>
      {years.map(year => {
        const { sells, totalPnl, wins } = yearStats(year);
        return (
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
              {sells > 0 && (
                <span style={{
                  color: totalPnl >= 0 ? '#10b981' : '#ef4444',
                  fontSize: '0.74rem', fontWeight: 700,
                }}>
                  {totalPnl >= 0 ? '+' : ''}${totalPnl.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  <span style={{ color: '#475569', fontWeight: 400, marginLeft: 4 }}>
                    ({wins}/{sells} wins)
                  </span>
                </span>
              )}
              <span style={{ color: '#475569', fontSize: '0.72rem' }}>{groups[year].length} actions</span>
              {openYears[year] ? <ChevronUp size={13} color="#475569" /> : <ChevronDown size={13} color="#475569" />}
            </button>

            {openYears[year] && (
              <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2 }}>
                {/* Header */}
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: '82px 70px 60px 70px 70px 70px 1fr',
                  gap: 8, padding: '5px 14px',
                  fontSize: '0.67rem', color: '#334155', textTransform: 'uppercase', letterSpacing: '0.06em',
                }}>
                  <span>Date</span><span>Action</span><span>Ticker</span>
                  <span>Price</span><span>Shares</span><span>P&amp;L $</span><span>Reason</span>
                </div>
                {groups[year].map((a, i) => (
                  <div key={i} style={{
                    display: 'grid',
                    gridTemplateColumns: '82px 70px 60px 70px 70px 70px 1fr',
                    gap: 8, alignItems: 'center',
                    background: 'rgba(15,23,42,0.5)', borderRadius: 6, padding: '7px 14px',
                    fontSize: '0.78rem',
                  }}>
                    <span style={{ color: '#475569' }}>{a.date}</span>
                    <ActionBadge action={a.action} />
                    <span style={{ color: '#94a3b8', fontWeight: 600 }}>{a.ticker}</span>
                    <span style={{ color: '#64748b' }}>${a.price?.toFixed(2)}</span>
                    <span style={{ color: '#64748b' }}>
                      {a.shares > 0 ? a.shares.toFixed(2) : '—'}
                    </span>
                    {a.action === 'SELL' ? (
                      <span style={{
                        color: a.profit_loss_dollars >= 0 ? '#10b981' : '#ef4444',
                        fontWeight: 700, whiteSpace: 'nowrap',
                      }}>
                        {a.profit_loss_dollars >= 0 ? '+' : ''}
                        ${Math.abs(a.profit_loss_dollars || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                        <span style={{ fontSize: '0.68rem', opacity: 0.7, marginLeft: 2 }}>
                          ({a.return_pct >= 0 ? '+' : ''}{a.return_pct?.toFixed(1)}%)
                        </span>
                      </span>
                    ) : (
                      <span style={{ color: '#334155' }}>—</span>
                    )}
                    <span style={{
                      color: '#334155', fontSize: '0.7rem',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {a.reason}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

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

// ── Leaderboard ───────────────────────────────────────────────────────────────

function Leaderboard() {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]    = useState('');

  useEffect(() => {
    fetch(`${API_BASE_URL}/strategies/leaderboard?n=20`)
      .then(r => r.json())
      .then(d => { setEntries(d.strategies || []); setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, []);

  const MEDAL = ['🥇', '🥈', '🥉'];

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#475569', padding: '32px 0', justifyContent: 'center' }}>
      <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} /> Loading leaderboard...
    </div>
  );

  if (error) return (
    <div style={{ color: '#f87171', fontSize: '0.82rem', padding: '16px 0', display: 'flex', gap: 8 }}>
      <AlertTriangle size={15} /> {error}
    </div>
  );

  if (!entries.length) return (
    <div style={{ color: '#475569', fontSize: '0.86rem', padding: '24px 0', textAlign: 'center' }}>
      No strategies yet — run a backtest to start the leaderboard.
    </div>
  );

  return (
    <div>
      <div style={{
        display: 'grid',
        gridTemplateColumns: '28px 1fr 80px 80px 80px 80px 90px',
        gap: 10, padding: '6px 14px',
        fontSize: '0.67rem', color: '#334155',
        textTransform: 'uppercase', letterSpacing: '0.06em',
      }}>
        <span>#</span><span>Strategy</span>
        <span>Total Return</span><span>CAGR</span>
        <span>Sharpe</span><span>Win Rate</span><span>Period</span>
      </div>
      {entries.map((e, i) => (
        <div key={e.id} style={{
          display: 'grid',
          gridTemplateColumns: '28px 1fr 80px 80px 80px 80px 90px',
          gap: 10, alignItems: 'center',
          background: i < 3 ? 'rgba(245,158,11,0.04)' : 'rgba(15,23,42,0.4)',
          border: `1px solid ${i < 3 ? 'rgba(245,158,11,0.12)' : 'rgba(255,255,255,0.04)'}`,
          borderRadius: 8, padding: '10px 14px', marginBottom: 4,
          fontSize: '0.8rem',
        }}>
          <span style={{ fontSize: '0.9rem' }}>{MEDAL[i] || `${i + 1}`}</span>
          <div>
            <div style={{ color: '#e2e8f0', fontWeight: 600, marginBottom: 2 }}>{e.strategy_name}</div>
            <div style={{ color: '#334155', fontSize: '0.7rem' }}>
              {e.strategy_category && e.strategy_category !== 'custom' && (
                <span style={{ color: '#64748b', marginRight: 6 }}>{e.strategy_category}</span>
              )}
              {e.start_date} → {e.end_date}
              {e.preset_id && <span style={{ color: '#475569', marginLeft: 6 }}>({e.preset_id})</span>}
              {e.outperformed && <span style={{ color: '#10b981', marginLeft: 6 }}>▲ beat SPY</span>}
            </div>
          </div>
          <span style={{ color: e.total_return_pct >= 0 ? '#10b981' : '#ef4444', fontWeight: 700 }}>
            {e.total_return_pct >= 0 ? '+' : ''}{e.total_return_pct?.toFixed(1)}%
          </span>
          <span style={{ color: e.cagr >= 0 ? '#10b981' : '#ef4444', fontWeight: 700 }}>
            {e.cagr >= 0 ? '+' : ''}{e.cagr?.toFixed(1)}%
          </span>
          <span style={{ color: e.sharpe >= 1 ? '#818cf8' : '#94a3b8' }}>
            {e.sharpe?.toFixed(2)}
          </span>
          <span style={{ color: e.win_rate >= 50 ? '#10b981' : '#ef4444' }}>
            {e.win_rate?.toFixed(0)}%
          </span>
          <span style={{ color: '#475569', fontSize: '0.72rem' }}>
            {e.total_trades} trades
          </span>
        </div>
      ))}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function BacktestUI() {
  const [activeTab, setActiveTab]   = useState('backtest');
  const [strategyText, setStrategyText] = useState('');
  const [presetId, setPresetId]     = useState('');
  const [presets, setPresets]       = useState([]);
  const [startDate, setStartDate]   = useState('2010-01-01');
  const [endDate, setEndDate]       = useState('2024-01-01');
  const [loading, setLoading]       = useState(false);
  const [result, setResult]         = useState(null);
  const [error, setError]           = useState('');
  const [showExamples, setShowExamples] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE_URL}/strategies/presets`)
      .then(r => r.json())
      .then(d => setPresets(d.presets || []))
      .catch(() => setPresets([]));
  }, []);

  const runBacktest = async () => {
    if (!presetId && !strategyText.trim()) return;
    setLoading(true); setResult(null); setError('');
    try {
      // Validate strategy before running expensive backtest
      if (!presetId && strategyText.trim()) {
        const validRes = await fetch(`${API_BASE_URL}/backtest/validate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            strategy: strategyText,
            start_date: startDate,
            end_date: endDate,
          }),
        });
        const validation = await validRes.json();
        if (!validation.valid) {
          setError(validation.reason + (validation.suggestion ? '\n💡 ' + validation.suggestion : ''));
          setLoading(false);
          return;
        }
      }

      const res = await fetch(`${API_BASE_URL}/backtest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          preset_id: presetId || undefined,
          strategy: presetId ? '' : strategyText,
          start_date: startDate,
          end_date: endDate,
        }),
      });
      if (!res.ok) throw new Error(`Server error ${res.status}: ${await res.text()}`);
      const data = await res.json();
      setResult(data);
      setActiveTab('backtest'); // stay on results tab
    } catch (e) {
      setError(e.message || 'Backtest failed.');
    } finally {
      setLoading(false);
    }
  };

  const chartData = result ? (() => {
    const portMap  = Object.fromEntries(result.portfolio_value_series.map(p => [p.date, p.value]));
    const benchMap = Object.fromEntries(result.benchmark_value_series.map(p => [p.date, p.value]));
    const allDates = [...new Set([
      ...result.portfolio_value_series.map(p => p.date),
      ...result.benchmark_value_series.map(p => p.date),
    ])].sort();
    return allDates.map(d => ({
      date: d.slice(0, 7),
      Portfolio: portMap[d],
      'S&P 500 (SPY)': benchMap[d],
    })).filter(d => d.Portfolio || d['S&P 500 (SPY)']);
  })() : [];

  const inputStyle = {
    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 8, padding: '9px 12px', color: '#e2e8f0', fontSize: '0.85rem',
    outline: 'none', width: '100%', boxSizing: 'border-box',
  };

  const tabBtn = (id, label, Icon) => (
    <button
      onClick={() => setActiveTab(id)}
      style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '7px 16px', borderRadius: 8, fontSize: '0.82rem', fontWeight: 600,
        border: 'none', cursor: 'pointer',
        background: activeTab === id ? 'rgba(99,102,241,0.18)' : 'transparent',
        color: activeTab === id ? '#818cf8' : '#475569',
      }}
    >
      <Icon size={14} /> {label}
    </button>
  );

  const totalReturnPositive = result && result.total_return_pct >= 0;

  return (
    <div style={{ padding: '24px 0', maxWidth: 960, margin: '0 auto' }}>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
        input[type="date"]::-webkit-calendar-picker-indicator { filter: invert(0.4); cursor: pointer; }
      `}</style>

      {/* ── Header ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 20 }}>
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
            Describe your investing strategy in plain English — test it against up to 20 years of history
          </p>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 4 }}>
          {tabBtn('backtest', 'Backtest', FlaskConical)}
          {tabBtn('leaderboard', 'Leaderboard', Trophy)}
        </div>
      </div>

      {/* ── PE data note ── */}
      <div style={{
        display: 'flex', gap: 8, alignItems: 'flex-start',
        background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.15)',
        borderRadius: 9, padding: '10px 14px', marginBottom: 18, fontSize: '0.77rem', color: '#64748b',
      }}>
        <Info size={13} style={{ color: '#10b981', flexShrink: 0, marginTop: 1 }} />
        <span>
          <strong style={{ color: '#10b981' }}>Historical data available from 2010.</strong>{' '}
          PE-based strategies now use SEC EDGAR filings (15 years of quarterly EPS) combined with
          yFinance — giving full PE signal coverage back to 2010. Price &amp; momentum strategies
          support 20+ years. Earliest allowed start date: <strong style={{ color: '#94a3b8' }}>2010-01-01</strong>.
        </span>
      </div>

      {activeTab === 'leaderboard' ? (
        <div style={{
          background: 'rgba(15,23,42,0.7)', borderRadius: 14, padding: '22px 24px',
          border: '1px solid rgba(255,255,255,0.07)', animation: 'fadeIn 0.35s ease-out both',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 18 }}>
            <Trophy size={16} color="#f59e0b" />
            <h3 style={{ margin: 0, color: '#e2e8f0', fontSize: '1rem', fontWeight: 700 }}>Strategy Leaderboard</h3>
            <span style={{ color: '#334155', fontSize: '0.75rem', marginLeft: 4 }}>ranked by CAGR</span>
          </div>
          <Leaderboard />
        </div>
      ) : (
        <>
          {/* ── Input Panel ── */}
          <div style={{
            background: 'rgba(15,23,42,0.7)', borderRadius: 14, padding: '22px 24px',
            border: '1px solid rgba(255,255,255,0.07)', marginBottom: 20,
          }}>
            <label style={{ color: '#94a3b8', fontSize: '0.78rem', display: 'block', marginBottom: 12, fontWeight: 600 }}>
              PROVEN STRATEGY PRESETS
            </label>
            
            {/* Category filter */}
            {(() => {
              const categories = [...new Set(presets.map(p => p.category))];
              return (
                <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
                  <button
                    onClick={() => setPresetId('')}
                    style={{
                      padding: '5px 12px', borderRadius: 20, fontSize: '0.75rem', fontWeight: 600,
                      border: !presetId ? '1px solid rgba(99,102,241,0.4)' : '1px solid rgba(255,255,255,0.08)',
                      background: !presetId ? 'rgba(99,102,241,0.15)' : 'rgba(255,255,255,0.03)',
                      color: !presetId ? '#818cf8' : '#64748b', cursor: 'pointer',
                    }}
                  >
                    ✍️ Custom
                  </button>
                  {categories.map(cat => {
                    const active = presets.find(p => p.preset_id === presetId)?.category === cat;
                    return (
                      <button key={cat} onClick={() => {
                        const first = presets.find(p => p.category === cat);
                        if (first) { setPresetId(first.preset_id); setStrategyText(''); }
                      }} style={{
                        padding: '5px 12px', borderRadius: 20, fontSize: '0.75rem', fontWeight: 600,
                        border: active ? '1px solid rgba(99,102,241,0.4)' : '1px solid rgba(255,255,255,0.08)',
                        background: active ? 'rgba(99,102,241,0.15)' : 'rgba(255,255,255,0.03)',
                        color: active ? '#818cf8' : '#64748b', cursor: 'pointer',
                      }}>
                        {cat}
                      </button>
                    );
                  })}
                </div>
              );
            })()}

            {/* Preset cards grid */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 10, marginBottom: 16 }}>
              {presets.map(p => {
                const isActive = presetId === p.preset_id;
                const catColors = {
                  'Factor': '#818cf8', 'Momentum': '#f59e0b', 'Value': '#10b981',
                  'Macro': '#ef4444', 'Income': '#22d3ee', 'Blended': '#a78bfa', 'Quality': '#34d399',
                };
                const accent = catColors[p.category] || '#818cf8';
                return (
                  <button key={p.preset_id} onClick={() => {
                    setPresetId(isActive ? '' : p.preset_id);
                    if (!isActive) setStrategyText('');
                  }} style={{
                    textAlign: 'left', cursor: 'pointer', padding: '14px 16px', borderRadius: 12,
                    background: isActive ? `${accent}11` : 'rgba(255,255,255,0.02)',
                    border: isActive ? `1.5px solid ${accent}44` : '1px solid rgba(255,255,255,0.06)',
                    transition: 'all 0.2s',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <span style={{
                        fontSize: '0.65rem', fontWeight: 700, padding: '2px 8px', borderRadius: 10,
                        background: `${accent}18`, color: accent, letterSpacing: '0.04em',
                      }}>
                        {p.category}
                      </span>
                      <span style={{ fontSize: '0.68rem', color: '#475569' }}>{p.rebalance_freq}</span>
                      {isActive && <CheckCircle2 size={14} color={accent} style={{ marginLeft: 'auto' }} />}
                    </div>
                    <div style={{ color: isActive ? '#e2e8f0' : '#94a3b8', fontSize: '0.85rem', fontWeight: 600, marginBottom: 4 }}>
                      {p.name}
                    </div>
                    <div style={{ color: '#475569', fontSize: '0.72rem', lineHeight: 1.4 }}>
                      {p.short_description}
                    </div>
                  </button>
                );
              })}
            </div>
            <label style={{ color: '#94a3b8', fontSize: '0.78rem', display: 'block', marginBottom: 8, fontWeight: 600 }}>
              STRATEGY DESCRIPTION
            </label>
            <textarea
              value={strategyText}
              onChange={e => {
                setStrategyText(e.target.value);
                if (e.target.value.trim()) setPresetId('');
              }}
              placeholder='e.g. "Buy Mag7 stocks when PE ratio is below 25, sell when PE exceeds 35"'
              rows={3}
              style={{ ...inputStyle, resize: 'vertical', lineHeight: 1.6, minHeight: 80, fontFamily: 'inherit' }}
            />

            <div style={{ display: 'flex', gap: 12, marginTop: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div style={{ flex: '1 1 130px' }}>
                <label style={{ color: '#475569', fontSize: '0.72rem', display: 'block', marginBottom: 4 }}>FROM</label>
                <input type="date" value={startDate} min={MIN_BACKTEST_DATE} onChange={e => setStartDate(e.target.value)} style={inputStyle} />
              </div>
              <div style={{ flex: '1 1 130px' }}>
                <label style={{ color: '#475569', fontSize: '0.72rem', display: 'block', marginBottom: 4 }}>TO</label>
                <input type="date" value={endDate} min={MIN_BACKTEST_DATE} onChange={e => setEndDate(e.target.value)} style={inputStyle} />
              </div>
              <button
                onClick={runBacktest}
                disabled={loading || (!presetId && !strategyText.trim())}
                style={{
                  flex: '2 1 180px',
                  background: loading ? 'rgba(99,102,241,0.25)' : 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                  border: 'none', borderRadius: 8, padding: '10px 20px',
                  color: '#fff', fontWeight: 700, fontSize: '0.88rem',
                  cursor: loading || (!presetId && !strategyText.trim()) ? 'not-allowed' : 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7,
                  boxShadow: loading ? 'none' : '0 4px 14px rgba(99,102,241,0.35)',
                }}
              >
                {loading
                  ? <><Loader2 size={15} style={{ animation: 'spin 1s linear infinite' }} /> Running...</>
                  : <><Play size={15} /> Run Backtest</>}
              </button>
            </div>

            {/* Example strategies */}
            <div style={{ marginTop: 14 }}>
              <button
                onClick={() => setShowExamples(v => !v)}
                style={{ background: 'none', border: 'none', color: '#475569', fontSize: '0.75rem', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4, padding: 0 }}
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
              display: 'flex', gap: 10, color: '#f87171', fontSize: '0.83rem',
            }}>
              <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} /> {error}
            </div>
          )}

          {/* ── Results ── */}
          {result && !loading && (
            <div style={{ animation: 'fadeIn 0.5s ease-out both' }}>

              {/* ── Portfolio Summary Banner ── */}
              <div style={{
                background: `linear-gradient(135deg, ${totalReturnPositive ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)'}, rgba(99,102,241,0.06))`,
                border: `1px solid ${totalReturnPositive ? 'rgba(16,185,129,0.2)' : 'rgba(239,68,68,0.2)'}`,
                borderRadius: 14, padding: '20px 24px', marginBottom: 20,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                  <DollarSign size={16} color={totalReturnPositive ? '#10b981' : '#ef4444'} />
                  <span style={{ color: '#94a3b8', fontSize: '0.78rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                    Portfolio Summary
                  </span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
                  {[
                    { label: 'Starting Capital', val: `$${result.initial_investment.toLocaleString()}`, color: '#64748b' },
                    { label: 'Final Value', val: `$${result.final_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: totalReturnPositive ? '#10b981' : '#ef4444' },
                    { label: 'Total Return $', val: `${result.total_return_dollars >= 0 ? '+' : ''}$${result.total_return_dollars.toLocaleString(undefined, { maximumFractionDigits: 0 })}`, color: totalReturnPositive ? '#10b981' : '#ef4444' },
                    { label: 'Total Return %', val: `${result.total_return_pct >= 0 ? '+' : ''}${result.total_return_pct.toFixed(1)}%`, color: totalReturnPositive ? '#10b981' : '#ef4444' },
                  ].map(({ label, val, color }) => (
                    <div key={label}>
                      <div style={{ color: '#475569', fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>{label}</div>
                      <div style={{ color, fontSize: '1.35rem', fontWeight: 800, letterSpacing: '-0.02em' }}>{val}</div>
                    </div>
                  ))}
                </div>
              </div>

              {/* ── Parsed Strategy ── */}
              <div style={{
                background: 'rgba(15,23,42,0.6)', borderRadius: 12, padding: '16px 20px',
                border: '1px solid rgba(99,102,241,0.15)', marginBottom: 20,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                  <CheckCircle2 size={14} color="#10b981" />
                  <span style={{ color: '#94a3b8', fontSize: '0.76rem', fontWeight: 600 }}>PARSED STRATEGY</span>
                </div>
                <div style={{ color: '#e2e8f0', fontWeight: 700, fontSize: '0.95rem', marginBottom: 10 }}>
                  {result.strategy.name}
                </div>
                <div style={{ color: '#64748b', fontSize: '0.74rem', marginBottom: 8, lineHeight: 1.5 }}>
                  {result.strategy.description}
                </div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
                  {(result.strategy.filters || []).length === 0 && !result.strategy.rank_by_metric && (
                    <span style={{ color: '#475569', fontSize: '0.72rem' }}>No static filters</span>
                  )}
                  {(result.strategy.filters || []).map((f, i) => <FilterChip key={i} filter={f} variant="buy" />)}
                  {(result.strategy.sell_filters || []).map((f, i) => <FilterChip key={i} filter={f} variant="sell" />)}
                </div>
                {result.strategy.rank_by_metric && (
                  <div style={{ color: '#818cf8', fontSize: '0.72rem', marginBottom: 8 }}>
                    Rank: <strong>{result.strategy.rank_by_metric}</strong> · top {result.strategy.select_top_n} ·{' '}
                    {result.strategy.rank_higher_is_better ? 'higher = better' : 'lower = better'}
                  </div>
                )}
                {result.strategy.survivorship_note && (
                  <div style={{ color: '#64748b', fontSize: '0.7rem', marginBottom: 8, fontStyle: 'italic' }}>
                    {result.strategy.survivorship_note}
                  </div>
                )}
                <div style={{ color: '#475569', fontSize: '0.74rem' }}>
                  {result.strategy.start_date} → {result.strategy.end_date} ·
                  {' '}{result.strategy.rebalance_months}mo check interval ·
                  {' '}{result.strategy.universe?.length ?? 0} stocks screened
                  {result.strategy.sell_filters?.length > 0 && (
                    <span style={{ color: '#818cf8', marginLeft: 6 }}>· event-driven exits</span>
                  )}
                </div>
              </div>

              {/* ── Stats + Chart ── */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, marginBottom: 20 }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  <StatCard label="CAGR"        value={result.cagr}           benchmark={result.benchmark_cagr} />
                  <StatCard label="Sharpe Ratio" value={result.sharpe_ratio}   benchmark={1.0} unit="" />
                  <StatCard label="Max Drawdown" value={result.max_drawdown}   benchmark={-20} unit="%" higherIsBetter={false} />
                  <StatCard label="Win Rate"     value={result.win_rate}       benchmark={null} unit="%" />
                </div>
                <div style={{
                  background: 'rgba(15,23,42,0.5)', borderRadius: 12, padding: '16px 12px',
                  border: '1px solid rgba(255,255,255,0.06)',
                }}>
                  <div style={{ color: '#64748b', fontSize: '0.71rem', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10, paddingLeft: 4 }}>
                    Portfolio vs S&P 500 · Starting $10,000
                  </div>
                  <ResponsiveContainer width="100%" height={200}>
                    <AreaChart data={chartData} margin={{ top: 0, right: 8, left: -10, bottom: 0 }}>
                      <defs>
                        <linearGradient id="portGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%"  stopColor="#8b5cf6" stopOpacity={0.25} />
                          <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="spyGrad"  x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.15} />
                          <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                      <XAxis dataKey="date" tick={{ fill: '#334155', fontSize: 10 }} tickLine={false} interval="preserveStartEnd" />
                      <YAxis tick={{ fill: '#334155', fontSize: 10 }} tickLine={false} tickFormatter={v => `$${(v/1000).toFixed(0)}k`} />
                      <Tooltip content={<ChartTooltip />} />
                      <Legend wrapperStyle={{ fontSize: '0.72rem', color: '#475569' }} />
                      <Area type="monotone" dataKey="Portfolio"     stroke="#8b5cf6" fill="url(#portGrad)" strokeWidth={2} dot={false} />
                      <Area type="monotone" dataKey="S&P 500 (SPY)" stroke="#3b82f6" fill="url(#spyGrad)"  strokeWidth={1.5} dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* ── Best / Worst ── */}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 20 }}>
                {[
                  { label: 'Best Period',  value: result.best_period,  color: '#10b981', Icon: TrendingUp },
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

              {/* ── Transaction Log ── */}
              <div style={{
                background: 'rgba(15,23,42,0.6)', borderRadius: 12, padding: '18px 22px',
                border: '1px solid rgba(255,255,255,0.06)', marginBottom: 20,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
                  <BarChart3 size={14} color="#818cf8" />
                  <h3 style={{ margin: 0, color: '#94a3b8', fontSize: '0.78rem', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
                    Transaction Log
                  </h3>
                  <span style={{ color: '#334155', fontSize: '0.72rem' }}>with dollar P&amp;L per trade</span>
                </div>
                <ActionTimeline actions={result.actions} />
              </div>

              {/* ── AI Explanation ── */}
              <div style={{
                background: 'rgba(15,23,42,0.6)', borderRadius: 12, padding: '20px 24px',
                border: '1px solid rgba(245,158,11,0.15)',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
                  <div style={{ width: 32, height: 32, borderRadius: 8, background: 'rgba(245,158,11,0.1)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <Lightbulb size={16} color="#f59e0b" />
                  </div>
                  <div>
                    <div style={{ color: '#e2e8f0', fontWeight: 700, fontSize: '0.95rem' }}>Why This Strategy Performed This Way</div>
                    <div style={{ color: '#475569', fontSize: '0.72rem' }}>AI analysis · {result.actions.filter(a => a.action === 'SELL').length} closed trades analysed</div>
                  </div>
                </div>
                <p style={{ color: '#94a3b8', fontSize: '0.86rem', lineHeight: 1.7, margin: '0 0 12px' }}>
                  {result.ai_explanation}
                </p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>
                    <EducationTooltip term="sharpe ratio" />
                    <EducationTooltip term="cagr" />
                    <EducationTooltip term="max drawdown" />
                </div>
                {result.knowledge_context && result.knowledge_context !== 'No relevant historical context found.' && (
                  <div style={{ color: '#334155', fontSize: '0.72rem', borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: 10 }}>
                    Informed by prior backtests in the knowledge base
                  </div>
                )}
              </div>

              {/* ── Strategy Lab Feedback Loop ── */}
              {result && (
                <div style={{
                  display: 'flex', gap: 12, marginTop: 20, padding: 16,
                  background: 'rgba(255,255,255,0.03)', borderRadius: 12,
                  border: '1px solid rgba(255,255,255,0.06)',
                }}>
                  <button onClick={() => {
                    setStrategyText(prev => prev || result?.strategy?.name || '');
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                  }} style={{
                    padding: '10px 18px', borderRadius: 10, fontSize: 13, fontWeight: 600,
                    border: '1px solid rgba(139,92,246,0.3)', background: 'rgba(139,92,246,0.1)',
                    color: '#a78bfa', cursor: 'pointer',
                  }}>
                    🔄 Try a Variation
                  </button>
                  <button onClick={() => {
                    setPresetId('');
                    window.scrollTo({ top: 0, behavior: 'smooth' });
                  }} style={{
                    padding: '10px 18px', borderRadius: 10, fontSize: 13, fontWeight: 600,
                    border: '1px solid rgba(59,130,246,0.3)', background: 'rgba(59,130,246,0.1)',
                    color: '#60a5fa', cursor: 'pointer',
                  }}>
                    📊 Compare with Preset
                  </button>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
