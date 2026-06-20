import { TrendingUp, ShieldAlert, Globe, Zap, Landmark } from 'lucide-react';

export const DEBATE_AGENTS = [
  { role: 'bull', label: 'Bull Analyst', Icon: TrendingUp, color: '#10b981', bg: 'rgba(16,185,129,0.08)' },
  { role: 'bear', label: 'Bear Analyst', Icon: ShieldAlert, color: '#ef4444', bg: 'rgba(239,68,68,0.08)' },
  { role: 'macro', label: 'Macro Economist', Icon: Globe, color: '#3b82f6', bg: 'rgba(59,130,246,0.08)' },
  { role: 'value', label: 'Value Investor', Icon: Landmark, color: '#f59e0b', bg: 'rgba(245,158,11,0.08)' },
  { role: 'momentum', label: 'Momentum Trader', Icon: Zap, color: '#8b5cf6', bg: 'rgba(139,92,246,0.08)' },
];

export const STANCE_STYLES = {
  BULLISH: { bg: 'rgba(16,185,129,0.15)', color: '#10b981', label: 'BULLISH' },
  BEARISH: { bg: 'rgba(239,68,68,0.15)', color: '#ef4444', label: 'BEARISH' },
  NEUTRAL: { bg: 'rgba(100,116,139,0.15)', color: '#94a3b8', label: 'NEUTRAL' },
};

export const VERDICT_STYLES = {
  'STRONG BUY': { color: '#10b981', glow: '0 0 20px rgba(16,185,129,0.4)' },
  BUY: { color: '#34d399', glow: '0 0 16px rgba(52,211,153,0.3)' },
  NEUTRAL: { color: '#94a3b8', glow: 'none' },
  SELL: { color: '#f87171', glow: '0 0 16px rgba(248,113,113,0.3)' },
  'STRONG SELL': { color: '#ef4444', glow: '0 0 20px rgba(239,68,68,0.4)' },
};

/** Count bullish / bearish / neutral stances from debate arguments or API scores. */
export function deriveDebateScores(result) {
  if (!result) return { bull: 0, bear: 0, neutral: 0 };
  if (result.bull_score != null && result.bear_score != null) {
    return {
      bull: result.bull_score,
      bear: result.bear_score,
      neutral: result.neutral_score ?? 0,
    };
  }
  const args = result.arguments || [];
  let bull = 0;
  let bear = 0;
  let neutral = 0;
  for (const a of args) {
    if (a.stance === 'BULLISH') bull += 1;
    else if (a.stance === 'BEARISH') bear += 1;
    else neutral += 1;
  }
  return { bull, bear, neutral };
}

/** Same tiering as backend debate_agents heuristic fallback. */
export function heuristicDebateVerdict(bull, bear) {
  if (bull >= 4) return 'STRONG BUY';
  if (bull === 3) return 'BUY';
  if (bear >= 4) return 'STRONG SELL';
  if (bear === 3) return 'SELL';
  return 'NEUTRAL';
}

/** Ensure verdict + summary exist when API omitted moderator fields. */
export function normalizeDebateResult(result) {
  if (!result) return null;
  const scores = deriveDebateScores(result);
  const verdict = (result.verdict && String(result.verdict).trim())
    ? result.verdict
    : heuristicDebateVerdict(scores.bull, scores.bear);
  const voteLine = `${scores.bull} bullish · ${scores.bear} bearish · ${scores.neutral} neutral`;
  const moderator_summary = result.moderator_summary?.trim()
    ? result.moderator_summary
    : `Committee vote split: ${voteLine}.`;
  return {
    ...result,
    verdict,
    moderator_summary,
    bull_score: scores.bull,
    bear_score: scores.bear,
    neutral_score: scores.neutral,
  };
}

