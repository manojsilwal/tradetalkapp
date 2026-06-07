import React, { useMemo, useState } from 'react';
import { Layers } from 'lucide-react';
import Sparkline from './Sparkline';
import './YourMorningHero.css';

function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(1)}%`;
}

function sortMovers(movers, mode) {
  const list = [...(movers || [])];
  if (mode === 'VOL') {
    list.sort((a, b) => {
      const av = Number(a.relative_volume) || Math.abs(Number(a.portfolio_impact_pct) || 0);
      const bv = Number(b.relative_volume) || Math.abs(Number(b.portfolio_impact_pct) || 0);
      return bv - av;
    });
  } else {
    list.sort(
      (a, b) => Math.abs(Number(b.daily_return_pct) || 0) - Math.abs(Number(a.daily_return_pct) || 0),
    );
  }
  return list;
}

export default function ImpactMoversPanel({ movers, sortMode, onSortChange, onOpen, selectedSymbol }) {
  const [activeSym, setActiveSym] = useState(selectedSymbol || movers?.[0]?.symbol || null);
  const sorted = useMemo(() => sortMovers(movers, sortMode), [movers, sortMode]);

  if (!sorted.length) {
    return (
      <div className="ym-panel ym-movers-panel">
        <div className="ym-panel-header">
          <Layers size={16} />
          <span>Impact Movers</span>
        </div>
        <p className="ym-panel-empty">No verified session moves in your holdings yet.</p>
      </div>
    );
  }

  return (
    <div className="ym-panel ym-movers-panel">
      <div className="ym-panel-header">
        <div className="ym-panel-title-row">
          <Layers size={16} />
          <span>Impact Movers</span>
        </div>
        <div className="ym-toggle-group">
          <button
            type="button"
            className={`ym-toggle-btn ${sortMode === 'VOL' ? 'active' : ''}`}
            onClick={() => onSortChange('VOL')}
          >
            VOL
          </button>
          <button
            type="button"
            className={`ym-toggle-btn ${sortMode === 'PRICE' ? 'active' : ''}`}
            onClick={() => onSortChange('PRICE')}
          >
            PRICE
          </button>
        </div>
      </div>

      <div className="ym-movers-list">
        {sorted.map((mover) => {
          const sym = mover.symbol;
          const daily = Number(mover.daily_return_pct);
          const isUp = daily > 0.05;
          const isDown = daily < -0.05;
          const pctClass = isUp ? 'ym-pct-up' : isDown ? 'ym-pct-down' : 'ym-pct-flat';
          const sparkStroke = isDown ? '#f87171' : isUp ? '#38bdf8' : '#94a3b8';
          const isActive = (activeSym || sorted[0]?.symbol) === sym;

          return (
            <button
              key={sym}
              type="button"
              className={`ym-mover-row ${isActive ? 'ym-mover-row-active' : ''}`}
              onClick={() => {
                setActiveSym(sym);
                onOpen(mover);
              }}
            >
              <div className="ym-mover-avatar">{sym.slice(0, 4)}</div>
              <div className="ym-mover-body">
                <div className="ym-mover-name">{mover.company_name || sym}</div>
                <div className="ym-mover-tags">
                  {(mover.sector_tags || []).join(' / ') || mover.sector || sym}
                </div>
                <div className="ym-mover-spark">
                  <Sparkline data={mover.sparkline_5d} width={90} height={22} stroke={sparkStroke} />
                </div>
              </div>
              <div className="ym-mover-stats">
                <div className={`ym-mover-pct ${pctClass}`}>{fmtPct(daily)}</div>
                <div className="ym-mover-score">Score: {mover.impact_score ?? '—'}/100</div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
