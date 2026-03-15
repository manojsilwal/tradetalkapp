import React, { useEffect, useState } from 'react';

/**
 * Displays a "badge unlocked" toast popup.
 * Usage: pass `badges` array — each time a new badge appears in the array,
 * a popup animates in for 4 seconds.
 */
export default function BadgePopup({ badges = [] }) {
    const [queue, setQueue]     = useState([]);
    const [current, setCurrent] = useState(null);
    const [visible, setVisible] = useState(false);
    const [seen, setSeen]       = useState(new Set());

    useEffect(() => {
        const newOnes = badges.filter(b => !seen.has(b.id));
        if (newOnes.length > 0) {
            setQueue(q => [...q, ...newOnes]);
            setSeen(s => new Set([...s, ...newOnes.map(b => b.id)]));
        }
    }, [badges]);

    useEffect(() => {
        if (!current && queue.length > 0) {
            setCurrent(queue[0]);
            setQueue(q => q.slice(1));
            setVisible(true);
            const t = setTimeout(() => {
                setVisible(false);
                setTimeout(() => setCurrent(null), 400);
            }, 4000);
            return () => clearTimeout(t);
        }
    }, [queue, current]);

    if (!current) return null;

    return (
        <div style={{
            position: 'fixed',
            bottom: 80,
            right: 24,
            zIndex: 9999,
            transform: visible ? 'translateY(0) scale(1)' : 'translateY(20px) scale(0.9)',
            opacity: visible ? 1 : 0,
            transition: 'transform 0.4s cubic-bezier(.34,1.56,.64,1), opacity 0.4s',
            pointerEvents: 'none',
        }}>
            <div style={{
                background: 'linear-gradient(135deg, rgba(124,58,237,0.95), rgba(16,185,129,0.95))',
                backdropFilter: 'blur(20px)',
                border: '1px solid rgba(255,255,255,0.2)',
                borderRadius: 16,
                padding: '14px 20px',
                minWidth: 240,
                boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
                display: 'flex',
                alignItems: 'center',
                gap: 14,
            }}>
                <span style={{ fontSize: 32, lineHeight: 1 }}>{current.icon || '🏆'}</span>
                <div>
                    <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.7)', fontWeight: 600, letterSpacing: 1, marginBottom: 2 }}>
                        BADGE UNLOCKED
                    </div>
                    <div style={{ fontSize: 14, color: '#fff', fontWeight: 700 }}>
                        {current.name}
                    </div>
                    <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.75)', marginTop: 2 }}>
                        {current.desc}
                    </div>
                </div>
            </div>
        </div>
    );
}
