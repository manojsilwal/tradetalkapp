import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, ArrowRight } from 'lucide-react';
import { API_BASE_URL, apiFetch, apiPost } from '../api';

function logUserAction(payload) {
  apiPost(`${API_BASE_URL}/portfolio/user-actions/log`, payload).catch(() => {});
}

function MorningBriefCard({ card, onOpen }) {
  const metricColor =
    card.primary_metric && String(card.primary_metric).startsWith('+')
      ? '#10b981'
      : card.primary_metric && String(card.primary_metric).startsWith('-')
        ? '#ef4444'
        : '#e2e8f0';

  return (
    <button
      type="button"
      onClick={() => onOpen(card)}
      style={{
        borderRadius: 16,
        border: '1px solid rgba(148,163,184,0.2)',
        background: 'rgba(255,255,255,0.03)',
        padding: '16px 18px',
        textAlign: 'left',
        cursor: 'pointer',
        transition: 'box-shadow 0.2s, border-color 0.2s',
        width: '100%',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
        <div>
          <p style={{ margin: 0, fontSize: 11, color: '#64748b', textTransform: 'uppercase', letterSpacing: 0.6 }}>
            {card.symbol || card.type}
          </p>
          <h3 style={{ margin: '6px 0 0', fontSize: 15, fontWeight: 600, color: '#f8fafc', lineHeight: 1.35 }}>
            {card.title}
          </h3>
        </div>
        <span style={{ fontSize: 18, fontWeight: 700, color: metricColor, whiteSpace: 'nowrap' }}>
          {card.primary_metric}
        </span>
      </div>
      {card.body && (
        <p style={{ margin: '12px 0 0', fontSize: 13, color: '#cbd5e1', lineHeight: 1.5 }}>{card.body}</p>
      )}
      {card.memory_context && (
        <p style={{ margin: '10px 0 0', fontSize: 12, color: '#94a3b8', lineHeight: 1.45 }}>{card.memory_context}</p>
      )}
    </button>
  );
}

export default function YourMorningHero() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    logUserAction({ action_type: 'page_open', page: 'your_morning' });
    apiFetch(`${API_BASE_URL}/portfolio/morning-brief`)
      .then((res) => setData(res))
      .catch((err) => setError(err?.message || 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  const handleCardOpen = useCallback(
    (card) => {
      logUserAction({
        action_type: 'brief_card_click',
        entity_type: 'morning_brief_card',
        entity_id: card.id,
        symbol: card.symbol,
        page: 'your_morning',
        metadata: { card_type: card.type },
      });
      if (card.symbol) {
        navigate(`/?ticker=${encodeURIComponent(card.symbol)}`);
      }
    },
    [navigate],
  );

  if (loading) {
    return (
      <section style={{ marginBottom: 28, padding: '20px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: '#94a3b8', fontSize: 14 }}>
          <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} />
          Loading Your Morning…
          <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
        </div>
      </section>
    );
  }

  if (error) {
    return (
      <section style={{ marginBottom: 28, padding: '16px 18px', borderRadius: 12, background: 'rgba(239,68,68,0.08)' }}>
        <p style={{ margin: 0, color: '#fca5a5', fontSize: 13 }}>
          We could not load your morning brief. Your holdings are still saved.
        </p>
      </section>
    );
  }

  if (!data?.has_portfolio) {
    return (
      <section
        style={{
          marginBottom: 28,
          padding: '22px 20px',
          borderRadius: 16,
          border: '1px dashed rgba(167,139,250,0.35)',
          background: 'rgba(167,139,250,0.06)',
        }}
      >
        <p style={{ margin: 0, fontSize: 11, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: 1 }}>
          Your Morning
        </p>
        <h2 style={{ margin: '8px 0 6px', fontSize: 20, fontWeight: 700, color: '#f8fafc' }}>
          Your Morning starts once you add your portfolio.
        </h2>
        <p style={{ margin: '0 0 16px', fontSize: 13, color: '#94a3b8', maxWidth: 520, lineHeight: 1.55 }}>
          Connect or import holdings to see what moved your money, since-you-added history, and holding-specific news.
        </p>
        <button
          type="button"
          onClick={() => navigate('/portfolio')}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 16px',
            borderRadius: 10,
            border: 'none',
            background: '#7c3aed',
            color: '#fff',
            fontWeight: 600,
            fontSize: 13,
            cursor: 'pointer',
          }}
        >
          Import Portfolio
          <ArrowRight size={16} />
        </button>
      </section>
    );
  }

  return (
    <section style={{ marginBottom: 32 }}>
      <p style={{ margin: 0, fontSize: 11, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: 1.2 }}>
        Your Morning
      </p>
      <h2 style={{ margin: '8px 0 4px', fontSize: 22, fontWeight: 800, color: '#f8fafc' }}>{data.headline}</h2>
      <p style={{ margin: '0 0 18px', fontSize: 13, color: '#94a3b8' }}>
        Here&apos;s what changed for your money today.
      </p>

      {data.summary && (
        <p style={{ margin: '0 0 16px', fontSize: 12, color: '#64748b' }}>
          Portfolio value ${Number(data.summary.total_value).toLocaleString()}
          {data.summary.benchmark_context?.spy_daily_return_pct != null && (
            <> · SPY {data.summary.benchmark_context.spy_daily_return_pct > 0 ? '+' : ''}
            {Number(data.summary.benchmark_context.spy_daily_return_pct).toFixed(1)}%</>
          )}
        </p>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: 14,
        }}
      >
        {(data.cards || []).slice(0, 3).map((card) => (
          <MorningBriefCard key={card.id} card={card} onOpen={handleCardOpen} />
        ))}
      </div>

      {data.watch_next?.length > 0 && (
        <div
          style={{
            marginTop: 16,
            padding: '14px 16px',
            borderRadius: 12,
            background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(148,163,184,0.15)',
          }}
        >
          <p style={{ margin: 0, fontSize: 11, color: '#64748b', textTransform: 'uppercase', letterSpacing: 0.6 }}>
            What to watch next
          </p>
          {data.watch_next.map((item, idx) => (
            <p key={idx} style={{ margin: '8px 0 0', fontSize: 13, color: '#cbd5e1' }}>
              <strong style={{ color: '#e2e8f0' }}>{item.title}</strong>
              {item.reason ? ` — ${item.reason}` : ''}
            </p>
          ))}
        </div>
      )}

      {data.continue_where_you_left_off && (
        <button
          type="button"
          onClick={() => {
            const sym = data.continue_where_you_left_off.symbol;
            if (sym) {
              logUserAction({
                action_type: 'ticker_click',
                symbol: sym,
                page: 'your_morning',
                metadata: { source: 'continue_where_you_left_off' },
              });
              navigate(`/?ticker=${encodeURIComponent(sym)}`);
            }
          }}
          style={{
            marginTop: 14,
            background: 'transparent',
            border: 'none',
            color: '#a78bfa',
            fontSize: 13,
            cursor: 'pointer',
            padding: 0,
          }}
        >
          {data.continue_where_you_left_off.label} →
        </button>
      )}

      {data.disclaimer && (
        <p style={{ margin: '16px 0 0', fontSize: 11, color: '#475569', lineHeight: 1.45 }}>{data.disclaimer}</p>
      )}
    </section>
  );
}
