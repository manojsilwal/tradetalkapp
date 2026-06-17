import React, { useState } from 'react';

const TONE_ROWS = [
  { tone: 'strong_positive', label: 'Strong positive', examples: 'STRONG BUY, Exceptional, UNDERVALUED (value opportunity)', color: 'var(--accent-green, #10b981)' },
  { tone: 'positive', label: 'Positive', examples: 'BUY, Favorable, BULLISH', color: '#34d399' },
  { tone: 'neutral', label: 'Neutral', examples: 'NEUTRAL, Balanced, NEAR FAIR VALUE', color: 'var(--dt-muted, #94a3b8)' },
  { tone: 'caution', label: 'Caution', examples: 'Caution, Stretched, REJECTED (capped)', color: '#f59e0b' },
  { tone: 'negative', label: 'Negative', examples: 'SELL, Avoid, OVERVALUED (price risk), BEARISH', color: '#ef4444' },
];

export default function VerdictToneLegend() {
  const [open, setOpen] = useState(false);

  return (
    <div style={{ marginTop: 10 }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          background: 'none',
          border: 'none',
          color: 'var(--dt-muted)',
          fontSize: '0.78rem',
          cursor: 'pointer',
          padding: 0,
          textDecoration: 'underline',
        }}
      >
        {open ? 'Hide rating guide' : 'What do these ratings mean?'}
      </button>
      {open && (
        <ul style={{ margin: '8px 0 0', padding: 0, listStyle: 'none', fontSize: '0.75rem' }}>
          {TONE_ROWS.map((row) => (
            <li key={row.tone} style={{ marginBottom: 6, color: 'var(--dt-muted)' }}>
              <span style={{ color: row.color, fontWeight: 700 }}>{row.label}</span>
              {' — '}
              {row.examples}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
