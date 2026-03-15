import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Plus, X, DollarSign, BarChart3, Target } from 'lucide-react';
import { API_BASE_URL, apiFetch } from './api';

const fmt = (n, dec = 2) => (n >= 0 ? '+' : '') + n.toFixed(dec);
const fmtUSD = n => (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(2);

export default function PaperPortfolioUI({ onXpGained }) {
    const [perf, setPerf]           = useState(null);
    const [loading, setLoading]     = useState(true);
    const [error, setError]         = useState(null);
    const [showAdd, setShowAdd]     = useState(false);
    const [addForm, setAddForm]     = useState({ ticker: '', direction: 'LONG', allocated: 1000, note: '' });
    const [adding, setAdding]       = useState(false);
    const [addError, setAddError]   = useState('');
    const [closing, setClosing]     = useState(null);

    const fetchPerf = async () => {
        try {
            setLoading(true);
            const data = await apiFetch(`${API_BASE_URL}/portfolio/performance`);
            setPerf(data);
            if (data.beating_spy && onXpGained) {
                // Silently poll — don't spam awards
            }
        } catch (e) {
            setError('Failed to load portfolio');
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchPerf(); }, []);

    const handleAdd = async () => {
        if (!addForm.ticker.trim()) { setAddError('Enter a ticker'); return; }
        setAdding(true);
        setAddError('');
        try {
            const data = await apiFetch(`${API_BASE_URL}/portfolio/position`, {
                method: 'POST',
                body: JSON.stringify(addForm),
            });
            if (data.error) { setAddError(data.error); return; }
            if (onXpGained) onXpGained({ xp_awarded: 10, new_badges: [] });
            setShowAdd(false);
            setAddForm({ ticker: '', direction: 'LONG', allocated: 1000, note: '' });
            await fetchPerf();
        } catch {
            setAddError('Failed to add position');
        } finally {
            setAdding(false);
        }
    };

    const handleClose = async (posId) => {
        setClosing(posId);
        try {
            await apiFetch(`${API_BASE_URL}/portfolio/close/${posId}`, { method: 'POST' });
            await fetchPerf();
        } finally {
            setClosing(null);
        }
    };

    if (loading) return (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
            <div className="spinner" style={{ width: 40, height: 40, border: '3px solid rgba(255,255,255,0.1)', borderTopColor: '#a78bfa', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
        </div>
    );

    const positions = perf?.positions || [];
    const beatingSPY = perf?.beating_spy;

    return (
        <div style={{ maxWidth: 800, margin: '0 auto', padding: '0 16px' }}>
            {/* Portfolio Summary */}
            <div style={{
                display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14, marginBottom: 24,
            }}>
                {[
                    {
                        label: 'Portfolio Value',
                        value: `$${(perf?.total_value || 0).toFixed(2)}`,
                        sub: `Started $${perf?.starting_cash?.toFixed(0) || '10,000'}`,
                        color: '#a78bfa',
                    },
                    {
                        label: 'Total P&L',
                        value: fmtUSD(perf?.total_pnl || 0),
                        sub: `${fmt(perf?.total_pnl_pct || 0)}%`,
                        color: (perf?.total_pnl || 0) >= 0 ? '#10b981' : '#ef4444',
                    },
                    {
                        label: 'vs SPY',
                        value: `${fmt(perf?.total_pnl_pct || 0)}% vs ${fmt(perf?.spy_pnl_pct || 0)}%`,
                        sub: beatingSPY ? '🔥 Beating the market!' : 'SPY is ahead',
                        color: beatingSPY ? '#10b981' : '#f59e0b',
                    },
                ].map(card => (
                    <div key={card.label} style={{
                        background: 'rgba(255,255,255,0.04)',
                        border: '1px solid rgba(255,255,255,0.08)',
                        borderRadius: 14,
                        padding: '16px 18px',
                    }}>
                        <div style={{ fontSize: 10, color: '#64748b', fontWeight: 600, letterSpacing: 1, marginBottom: 8 }}>
                            {card.label.toUpperCase()}
                        </div>
                        <div style={{ fontSize: 20, fontWeight: 800, color: card.color, marginBottom: 4 }}>
                            {card.value}
                        </div>
                        <div style={{ fontSize: 11, color: '#64748b' }}>{card.sub}</div>
                    </div>
                ))}
            </div>

            {/* Add position button */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: '#e2e8f0' }}>
                    Open Positions ({positions.length})
                </h3>
                <button
                    onClick={() => setShowAdd(s => !s)}
                    style={{
                        padding: '8px 16px',
                        borderRadius: 8,
                        border: '1px solid rgba(124,58,237,0.4)',
                        background: 'rgba(124,58,237,0.15)',
                        color: '#a78bfa',
                        fontSize: 13,
                        fontWeight: 600,
                        cursor: 'pointer',
                        display: 'flex', alignItems: 'center', gap: 6,
                    }}
                >
                    <Plus size={14} /> Add Position
                </button>
            </div>

            {/* Add form */}
            {showAdd && (
                <div style={{
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(124,58,237,0.2)',
                    borderRadius: 14,
                    padding: '18px 20px',
                    marginBottom: 20,
                }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
                        <input
                            value={addForm.ticker}
                            onChange={e => setAddForm(f => ({ ...f, ticker: e.target.value.toUpperCase() }))}
                            placeholder="Ticker (e.g. AAPL)"
                            style={inputStyle}
                        />
                        <select
                            value={addForm.direction}
                            onChange={e => setAddForm(f => ({ ...f, direction: e.target.value }))}
                            style={inputStyle}
                        >
                            <option value="LONG">LONG (Bull)</option>
                            <option value="SHORT">SHORT (Bear)</option>
                        </select>
                        <input
                            type="number"
                            value={addForm.allocated}
                            onChange={e => setAddForm(f => ({ ...f, allocated: Number(e.target.value) }))}
                            placeholder="Amount ($)"
                            style={inputStyle}
                        />
                    </div>
                    <input
                        value={addForm.note}
                        onChange={e => setAddForm(f => ({ ...f, note: e.target.value }))}
                        placeholder="Note (optional — e.g. from AI Debate)"
                        style={{ ...inputStyle, width: '100%', marginBottom: 10, boxSizing: 'border-box' }}
                    />
                    {addError && <div style={{ fontSize: 12, color: '#ef4444', marginBottom: 8 }}>{addError}</div>}
                    <div style={{ display: 'flex', gap: 10 }}>
                        <button onClick={handleAdd} disabled={adding} style={btnStyle('#7c3aed')}>
                            {adding ? 'Adding...' : 'Add Position'}
                        </button>
                        <button onClick={() => setShowAdd(false)} style={btnStyle('#374151')}>Cancel</button>
                    </div>
                </div>
            )}

            {/* Positions table */}
            {positions.length === 0 ? (
                <div style={{
                    padding: '40px 20px', textAlign: 'center',
                    background: 'rgba(255,255,255,0.02)',
                    border: '1px dashed rgba(255,255,255,0.1)',
                    borderRadius: 14,
                }}>
                    <Target size={32} color="#64748b" style={{ marginBottom: 12 }} />
                    <div style={{ fontSize: 15, color: '#64748b' }}>
                        No positions yet. Add a position after running a debate or valuation.
                    </div>
                </div>
            ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {positions.map(pos => {
                        const isPos = pos.pnl_dollar >= 0;
                        return (
                            <div key={pos.id} style={{
                                background: 'rgba(255,255,255,0.03)',
                                border: '1px solid rgba(255,255,255,0.08)',
                                borderRadius: 12,
                                padding: '14px 18px',
                                display: 'grid',
                                gridTemplateColumns: 'auto 1fr auto auto auto',
                                gap: '12px 16px',
                                alignItems: 'center',
                            }}>
                                {/* Direction badge */}
                                <div style={{
                                    padding: '4px 8px', borderRadius: 6, fontSize: 10, fontWeight: 700, letterSpacing: 0.5,
                                    background: pos.direction === 'LONG' ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)',
                                    color: pos.direction === 'LONG' ? '#10b981' : '#ef4444',
                                }}>
                                    {pos.direction}
                                </div>

                                <div>
                                    <div style={{ fontWeight: 700, color: '#e2e8f0', fontSize: 15 }}>{pos.ticker}</div>
                                    <div style={{ fontSize: 11, color: '#64748b' }}>
                                        {pos.shares.toFixed(4)} shares · ${pos.entry_price.toFixed(2)} entry
                                        {pos.note && ` · ${pos.note}`}
                                    </div>
                                </div>

                                <div style={{ textAlign: 'right' }}>
                                    <div style={{ fontSize: 14, fontWeight: 700, color: isPos ? '#10b981' : '#ef4444' }}>
                                        {fmtUSD(pos.pnl_dollar)}
                                    </div>
                                    <div style={{ fontSize: 11, color: isPos ? '#10b981' : '#ef4444' }}>
                                        {fmt(pos.pnl_pct)}%
                                    </div>
                                </div>

                                <div style={{ textAlign: 'right' }}>
                                    <div style={{ fontSize: 12, color: '#94a3b8' }}>${pos.current_price?.toFixed(2)}</div>
                                    <div style={{ fontSize: 11, color: '#64748b' }}>now</div>
                                </div>

                                <button
                                    onClick={() => handleClose(pos.id)}
                                    disabled={closing === pos.id}
                                    style={{
                                        padding: '6px 10px', borderRadius: 6,
                                        border: '1px solid rgba(239,68,68,0.3)',
                                        background: 'rgba(239,68,68,0.08)',
                                        color: '#ef4444',
                                        fontSize: 11, fontWeight: 600, cursor: 'pointer',
                                    }}
                                >
                                    {closing === pos.id ? '...' : 'Close'}
                                </button>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

const inputStyle = {
    padding: '10px 12px',
    borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.1)',
    background: 'rgba(255,255,255,0.05)',
    color: '#e2e8f0',
    fontSize: 13,
    outline: 'none',
    width: '100%',
    boxSizing: 'border-box',
};

const btnStyle = (bg) => ({
    padding: '9px 18px',
    borderRadius: 8,
    border: 'none',
    background: bg,
    color: '#fff',
    fontSize: 13,
    fontWeight: 600,
    cursor: 'pointer',
});
