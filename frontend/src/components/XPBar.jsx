import React, { useEffect, useState } from 'react';
import { Zap } from 'lucide-react';
import { API_BASE_URL, apiFetch } from '../api';
import { useAuth } from '../AuthContext';

export default function XPBar() {
    const { user } = useAuth();
    const [prog, setProg]   = useState(null);
    const [flash, setFlash] = useState(false);

    const fetchProgress = async () => {
        if (!user) return;
        try {
            const data = await apiFetch(`${API_BASE_URL}/progress`);
            setProg(prev => {
                if (prev && data.xp > prev.xp) setFlash(true);
                return data;
            });
        } catch { /* token expired or backend loading */ }
    };

    useEffect(() => {
        fetchProgress();
        const interval = setInterval(fetchProgress, 30000);
        return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [user]);

    useEffect(() => {
        if (flash) {
            const t = setTimeout(() => setFlash(false), 1000);
            return () => clearTimeout(t);
        }
    }, [flash]);

    // Not logged in — show a subtle nudge
    if (!user) return (
        <div style={{
            margin: '8px 0 4px', padding: '8px 14px',
            background: 'rgba(124,58,237,0.06)',
            borderRadius: 10, border: '1px dashed rgba(124,58,237,0.2)',
            display: 'flex', alignItems: 'center', gap: 8,
        }}>
            <Zap size={12} color="#7c3aed" />
            <span style={{ fontSize: 11, color: '#64748b' }}>Sign in to track XP & streaks</span>
        </div>
    );

    if (!prog) return null;

    const streakColor = prog.streak_days >= 7 ? '#f59e0b' : prog.streak_days >= 3 ? '#fb923c' : '#64748b';

    return (
        <div style={{
            margin: '12px 0 4px',
            padding: '10px 14px',
            background: 'rgba(255,255,255,0.04)',
            borderRadius: '10px',
            border: '1px solid rgba(255,255,255,0.07)',
        }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#a78bfa', letterSpacing: 1 }}>
                    LVL {prog.level} · {prog.level_title}
                </span>
                <span style={{ fontSize: 10, color: streakColor, fontWeight: 600 }}>
                    🔥 {prog.streak_days}d streak
                </span>
            </div>
            <div style={{ height: 5, borderRadius: 4, background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
                <div style={{
                    height: '100%',
                    width: `${prog.xp_pct}%`,
                    background: flash
                        ? 'linear-gradient(90deg, #a78bfa, #34d399)'
                        : 'linear-gradient(90deg, #7c3aed, #a78bfa)',
                    borderRadius: 4,
                    transition: 'width 0.6s cubic-bezier(.4,0,.2,1), background 0.3s',
                }} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
                <span style={{ fontSize: 9, color: '#64748b' }}>{prog.xp_in_level} XP</span>
                <span style={{ fontSize: 9, color: '#64748b' }}>{prog.xp_for_level} to next level</span>
            </div>
        </div>
    );
}
