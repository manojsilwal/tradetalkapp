/**
 * GlobalMarketsChart — standalone export (all markets).
 * Primary surface is now embedded in GlobalCapFlowDashboard per tab.
 */
import React, { useState } from 'react';
import GlobalMarketsChartPanel from './macro/GlobalMarketsChartPanel';
import { ALL_GLOBAL_MARKETS } from './macro/globalMarketsConfig';

const PERIODS = [
  { id: '1M', label: '1M' },
  { id: '3M', label: '3M' },
  { id: 'YTD', label: 'YTD' },
  { id: '1Y', label: '1Y' },
];

export default function GlobalMarketsChart() {
  const [period, setPeriod] = useState('3M');

  return (
    <div
      className="dash-card glass-panel fade-in"
      data-testid="global-markets-chart-standalone"
      style={{
        padding: '28px 28px 20px',
        borderRadius: '20px',
        background: 'rgba(9,13,24,0.75)',
        border: '1px solid rgba(255,255,255,0.07)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12, marginBottom: 12 }}>
        <div>
          <h3 style={{ margin: '0 0 4px', fontSize: '1.1rem', fontWeight: 700, color: '#f1f5f9' }}>
            Global Capital Flows
          </h3>
          <p style={{ margin: 0, fontSize: '0.78rem', color: '#64748b' }}>
            All markets indexed to the start of the period.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          {PERIODS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => setPeriod(p.id)}
              style={{
                padding: '5px 11px', borderRadius: 7, fontSize: '0.78rem', fontWeight: 600,
                border: period === p.id ? '1px solid rgba(255,255,255,0.5)' : '1px solid rgba(255,255,255,0.1)',
                background: period === p.id ? 'rgba(255,255,255,0.12)' : 'transparent',
                color: period === p.id ? '#f1f5f9' : '#64748b',
                cursor: 'pointer',
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      <GlobalMarketsChartPanel
        markets={ALL_GLOBAL_MARKETS}
        period={period}
        dataTestId="global-markets-chart"
      />
    </div>
  );
}
