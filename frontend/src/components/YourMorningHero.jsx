import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, ArrowRight, X, MessageCircle, Search } from 'lucide-react';
import { API_BASE_URL, apiFetchTimed, apiPost } from '../api';
import PortfolioTimeline from './PortfolioTimeline';
import ImpactMoversPanel from './ImpactMoversPanel';
import PortfolioSentimentCard from './PortfolioSentimentCard';
import SectorSwingsCard from './SectorSwingsCard';
import { footerMoverLabel, pickFooterMover } from '../utils/impactMovers';
import './YourMorningHero.css';

function logUserAction(payload) {
  apiPost(`${API_BASE_URL}/portfolio/user-actions/log`, payload).catch(() => {});
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
          <p className="ym-drawer-label">Details</p>
          <h3 style={{ margin: '6px 0 0', fontSize: 17, color: '#f8fafc' }}>
            {card.symbol || card.sector_name || card.title}
          </h3>
        </div>
        <button type="button" onClick={onClose} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}>
          <X size={18} />
        </button>
      </div>
      {card.impact_label && (
        <p style={{ margin: '10px 0 0', fontSize: 12, color: '#94a3b8' }}>{card.impact_label}</p>
      )}
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
  const [showMore, setShowMore] = useState(false);
  const [moverSortMode, setMoverSortMode] = useState('PRICE');

  useEffect(() => {
    logUserAction({ action_type: 'page_open', page: 'your_morning' });
    Promise.all([
      apiFetchTimed(`${API_BASE_URL}/portfolio/morning-brief`, {}, 45000),
      apiFetchTimed(`${API_BASE_URL}/portfolio/track-record`, {}, 20000).catch(() => null),
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

  const handleMoverOpen = useCallback(
    (mover) => {
      const sym = mover?.symbol;
      const card = (data?.cards || []).find((c) => c.symbol === sym);
      if (card) {
        handleCardOpen(card);
        return;
      }
      const daily = mover.daily_return_pct;
      const direction = daily > 0.05 ? 'up' : daily < -0.05 ? 'down' : 'flat';
      const chip = direction === 'up' ? 'LIFT' : direction === 'down' ? 'DRAG' : 'FLAT';
      handleCardOpen({
        id: `mover_${sym}`,
        type: 'holding_move',
        symbol: sym,
        title: sym,
        primary_metric: daily != null ? `${daily > 0 ? '+' : ''}${Number(daily).toFixed(1)}%` : '—',
        direction,
        chip,
        body: '',
        memory_context: '',
        actions: [
          { label: 'View why', action: 'open_trace' },
          { label: 'Ask AI', action: 'open_chat' },
        ],
      });
    },
    [data?.cards, handleCardOpen],
  );

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

  const footerMover = useMemo(
    () => pickFooterMover(data?.impact_movers, {
      sortMode: moverSortMode,
      selectedSymbol: selectedCard?.symbol,
    }),
    [data?.impact_movers, moverSortMode, selectedCard?.symbol],
  );
  const footerLinkLabel = footerMover ? footerMoverLabel(footerMover) : null;

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
          Connect or import holdings to see what moved your money and holding-specific context.
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
  const portDaily = data.summary?.daily_return_pct;
  const hasMoreContext =
    (data.continuity_moments?.length > 0) || trackRecord;

  return (
    <section style={{ marginBottom: 32 }}>
      <p style={{ margin: 0, fontSize: 11, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: 1.2 }}>
        Your Morning
      </p>
      <h2 style={{ margin: '8px 0 4px', fontSize: 22, fontWeight: 800, color: '#f8fafc' }}>{data.headline}</h2>
      {sessionMsg && (
        <p style={{ margin: '0 0 12px', fontSize: 13, color: '#94a3b8' }}>{sessionMsg}</p>
      )}

      {data.summary && (
        <div className="ym-hero-kpis">
          <div className="ym-kpi ym-kpi-portfolio">
            <span className="ym-kpi-label">Portfolio</span>
            <span className="ym-kpi-value">
              ${Number(data.summary.total_value).toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
          </div>
          {portDaily != null && (
            <div className={`ym-kpi ym-kpi-today ${portDaily < -0.05 ? 'ym-kpi-down' : portDaily > 0.05 ? 'ym-kpi-up' : 'ym-kpi-flat'}`}>
              <span className="ym-kpi-label">Today</span>
              <span className="ym-kpi-value ym-kpi-value-hero">
                {portDaily > 0 ? '+' : ''}{Number(portDaily).toFixed(1)}%
              </span>
            </div>
          )}
          {data.summary.benchmark_context?.spy_daily_return_pct != null && (
            <div className="ym-kpi ym-kpi-benchmark">
              <span className="ym-kpi-label">vs SPY</span>
              <span className="ym-kpi-value">
                {data.summary.benchmark_context.spy_daily_return_pct > 0 ? '+' : ''}
                {Number(data.summary.benchmark_context.spy_daily_return_pct).toFixed(1)}%
              </span>
            </div>
          )}
        </div>
      )}

      <div className="ym-dashboard-grid">
        <ImpactMoversPanel
          movers={data.impact_movers}
          sortMode={moverSortMode}
          onSortChange={setMoverSortMode}
          onOpen={handleMoverOpen}
          selectedSymbol={selectedCard?.symbol}
        />
        <div className="ym-dashboard-side">
          <PortfolioSentimentCard sentiment={data.portfolio_sentiment} />
          <SectorSwingsCard sectors={data.sector_swings} />
        </div>
      </div>

      <CardDetailPanel
        card={selectedCard}
        onClose={() => setSelectedCard(null)}
        onAction={handleCardAction}
      />

      {data.watch_next?.length > 0 && (
        <div className="ym-watch-chips">
          {data.watch_next.slice(0, 2).map((item, idx) => (
            <span key={idx} className="ym-watch-chip">
              {item.title}{item.reason ? ` · ${item.reason}` : ''}
            </span>
          ))}
        </div>
      )}

      {hasMoreContext && (
        <>
          <button
            type="button"
            className="ym-more-toggle"
            onClick={() => {
              setShowMore((v) => !v);
              if (!showMore) {
                logUserAction({ action_type: 'page_open', page: 'your_morning_more' });
              }
            }}
          >
            {showMore ? 'Hide more context' : 'More context'} →
          </button>
          {showMore && (
            <div className="ym-more-panel">
              {trackRecord && (
                <div className="ym-more-section">
                  <p className="ym-more-label">Observation history</p>
                  <p className="ym-more-text">{trackRecord.headline}</p>
                  {trackRecord.graded_count > 0 && (
                    <p className="ym-more-text" style={{ fontSize: 12, color: '#94a3b8' }}>
                      {trackRecord.directionally_right} right · {trackRecord.wrong} wrong · {trackRecord.neutral} neutral
                    </p>
                  )}
                </div>
              )}
              {data.continuity_moments?.map((m, idx) => (
                <div key={idx} className="ym-more-section">
                  <p className="ym-more-label">{m.title}</p>
                  <p className="ym-more-text">{m.body}</p>
                </div>
              ))}
              <div className="ym-more-section">
                <p className="ym-more-label">Portfolio memory</p>
                <PortfolioTimeline limit={10} compact />
              </div>
            </div>
          )}
        </>
      )}

      <div className="ym-footer-links">
        {footerLinkLabel && footerMover?.symbol && (
          <button
            type="button"
            className="ym-footer-link"
            onClick={() => {
              const sym = footerMover.symbol;
              logUserAction({
                action_type: 'ticker_click',
                symbol: sym,
                page: 'your_morning',
                metadata: { source: 'morning_footer_mover', sort_mode: moverSortMode },
              });
              navigate(`/?ticker=${encodeURIComponent(sym)}`);
            }}
          >
            {footerLinkLabel} →
          </button>
        )}
      </div>

      {data.disclaimer && (
        <p style={{ margin: '12px 0 0', fontSize: 11, color: '#475569', lineHeight: 1.45 }}>{data.disclaimer}</p>
      )}
    </section>
  );
}
