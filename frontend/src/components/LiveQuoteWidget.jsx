import React, { useCallback, useState } from 'react';
import { Loader2, Search } from 'lucide-react';
import { API_BASE_URL, apiFetch } from '../api';
import { FreshnessBadge, StaleValue } from './Freshness';

/**
 * Live S&P 500 quote lookup via GET /mcp/sp500/live-quote.
 * Shows price/change with Data Trust Layer badges; EOD lake fallback is labeled stale.
 */
export default function LiveQuoteWidget({ defaultSymbol = 'AAPL' }) {
  const [symbol, setSymbol] = useState(defaultSymbol);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [quote, setQuote] = useState(null);

  const lookup = useCallback(async (symOverride) => {
    const sym = (symOverride ?? symbol).trim().toUpperCase();
    if (!sym) {
      setError('Enter a ticker symbol.');
      return;
    }
    setLoading(true);
    setError('');
    setQuote(null);
    try {
      const data = await apiFetch(`${API_BASE_URL}/mcp/sp500/live-quote?symbol=${encodeURIComponent(sym)}`);
      setQuote(data);
    } catch (e) {
      setError(e?.message || 'Quote lookup failed.');
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  const onSubmit = (e) => {
    e.preventDefault();
    lookup();
  };

  const pct = quote?.change_pct;
  const pctPositive = pct != null && pct >= 0;

  return (
    <div
      className="glass-panel"
      data-testid="live-quote-widget"
      style={{
        padding: '14px 18px',
        borderRadius: 12,
        border: '1px solid rgba(148,163,184,0.2)',
        minWidth: 280,
        flex: '1 1 280px',
        maxWidth: 420,
      }}
    >
      <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>
        Live quote lookup
      </div>
      <form onSubmit={onSubmit} style={{ display: 'flex', gap: 8, marginBottom: quote || error ? 12 : 0 }}>
        <input
          type="text"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder="AAPL"
          maxLength={10}
          data-testid="live-quote-input"
          style={{
            flex: 1,
            padding: '8px 10px',
            borderRadius: 8,
            border: '1px solid rgba(148,163,184,0.25)',
            background: 'rgba(0,0,0,0.25)',
            color: '#e2e8f0',
            fontSize: '0.9rem',
          }}
        />
        <button
          type="submit"
          disabled={loading}
          data-testid="live-quote-lookup"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '8px 12px',
            borderRadius: 8,
            border: '1px solid rgba(59,130,246,0.4)',
            background: 'rgba(59,130,246,0.15)',
            color: '#93c5fd',
            cursor: loading ? 'wait' : 'pointer',
            fontSize: '0.85rem',
            fontWeight: 600,
          }}
        >
          {loading ? <Loader2 size={14} className="spinner" /> : <Search size={14} />}
          Lookup
        </button>
      </form>

      {error && (
        <div style={{ color: '#fca5a5', fontSize: '0.85rem' }} data-testid="live-quote-error">
          {error}
        </div>
      )}

      {quote && quote.price != null && (
        <div data-testid="live-quote-result">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
            <span style={{ fontWeight: 700, color: '#e2e8f0', fontSize: '1rem' }}>{quote.symbol}</span>
            {quote.data_freshness && <FreshnessBadge freshness={quote.data_freshness} showEod />}
          </div>
          <StaleValue freshness={quote.data_freshness} priceSensitive>
            <span style={{ fontSize: '1.35rem', fontWeight: 700, color: '#f8fafc' }} data-testid="live-quote-price">
              ${Number(quote.price).toFixed(2)}
            </span>
            {pct != null && (
              <span
                style={{
                  marginLeft: 8,
                  fontSize: '0.9rem',
                  fontWeight: 600,
                  color: pctPositive ? '#34d399' : '#f87171',
                }}
                data-testid="live-quote-change"
              >
                {pctPositive ? '▲' : '▼'} {Math.abs(pct).toFixed(2)}%
              </span>
            )}
          </StaleValue>
          {quote.source && (
            <div style={{ marginTop: 6, fontSize: '0.72rem', color: '#64748b' }}>
              Source: {quote.source}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
