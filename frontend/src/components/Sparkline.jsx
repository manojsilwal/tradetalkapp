import React from 'react';

function normalizeValues(data) {
  if (!data || data.length < 2) return null;
  if (typeof data[0] === 'number') {
    return data.map((v) => Number(v));
  }
  return data.map((d) => Number(d.value));
}

export default function Sparkline({ data, width = 70, height = 20, stroke = '#10b981' }) {
  const values = normalizeValues(data);
  if (!values || values.length < 2) return null;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * width;
      const y = height - ((v - min) / range) * (height - 4) - 2;
      return `${x},${y}`;
    })
    .join(' ');

  const lastY = height - ((values[values.length - 1] - min) / range) * (height - 4) - 2;

  return (
    <svg width={width} height={height} style={{ overflow: 'visible' }}>
      <polyline fill="none" stroke={stroke} strokeWidth="1.8" points={points} />
      <circle cx={width} cy={lastY} r="2.5" fill={stroke} />
    </svg>
  );
}
