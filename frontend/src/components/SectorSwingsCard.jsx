import React from 'react';
import { PieChart } from 'lucide-react';
import './YourMorningHero.css';

function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '0.0%';
  const n = Number(v);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(1)}%`;
}

export default function SectorSwingsCard({ sectors }) {
  const list = sectors || [];
  const maxAlloc = Math.max(...list.map((s) => Number(s.allocation_pct) || 0), 1);

  return (
    <div className="ym-panel ym-sectors-panel">
      <div className="ym-panel-header">
        <PieChart size={16} />
        <span>Sector Swings</span>
      </div>

      {!list.length ? (
        <p className="ym-panel-empty">No sector exposure data yet.</p>
      ) : (
        <div className="ym-sectors-list">
          {list.map((sector) => {
            const daily = Number(sector.daily_return_pct);
            const alloc = Number(sector.allocation_pct) || 0;
            const isUp = daily > 0.05;
            const isDown = daily < -0.05;
            const pctClass = isUp ? 'ym-pct-up' : isDown ? 'ym-pct-down' : 'ym-pct-flat';
            const barWidth = `${Math.min(100, (alloc / maxAlloc) * 100)}%`;

            return (
              <div key={sector.sector_name} className="ym-sector-row">
                <div className="ym-sector-head">
                  <span className="ym-sector-name">{sector.sector_name}</span>
                  <span className={`ym-sector-pct ${pctClass}`}>{fmtPct(daily)}</span>
                </div>
                <div className="ym-sector-track">
                  <div
                    className={`ym-sector-fill ${isDown ? 'down' : isUp ? 'up' : 'flat'}`}
                    style={{ width: barWidth }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
