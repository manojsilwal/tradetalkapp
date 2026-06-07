import React from 'react';
import { Activity, TrendingDown, TrendingUp } from 'lucide-react';
import './YourMorningHero.css';

export default function PortfolioSentimentCard({ sentiment }) {
  if (!sentiment) {
    return (
      <div className="ym-panel ym-sentiment-panel">
        <div className="ym-panel-header">
          <Activity size={16} />
          <span>Portfolio Sentiment</span>
        </div>
        <p className="ym-panel-empty">Sentiment unavailable.</p>
      </div>
    );
  }

  const score = Number(sentiment.score ?? 0.5);
  const label = sentiment.label || 'NEUTRAL';
  const gaugePct = sentiment.gauge_position_pct ?? Math.round(score * 100);
  const isBull = label === 'BULLISH';
  const isBear = label === 'BEARISH';

  return (
    <div className="ym-panel ym-sentiment-panel">
      <div className="ym-panel-header">
        <Activity size={16} />
        <span>Portfolio Sentiment</span>
      </div>

      <div className="ym-sentiment-main">
        <span className="ym-sentiment-score">{score.toFixed(2)}</span>
        <span className={`ym-sentiment-label ${isBull ? 'bull' : isBear ? 'bear' : 'neutral'}`}>
          {isBull ? <TrendingUp size={14} /> : isBear ? <TrendingDown size={14} /> : null}
          {label}
        </span>
      </div>

      <div className="ym-sentiment-gauge">
        <div className="ym-gauge-track">
          <span className="ym-gauge-zone bear">BEAR</span>
          <span className="ym-gauge-zone neutral">NEUTRAL</span>
          <span className="ym-gauge-zone bull">BULL</span>
          <div className="ym-gauge-marker" style={{ left: `${Math.min(98, Math.max(2, gaugePct))}%` }} />
        </div>
      </div>
    </div>
  );
}
