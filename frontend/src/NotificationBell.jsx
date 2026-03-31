import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Bell, X, AlertTriangle, ShieldCheck, Clock } from 'lucide-react';
import { API_BASE_URL, BACKEND_RETRY_EVENT, apiFetch, notifyBackendUnreachable } from './api';

export default function NotificationBell() {
    const [alerts, setAlerts] = useState([]);
    const [unread, setUnread] = useState(0);
    const [showRecent, setShowRecent] = useState(false);
    const [toasts, setToasts] = useState([]);
    const [sseKey, setSseKey] = useState(0);
    const bellRef = useRef(null);

    const loadHistory = useCallback(() => {
        apiFetch(`${API_BASE_URL}/notifications/history`)
            .then(data => {
                setAlerts(data.alerts || []);
                setUnread(data.unread || 0);
            })
            .catch(() => { });
    }, []);

    useEffect(() => {
        loadHistory();
    }, [loadHistory]);

    useEffect(() => {
        const onRetry = () => {
            loadHistory();
            setSseKey((k) => k + 1);
        };
        window.addEventListener(BACKEND_RETRY_EVENT, onRetry);
        return () => window.removeEventListener(BACKEND_RETRY_EVENT, onRetry);
    }, [loadHistory]);

    // SSE for real-time push
    useEffect(() => {
        const sse = new EventSource(`${API_BASE_URL}/notifications/stream`);
        sse.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'connected') return;
                setAlerts(prev => [data, ...prev].slice(0, 30));
                setUnread(prev => prev + 1);
                const toastId = data.id + '_' + Date.now();
                setToasts(prev => [{ ...data, _toastId: toastId }, ...prev].slice(0, 4));
                setTimeout(() => setToasts(prev => prev.filter(t => t._toastId !== toastId)), 10000);
            } catch (e) { }
        };
        sse.onerror = () => {
            notifyBackendUnreachable();
        };
        return () => sse.close();
    }, [sseKey]);

    // Close dropdown on outside click
    useEffect(() => {
        if (!showRecent) return;
        const handler = (e) => {
            if (bellRef.current && !bellRef.current.contains(e.target)) setShowRecent(false);
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, [showRecent]);

    // Close dropdown on Escape key
    useEffect(() => {
        if (!showRecent) return;
        const handleKeyDown = (e) => {
            if (e.key === 'Escape') setShowRecent(false);
        };
        document.addEventListener('keydown', handleKeyDown);
        return () => document.removeEventListener('keydown', handleKeyDown);
    }, [showRecent]);

    const dismissAlert = async (id) => {
        await apiFetch(`${API_BASE_URL}/notifications/dismiss/${id}`, { method: 'POST' });
        setAlerts(prev => prev.map(a => a.id === id ? { ...a, is_read: true } : a));
        setUnread(prev => Math.max(0, prev - 1));
    };

    const urgencyColor = (u) => u >= 8 ? '#ef4444' : u >= 6 ? '#fb923c' : '#60a5fa';

    const timeAgo = (ts) => {
        const diff = Math.floor((Date.now() / 1000) - ts);
        if (diff < 60) return 'just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return `${Math.floor(diff / 86400)}d ago`;
    };

    return (
        <>
            {/* ── Bell Icon ── */}
            <div style={{ position: 'relative' }} ref={bellRef}>
                <button
                    onClick={() => {
                        const opening = !showRecent;
                        setShowRecent(opening);
                        if (opening) {
                            // User is viewing alerts — after 3s mark them as seen and clear from DB
                            setTimeout(async () => {
                                if (alerts.length > 0) {
                                    await apiFetch(`${API_BASE_URL}/notifications/mark-seen`, { method: 'POST' });
                                    setUnread(0);
                                }
                            }, 3000);
                        } else {
                            // Closing the panel — refetch fresh state from DB
                            apiFetch(`${API_BASE_URL}/notifications/history`)
                                .then(data => {
                                    setAlerts(data.alerts || []);
                                    setUnread(data.unread || 0);
                                })
                                .catch(() => { });
                        }
                    }}
                    aria-expanded={showRecent}
                    aria-haspopup="true"
                    aria-label={`Notifications${unread > 0 ? `, ${unread} unread` : ''}`}
                    style={{
                        background: showRecent ? 'rgba(255,255,255,0.1)' : 'transparent',
                        border: 'none', cursor: 'pointer', padding: '8px',
                        borderRadius: '10px', display: 'flex', alignItems: 'center',
                        position: 'relative', transition: 'background 0.2s',
                        color: 'var(--text-muted)'
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.08)'}
                    onMouseLeave={e => e.currentTarget.style.background = showRecent ? 'rgba(255,255,255,0.1)' : 'transparent'}
                >
                    <Bell size={20} />
                    {unread > 0 && (
                        <span style={{
                            position: 'absolute', top: '1px', right: '1px',
                            width: '16px', height: '16px', borderRadius: '50%',
                            background: '#ef4444', color: '#fff',
                            fontSize: '0.55rem', fontWeight: 700,
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            border: '2px solid rgba(15,15,30,1)',
                            animation: 'bellPulse 2s infinite'
                        }}>
                            {unread > 9 ? '9+' : unread}
                        </span>
                    )}
                </button>

                {/* ── Compact dropdown — positioned to RIGHT of bell ── */}
                {showRecent && (
                    <div style={{
                        position: 'absolute', top: '44px', left: '0',
                        width: '280px', maxHeight: '320px',
                        background: 'rgba(18,18,35,0.97)',
                        border: '1px solid rgba(255,255,255,0.08)',
                        borderRadius: '12px', overflow: 'hidden',
                        boxShadow: '0 12px 36px rgba(0,0,0,0.5)',
                        zIndex: 1000, backdropFilter: 'blur(16px)',
                    }}>
                        <div style={{
                            padding: '10px 14px', display: 'flex', alignItems: 'center',
                            borderBottom: '1px solid rgba(255,255,255,0.06)',
                            fontSize: '0.75rem', fontWeight: 600, color: '#fff'
                        }}>
                            <AlertTriangle size={13} color="#fb923c" style={{ marginRight: '6px' }} />
                            Macro Alerts
                            <span style={{ marginLeft: 'auto', fontSize: '0.65rem', color: 'var(--text-muted)', fontWeight: 400 }}>
                                {alerts.length}
                            </span>
                        </div>
                        <div style={{ overflowY: 'auto', maxHeight: '260px', padding: '6px' }}>
                            {alerts.length === 0 ? (
                                <div style={{ padding: '24px 12px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.75rem' }}>
                                    <Bell size={20} style={{ marginBottom: '6px', opacity: 0.3 }} />
                                    <p style={{ margin: 0 }}>No alerts yet</p>
                                    <p style={{ margin: '4px 0 0', fontSize: '0.65rem' }}>Scans every 60s</p>
                                </div>
                            ) : (
                                alerts.slice(0, 8).map(a => (
                                    <div key={a.id} style={{
                                        padding: '8px 10px', borderRadius: '8px', marginBottom: '3px',
                                        background: a.is_read ? 'transparent' : 'rgba(255,255,255,0.03)',
                                        opacity: a.is_read ? 0.5 : 1, fontSize: '0.75rem',
                                        display: 'flex', alignItems: 'flex-start', gap: '8px',
                                    }}>
                                        <div style={{
                                            width: '7px', height: '7px', borderRadius: '50%',
                                            background: urgencyColor(a.urgency), marginTop: '5px', flexShrink: 0
                                        }} />
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{
                                                fontWeight: 600, color: '#fff', lineHeight: 1.35,
                                                overflow: 'hidden', textOverflow: 'ellipsis',
                                                display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical'
                                            }}>{a.title}</div>
                                            <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginTop: '3px', display: 'flex', gap: '6px', alignItems: 'center' }}>
                                                <Clock size={9} /> {timeAgo(a.timestamp)}
                                                <span style={{ color: urgencyColor(a.urgency), fontWeight: 600, textTransform: 'uppercase', fontSize: '0.55rem' }}>{a.urgency_label}</span>
                                            </div>
                                        </div>
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                )}
            </div>

            {/* ── Toast Stack (bottom-right, non-blocking) ── */}
            <div style={{
                position: 'fixed', bottom: '20px', right: '20px',
                display: 'flex', flexDirection: 'column-reverse', gap: '10px',
                zIndex: 2000, pointerEvents: 'none',
            }}>
                {toasts.map(t => (
                    <div key={t._toastId} style={{
                        width: '340px', padding: '14px 16px',
                        background: 'rgba(18,18,35,0.97)',
                        border: `1px solid ${urgencyColor(t.urgency)}25`,
                        borderLeft: `3px solid ${urgencyColor(t.urgency)}`,
                        borderRadius: '12px',
                        boxShadow: '0 8px 30px rgba(0,0,0,0.4)',
                        backdropFilter: 'blur(16px)',
                        animation: 'toastSlideIn 0.3s ease-out',
                        pointerEvents: 'auto',
                    }}>
                        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
                            <AlertTriangle size={16} color={urgencyColor(t.urgency)} style={{ marginTop: '1px', flexShrink: 0 }} />
                            <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: '0.6rem', fontWeight: 600, textTransform: 'uppercase', color: urgencyColor(t.urgency), marginBottom: '3px' }}>
                                    🚨 Macro Alert
                                </div>
                                <div style={{
                                    fontSize: '0.8rem', fontWeight: 600, color: '#fff', lineHeight: 1.4,
                                    overflow: 'hidden', textOverflow: 'ellipsis',
                                    display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical'
                                }}>{t.title}</div>
                                <div style={{ display: 'flex', gap: '4px', marginTop: '6px', flexWrap: 'wrap' }}>
                                    {t.affected_sectors?.slice(0, 3).map(s => (
                                        <span key={s} style={{
                                            fontSize: '0.55rem', padding: '1px 7px', borderRadius: '8px',
                                            background: 'rgba(96,165,250,0.1)', color: '#60a5fa'
                                        }}>{s}</span>
                                    ))}
                                </div>
                            </div>
                            <button onClick={() => setToasts(prev => prev.filter(x => x._toastId !== t._toastId))} style={{
                                background: 'none', border: 'none', cursor: 'pointer',
                                color: 'var(--text-muted)', padding: '2px', flexShrink: 0
                            }}>
                                <X size={14} />
                            </button>
                        </div>
                    </div>
                ))}
            </div>

            <style>{`
                @keyframes toastSlideIn { from { transform: translateX(120%); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
                @keyframes bellPulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.15); } }
            `}</style>
        </>
    );
}
