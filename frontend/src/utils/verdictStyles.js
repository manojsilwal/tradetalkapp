/** Row/badge colors for trading verdicts (Daily Brief, dashboards). */

export const VERDICT_ROW_BG = {
  'Strong Buy': 'rgba(4, 120, 87, 0.22)',
  Buy: 'rgba(16, 185, 129, 0.18)',
  Hold: 'rgba(234, 179, 8, 0.14)',
  Sell: 'rgba(239, 68, 68, 0.18)',
}

export const VERDICT_TEXT = {
  'Strong Buy': '#047857',
  Buy: '#10b981',
  Hold: '#eab308',
  Sell: '#ef4444',
}

export const VERDICT_BORDER = {
  'Strong Buy': 'rgba(4, 120, 87, 0.55)',
  Buy: 'rgba(16, 185, 129, 0.45)',
  Hold: 'rgba(234, 179, 8, 0.4)',
  Sell: 'rgba(239, 68, 68, 0.45)',
}

export function verdictRowStyle(verdict) {
  const v = verdict || 'Hold'
  return {
    background: VERDICT_ROW_BG[v] || 'rgba(148, 163, 184, 0.06)',
    borderLeft: `3px solid ${VERDICT_BORDER[v] || '#64748b'}`,
  }
}

export function verdictBadgeStyle(verdict) {
  const v = verdict || 'Hold'
  return {
    color: VERDICT_TEXT[v] || '#94a3b8',
    fontWeight: 700,
    fontSize: 11,
    letterSpacing: 0.4,
    textTransform: 'uppercase',
  }
}
