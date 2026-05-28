import { TrendingUp, ShieldAlert, Globe, Zap } from 'lucide-react';

export const DEBATE_AGENTS = [
  { role: 'bull', label: 'Bull Analyst', Icon: TrendingUp, color: '#10b981', bg: 'rgba(16,185,129,0.08)' },
  { role: 'bear', label: 'Bear Analyst', Icon: ShieldAlert, color: '#ef4444', bg: 'rgba(239,68,68,0.08)' },
  { role: 'macro', label: 'Macro Economist', Icon: Globe, color: '#3b82f6', bg: 'rgba(59,130,246,0.08)' },
  { role: 'value', label: 'Value Investor', Icon: null, color: '#f59e0b', bg: 'rgba(245,158,11,0.08)' },
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

