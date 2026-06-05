import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, ArrowRight, X, MessageCircle, Search } from 'lucide-react';
import { API_BASE_URL, apiFetch, apiPost } from '../api';
import PortfolioTimeline from './PortfolioTimeline';

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

function CardDetailPanel({ card, onClose, onAction }) {
  if (!card) return null;
  return (
    <div
      style={{
        marginTop: 16,
        padding: '18px 20px',
        borderRadius: 14,
        background: 'rgba(15,23,42,0.65)',
        border: '1px solid rgba(167,139,250,0.25)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
        <div>
          <p style={{ margin: 0, fontSize: 11, color: '#a78bfa', textTransform: 'uppercase' }}>What we noticed</p>
          <h3 style={{ margin: '6px 0 0', fontSize: 17, color: '#f8fafc' }}>{card.title}</h3>
        </div>
        <button type="button" onClick={onClose} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}>
          <X size={18} />
        </button>
      </div>
      {card.body && <p style={{ margin: '12px 0 0', fontSize: 13, color: '#cbd5e1', lineHeight: 1.55 }}>{card.body}</p>}
      {card.memory_context && (
        <p style={{ margin: '10px 0 0', fontSize: 12, color: '#94a3b8' }}>{card.memory_context}</p>
      )}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 16 }}>
        {(card.actions || []).map((action) => (
          <button
            key={action.action}
            type="button"
            onClick={() => onAction(card, action)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '8px 14px',
              borderRadius: 8,
              border: '1px solid rgba(148,163,184,0.25)',
              background: 'rgba(255,255,255,0.05)',
              color: '#e2e8f0',
              fontSize: 12,
              cursor: 'pointer',
            }}
          >
            {action.action === 'open_chat' ? <MessageCircle size={14} /> : <Search size={14} />}
            {action.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function YourMorningHero() {
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [trackRecord, setTrackRecord] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedCard, setSelectedCard] = useState(null);
  const [showTimeline, setShowTimeline] = useState(false);

  useEffect(() => {
    logUserAction({ action_type: 'page_open', page: 'your_morning' });
    Promise.all([
      apiFetch(`${API_BASE_URL}/portfolio/morning-brief`),
      apiFetch(`${API_BASE_URL}/portfolio/track-record`).catch(() => null),
    ])
      .then(([brief, tr]) => {
        setData(brief);
        setTrackRecord(tr);
      })
      .catch((err) => setError(err?.message || 'Failed to load'))
      .finally(() => setLoading(false));
  }, []);

  const handleCardOpen = useCallback((card) => {
    logUserAction({
      action_type: 'brief_card_click',
      entity_type: 'morning_brief_card',
      entity_id: card.id,
      symbol: card.symbol,
      page: 'your_morning',
      metadata: { card_type: card.type },
    });
    setSelectedCard(card);
  }, []);

  const handleCardAction = useCallback(
    (card, action) => {
      const sym = card.symbol;
      if (action.action === 'open_chat') {
        logUserAction({ action_type: 'chat_question', symbol: sym, page: 'your_morning', metadata: { source: 'morning_card' } });
        navigate(sym ? `/chat?ticker=${encodeURIComponent(sym)}` : '/chat');
        return;
      }
      if (action.action === 'open_trace' && sym) {
        logUserAction({ action_type: 'trace_open', symbol: sym, page: 'your_morning' });
        navigate(`/?ticker=${encodeURIComponent(sym)}`);
        return;
      }
      if (sym) {
        navigate(`/?ticker=${encodeURIComponent(sym)}`);
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

  const sessionMsg = data.market_session?.message;
  const subhead =
    sessionMsg || "Here's what changed for your money today.";

  return (
    <section style={{ marginBottom: 32 }}>
      <p style={{ margin: 0, fontSize: 11, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: 1.2 }}>
        Your Morning
      </p>
      <h2 style={{ margin: '8px 0 4px', fontSize: 22, fontWeight: 800, color: '#f8fafc' }}>{data.headline}</h2>
      <p style={{ margin: '0 0 18px', fontSize: 13, color: '#94a3b8' }}>{subhead}</p>

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

      <CardDetailPanel
        card={selectedCard}
        onClose={() => setSelectedCard(null)}
        onAction={handleCardAction}
      />

      {data.continuity_moments?.length > 0 && (
        <div style={{ marginTop: 16, padding: '14px 16px', borderRadius: 12, background: 'rgba(167,139,250,0.06)', border: '1px solid rgba(167,139,250,0.2)' }}>
          <p style={{ margin: 0, fontSize: 11, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: 0.6 }}>
            You were here before
          </p>
          {data.continuity_moments.map((m, idx) => (
            <div key={idx} style={{ marginTop: 10 }}>
              <p style={{ margin: 0, fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>{m.title}</p>
              <p style={{ margin: '4px 0 0', fontSize: 12, color: '#94a3b8', lineHeight: 1.5 }}>{m.body}</p>
            </div>
          ))}
        </div>
      )}

      {trackRecord && (
        <div style={{ marginTop: 16, padding: '14px 16px', borderRadius: 12, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(148,163,184,0.15)' }}>
          <p style={{ margin: 0, fontSize: 11, color: '#64748b', textTransform: 'uppercase', letterSpacing: 0.6 }}>
            What we noticed — track record
          </p>
          <p style={{ margin: '8px 0 0', fontSize: 13, color: '#cbd5e1', lineHeight: 1.5 }}>{trackRecord.headline}</p>
          {trackRecord.graded_count > 0 && (
            <p style={{ margin: '6px 0 0', fontSize: 12, color: '#94a3b8' }}>
              {trackRecord.directionally_right} right · {trackRecord.wrong} wrong · {trackRecord.neutral} neutral
            </p>
          )}
        </div>
      )}

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

      <div style={{ marginTop: 18 }}>
        <button
          type="button"
          onClick={() => {
            setShowTimeline((v) => !v);
            logUserAction({ action_type: 'page_open', page: 'your_morning_timeline' });
          }}
          style={{
            background: 'transparent',
            border: 'none',
            color: '#a78bfa',
            fontSize: 13,
            cursor: 'pointer',
            padding: 0,
          }}
        >
          {showTimeline ? 'Hide portfolio memory' : 'View your portfolio memory'} →
        </button>
        {showTimeline && (
          <div style={{ marginTop: 12 }}>
            <PortfolioTimeline limit={15} />
          </div>
        )}
      </div>

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
