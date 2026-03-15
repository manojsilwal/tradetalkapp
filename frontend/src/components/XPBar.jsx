import React, { useEffect, useState } from 'react';
import { API_BASE_URL } from '../api';

export default function XPBar() {
    const [prog, setProg] = useState(null);
    const [flash, setFlash] = useState(false);

    const fetchProgress = async () => {
        try {
            const res = await fetch(`${API_BASE_URL}/progress`);
            if (!res.ok) return;
            const data = await res.json();
            setProg(prev => {
                if (prev && data.xp > prev.xp) setFlash(true);
                return data;
            });
        } catch { /* backend might be loading */ }
    };

    useEffect(() => {
        fetchProgress();
        const interval = setInterval(fetchProgress, 30000);
        return () => clearInterval(interval);
    }, []);

    useEffect(() => {
        if (flash) {
            const t = setTimeout(() => setFlash(false), 1000);
            return () => clearTimeout(t);
        }
    }, [flash]);

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
            {/* Level + XP total */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#a78bfa', letterSpacing: 1 }}>
                    LVL {prog.level} · {prog.level_title}
                </span>
                <span style={{
                    fontSize: 10, color: streakColor, fontWeight: 600,
                    transition: 'color 0.3s',
                }}>
                    🔥 {prog.streak_days}d streak
                </span>
            </div>

            {/* XP progress bar */}
            <div style={{
                height: 5, borderRadius: 4,
                background: 'rgba(255,255,255,0.08)',
                overflow: 'hidden',
            }}>
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
                <span style={{ fontSize: 9, color: '#64748b' }}>
                    {prog.xp_in_level} XP
                </span>
                <span style={{ fontSize: 9, color: '#64748b' }}>
                    {prog.xp_for_level} to next level
                </span>
            </div>
        </div>
    );
}
