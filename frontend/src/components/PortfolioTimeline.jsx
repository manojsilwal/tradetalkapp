import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { API_BASE_URL, apiFetch } from '../api';

export default function PortfolioTimeline({ limit = 12, compact = false }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    apiFetch(`${API_BASE_URL}/portfolio/timeline?limit=${limit}`)
      .then((res) => setItems(res.items || []))
      .catch((err) => setError(err?.message || 'Timeline unavailable'))
      .finally(() => setLoading(false));
  }, [limit]);

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#64748b', fontSize: 13 }}>
        <Loader2 size={16} style={{ animation: 'spin 1s linear infinite' }} />
        Loading your portfolio memory…
        <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
      </div>
    );
  }

  if (error) {
    return <p style={{ margin: 0, fontSize: 12, color: '#64748b' }}>{error}</p>;
  }

  if (!items.length) {
    return (
      <p style={{ margin: 0, fontSize: 12, color: '#64748b' }}>
        Your portfolio memory will grow as you hold positions and markets move.
      </p>
    );
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: compact ? 8 : 10,
        maxHeight: compact ? 220 : 320,
        overflowY: 'auto',
        paddingRight: 4,
      }}
    >
      {items.map((item) => (
        <div
          key={item.id || `${item.event_date}-${item.title}`}
          style={{
            padding: compact ? '10px 12px' : '12px 14px',
            borderRadius: 10,
            background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(148,163,184,0.12)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>{item.title}</span>
            {item.symbol && (
              <span style={{ fontSize: 11, color: '#a78bfa', fontWeight: 600 }}>{item.symbol}</span>
            )}
          </div>
          {item.event_date && (
            <p style={{ margin: '4px 0 0', fontSize: 11, color: '#64748b' }}>{item.event_date}</p>
          )}
          {item.description && item.description !== item.title && (
            <p style={{ margin: '6px 0 0', fontSize: 12, color: '#94a3b8', lineHeight: 1.45 }}>
              {item.description}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
