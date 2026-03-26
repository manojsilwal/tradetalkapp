import React, { useState, useEffect } from 'react';
import { Zap, CheckCircle2, XCircle, Clock, Trophy, Star } from 'lucide-react';
import { API_BASE_URL, apiFetch } from './api';

const TYPE_LABELS = { A: '📊 Market Call', B: '⚔️ Debate Duel', C: '🎓 Strategy Quiz' };
const TYPE_DESC   = {
    A: 'Predict which direction the market will move tomorrow',
    B: 'Pick your side in today\'s AI debate — bull or bear',
    C: 'Answer an investment knowledge question',
};

export default function DailyChallengeUI({ onXpGained }) {
    const [challenge, setChallenge] = useState(null);
    const [yesterday, setYesterday] = useState(null);
    const [selected, setSelected]   = useState(null);
    const [result, setResult]       = useState(null);
    const [loading, setLoading]     = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [error, setError]         = useState(null);

    useEffect(() => {
        Promise.all([
            apiFetch(`${API_BASE_URL}/challenge/today`),
            apiFetch(`${API_BASE_URL}/challenge/yesterday`).catch(() => null),
        ]).then(([today, yest]) => {
            setChallenge(today);
            setYesterday(yest);
            if (today.answered) {
                setSelected(today.user_answer);
                if (today.resolved) {
                    setResult({ resolved: true, correct: today.correct, xp_awarded: today.xp_awarded });
                }
            }
        }).catch(err => {
            setError('Failed to load daily challenge. Please try again.');
            console.error('[DailyChallenge] fetch error:', err);
        }).finally(() => setLoading(false));
    }, []);

    const handleSubmit = async () => {
        if (selected === null || submitting) return;
        setSubmitting(true);
        try {
            const data = await apiFetch(`${API_BASE_URL}/challenge/answer`, {
                method: 'POST',
                body: JSON.stringify({ answer: String(selected) }),
            });
            setResult(data);
            if (data.progress && onXpGained) {
                onXpGained(data.progress);
            }
            // Refresh challenge state
            const updated = await apiFetch(`${API_BASE_URL}/challenge/today`);
            setChallenge(updated);
        } finally {
            setSubmitting(false);
        }
    };

    if (loading) return (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
            <div className="spinner" style={{ width: 40, height: 40, border: '3px solid rgba(255,255,255,0.1)', borderTopColor: '#a78bfa', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
        </div>
    );

    if (error) return (
        <div style={{ maxWidth: 640, margin: '0 auto', padding: '0 16px' }}>
            <div style={{ padding: '16px 20px', borderRadius: 12, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', color: '#ef4444', fontSize: 14, textAlign: 'center' }}>
                {error}
            </div>
        </div>
    );

    const timeUntilMidnight = () => {
        const now   = new Date();
        const end   = new Date(now);
        end.setHours(24, 0, 0, 0);
        const diff  = end - now;
        const h = Math.floor(diff / 3600000);
        const m = Math.floor((diff % 3600000) / 60000);
        return `${h}h ${m}m`;
    };

    return (
        <div style={{ maxWidth: 640, margin: '0 auto', padding: '0 16px' }}>
            {/* Yesterday's result */}
            {yesterday && yesterday.resolved && (
                <div style={{
                    marginBottom: 20,
                    padding: '14px 18px',
                    borderRadius: 12,
                    background: yesterday.correct
                        ? 'rgba(16,185,129,0.08)'
                        : 'rgba(239,68,68,0.08)',
                    border: `1px solid ${yesterday.correct ? 'rgba(16,185,129,0.2)' : 'rgba(239,68,68,0.2)'}`,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                }}>
                    {yesterday.correct
                        ? <CheckCircle2 size={20} color="#10b981" />
                        : <XCircle size={20} color="#ef4444" />}
                    <div>
                        <span style={{ fontSize: 13, fontWeight: 600, color: yesterday.correct ? '#10b981' : '#ef4444' }}>
                            Yesterday: {yesterday.correct ? `+${yesterday.xp_awarded} XP!` : 'Not this time'}
                        </span>
                        <span style={{ fontSize: 12, color: '#64748b', marginLeft: 8 }}>
                            {yesterday.challenge?.title}
                        </span>
                    </div>
                </div>
            )}

            {/* Header */}
            <div style={{
                background: 'linear-gradient(135deg, rgba(124,58,237,0.15), rgba(16,185,129,0.08))',
                border: '1px solid rgba(124,58,237,0.2)',
                borderRadius: 16,
                padding: '20px 24px',
                marginBottom: 20,
            }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div>
                        <div style={{ fontSize: 11, color: '#a78bfa', fontWeight: 700, letterSpacing: 1.5, marginBottom: 4 }}>
                            DAILY CHALLENGE
                        </div>
                        <div style={{ fontSize: 22, fontWeight: 800, color: '#fff', marginBottom: 4 }}>
                            {TYPE_LABELS[challenge?.type] || 'Today\'s Challenge'}
                        </div>
                        <div style={{ fontSize: 13, color: '#94a3b8' }}>
                            {TYPE_DESC[challenge?.type]}
                        </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#f59e0b', marginBottom: 6 }}>
                            <Zap size={14} />
                            <span style={{ fontSize: 13, fontWeight: 700 }}>+{challenge?.xp_reward || 30} XP</span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#64748b', fontSize: 11 }}>
                            <Clock size={12} />
                            <span>Resets in {timeUntilMidnight()}</span>
                        </div>
                    </div>
                </div>
            </div>

            {/* Question */}
            <div style={{
                background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: 14,
                padding: '20px 24px',
                marginBottom: 16,
            }}>
                <p style={{ fontSize: 16, fontWeight: 600, color: '#e2e8f0', lineHeight: 1.5, margin: 0 }}>
                    {challenge?.prompt}
                </p>
            </div>

            {/* Options */}
            {challenge && !challenge.answered && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 20 }}>
                    {challenge.options?.map((opt, i) => {
                        const isSelected = selected === (challenge.kind === 'quiz' ? String(i) : opt);
                        return (
                            <button
                                key={i}
                                onClick={() => setSelected(challenge.kind === 'quiz' ? String(i) : opt)}
                                style={{
                                    padding: '14px 20px',
                                    borderRadius: 12,
                                    border: `1px solid ${isSelected ? 'rgba(124,58,237,0.6)' : 'rgba(255,255,255,0.08)'}`,
                                    background: isSelected ? 'rgba(124,58,237,0.2)' : 'rgba(255,255,255,0.03)',
                                    color: isSelected ? '#a78bfa' : '#94a3b8',
                                    fontSize: 14,
                                    fontWeight: isSelected ? 700 : 400,
                                    cursor: 'pointer',
                                    textAlign: 'left',
                                    transition: 'all 0.2s',
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 10,
                                }}
                            >
                                <span style={{
                                    width: 24, height: 24, borderRadius: '50%',
                                    border: `1px solid ${isSelected ? '#a78bfa' : 'rgba(255,255,255,0.2)'}`,
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                    fontSize: 11, fontWeight: 700, flexShrink: 0,
                                    background: isSelected ? 'rgba(124,58,237,0.4)' : 'transparent',
                                    color: isSelected ? '#a78bfa' : '#64748b',
                                }}>
                                    {String.fromCharCode(65 + i)}
                                </span>
                                {opt}
                            </button>
                        );
                    })}
                </div>
            )}

            {/* Submit */}
            {challenge && !challenge.answered && (
                <button
                    onClick={handleSubmit}
                    disabled={selected === null || submitting}
                    style={{
                        width: '100%',
                        padding: '14px',
                        borderRadius: 12,
                        border: 'none',
                        background: selected !== null
                            ? 'linear-gradient(135deg, #7c3aed, #a78bfa)'
                            : 'rgba(255,255,255,0.05)',
                        color: selected !== null ? '#fff' : '#64748b',
                        fontSize: 15,
                        fontWeight: 700,
                        cursor: selected !== null ? 'pointer' : 'not-allowed',
                        transition: 'all 0.2s',
                    }}
                >
                    {submitting ? 'Submitting...' : 'Submit Answer'}
                </button>
            )}

            {/* Result */}
            {result && result.resolved && (
                <div style={{
                    marginTop: 20,
                    padding: '20px 24px',
                    borderRadius: 14,
                    background: result.correct
                        ? 'rgba(16,185,129,0.1)'
                        : 'rgba(239,68,68,0.1)',
                    border: `1px solid ${result.correct ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)'}`,
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                        {result.correct
                            ? <CheckCircle2 size={24} color="#10b981" />
                            : <XCircle size={24} color="#ef4444" />}
                        <span style={{ fontSize: 18, fontWeight: 800, color: result.correct ? '#10b981' : '#ef4444' }}>
                            {result.correct ? `+${result.xp_awarded} XP!` : 'Incorrect'}
                        </span>
                    </div>
                    {result.explanation && (
                        <p style={{ fontSize: 13, color: '#94a3b8', lineHeight: 1.6, margin: 0 }}>
                            {result.explanation}
                        </p>
                    )}
                </div>
            )}

            {result && result.pending && (
                <div style={{
                    marginTop: 20,
                    padding: '16px 20px',
                    borderRadius: 12,
                    background: 'rgba(59,130,246,0.08)',
                    border: '1px solid rgba(59,130,246,0.2)',
                    display: 'flex', gap: 12, alignItems: 'center',
                }}>
                    <Clock size={20} color="#3b82f6" />
                    <div>
                        <div style={{ fontSize: 14, fontWeight: 600, color: '#3b82f6', marginBottom: 2 }}>
                            Answer recorded!
                        </div>
                        <div style={{ fontSize: 12, color: '#64748b' }}>
                            {result.message}
                        </div>
                    </div>
                </div>
            )}

            {/* Already answered state */}
            {challenge?.answered && !result && (
                <div style={{
                    padding: '16px 20px',
                    borderRadius: 12,
                    background: 'rgba(100,116,139,0.08)',
                    border: '1px solid rgba(100,116,139,0.2)',
                    textAlign: 'center',
                }}>
                    <Trophy size={20} color="#f59e0b" style={{ marginBottom: 8 }} />
                    <div style={{ fontSize: 14, color: '#94a3b8' }}>
                        You've completed today's challenge!
                        {challenge.resolved
                            ? <span style={{ color: challenge.correct ? '#10b981' : '#ef4444', marginLeft: 6 }}>
                                {challenge.correct ? `+${challenge.xp_awarded} XP earned` : 'Better luck tomorrow!'}
                              </span>
                            : <span style={{ color: '#64748b', marginLeft: 6 }}>Results tomorrow.</span>}
                    </div>
                </div>
            )}
        </div>
    );
}
