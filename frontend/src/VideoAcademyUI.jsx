import React, { useState, useEffect, useRef } from 'react';
import { Play, Eye, Zap, Film, ChevronRight, RefreshCw, CheckCircle2, Lock } from 'lucide-react';
import { API_BASE_URL } from './api';

const TRACK_COLORS = {
    'Value Investing':  { bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.3)', color: '#10b981' },
    'Market Structure': { bg: 'rgba(59,130,246,0.12)',  border: 'rgba(59,130,246,0.3)',  color: '#3b82f6' },
    'Quant Strategies': { bg: 'rgba(124,58,237,0.12)',  border: 'rgba(124,58,237,0.3)',  color: '#a78bfa' },
    'AI in Finance':    { bg: 'rgba(245,158,11,0.12)',  border: 'rgba(245,158,11,0.3)',  color: '#f59e0b' },
};

export default function VideoAcademyUI({ onXpGained }) {
    const [lessons, setLessons]         = useState([]);
    const [tracks, setTracks]           = useState([]);
    const [activeTrack, setActiveTrack] = useState('All');
    const [selected, setSelected]       = useState(null);
    const [generating, setGenerating]   = useState({});
    const [pollMap, setPollMap]         = useState({});
    const [loading, setLoading]         = useState(true);

    const fetchCatalogue = async () => {
        const res  = await fetch(`${API_BASE_URL}/academy/catalogue`);
        const data = await res.json();
        setLessons(data.lessons || []);
        const trackSet = ['All', ...new Set((data.lessons || []).map(l => l.track))];
        setTracks(trackSet);
        setLoading(false);
    };

    useEffect(() => { fetchCatalogue(); }, []);

    // Poll generating lessons
    useEffect(() => {
        const genIds = lessons.filter(l => l.status === 'generating').map(l => l.id);
        genIds.forEach(id => {
            if (!pollMap[id]) {
                const interval = setInterval(async () => {
                    const res  = await fetch(`${API_BASE_URL}/academy/lesson/${id}`);
                    const data = await res.json();
                    if (data.status !== 'generating') {
                        clearInterval(interval);
                        setPollMap(m => { const n = { ...m }; delete n[id]; return n; });
                        await fetchCatalogue();
                    }
                }, 5000);
                setPollMap(m => ({ ...m, [id]: interval }));
            }
        });
    }, [lessons]);

    const handleGenerate = async (lessonId) => {
        setGenerating(g => ({ ...g, [lessonId]: true }));
        try {
            await fetch(`${API_BASE_URL}/academy/lesson/${lessonId}/generate`, { method: 'POST' });
            await fetchCatalogue();
        } finally {
            setGenerating(g => { const n = { ...n, ...g }; delete n[lessonId]; return n; });
        }
    };

    const handleWatch = async (lesson) => {
        setSelected(lesson);
        if (!lesson.watched) {
            const res = await fetch(`${API_BASE_URL}/academy/lesson/${lesson.id}/watch`, { method: 'POST' });
            const data = await res.json();
            if (data.progress && onXpGained) onXpGained(data.progress);
            await fetchCatalogue();
        }
    };

    const filtered = activeTrack === 'All' ? lessons : lessons.filter(l => l.track === activeTrack);

    if (loading) return (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}>
            <div style={{ width: 40, height: 40, border: '3px solid rgba(255,255,255,0.1)', borderTopColor: '#a78bfa', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
        </div>
    );

    // Video player view
    if (selected) {
        return <VideoPlayer lesson={selected} onBack={() => setSelected(null)} onXpGained={onXpGained} />;
    }

    return (
        <div style={{ maxWidth: 900, margin: '0 auto', padding: '0 16px' }}>
            {/* Header */}
            <div style={{ marginBottom: 24 }}>
                <h2 style={{ margin: '0 0 4px', fontSize: 22, color: '#fff', display: 'flex', alignItems: 'center', gap: 10 }}>
                    <Film size={22} color="#a78bfa" /> AI Video Academy
                </h2>
                <p style={{ margin: 0, fontSize: 13, color: '#64748b' }}>
                    TikTok-style AI-generated lessons · {lessons.filter(l => l.watched).length}/{lessons.length} watched
                </p>
            </div>

            {/* Track filter */}
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 24 }}>
                {tracks.map(t => {
                    const tc = TRACK_COLORS[t] || {};
                    const isActive = activeTrack === t;
                    return (
                        <button
                            key={t}
                            onClick={() => setActiveTrack(t)}
                            style={{
                                padding: '7px 16px', borderRadius: 20, fontSize: 12, fontWeight: 600,
                                border: `1px solid ${isActive ? (tc.border || 'rgba(124,58,237,0.5)') : 'rgba(255,255,255,0.08)'}`,
                                background: isActive ? (tc.bg || 'rgba(124,58,237,0.15)') : 'rgba(255,255,255,0.03)',
                                color: isActive ? (tc.color || '#a78bfa') : '#64748b',
                                cursor: 'pointer',
                                transition: 'all 0.2s',
                            }}
                        >
                            {t}
                        </button>
                    );
                })}
            </div>

            {/* Lesson grid */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 16 }}>
                {filtered.map(lesson => (
                    <LessonCard
                        key={lesson.id}
                        lesson={lesson}
                        onWatch={() => handleWatch(lesson)}
                        onGenerate={() => handleGenerate(lesson.id)}
                        isGenerating={generating[lesson.id] || lesson.status === 'generating'}
                    />
                ))}
            </div>
        </div>
    );
}

function LessonCard({ lesson, onWatch, onGenerate, isGenerating }) {
    const tc   = TRACK_COLORS[lesson.track] || { bg: 'rgba(124,58,237,0.12)', color: '#a78bfa', border: 'rgba(124,58,237,0.3)' };
    const ready = lesson.status === 'ready';
    const failed = lesson.status === 'failed';

    return (
        <div style={{
            background: 'rgba(255,255,255,0.03)',
            border: `1px solid ${lesson.watched ? tc.border : 'rgba(255,255,255,0.08)'}`,
            borderRadius: 16,
            overflow: 'hidden',
            transition: 'transform 0.2s, border-color 0.2s',
            cursor: 'pointer',
            position: 'relative',
        }}
            onMouseEnter={e => e.currentTarget.style.transform = 'translateY(-2px)'}
            onMouseLeave={e => e.currentTarget.style.transform = 'none'}
        >
            {/* Thumbnail */}
            <div style={{
                height: 120,
                background: `linear-gradient(135deg, ${tc.bg.replace('0.12', '0.3')}, rgba(0,0,0,0.4))`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 48, position: 'relative',
            }}>
                {lesson.thumbnail || '🎬'}
                {lesson.watched && (
                    <div style={{
                        position: 'absolute', top: 8, right: 8,
                        background: 'rgba(0,0,0,0.6)', borderRadius: 20,
                        padding: '3px 8px', display: 'flex', alignItems: 'center', gap: 4,
                        fontSize: 10, color: '#10b981', fontWeight: 700,
                    }}>
                        <CheckCircle2 size={10} /> Watched
                    </div>
                )}
                {isGenerating && (
                    <div style={{
                        position: 'absolute', inset: 0,
                        background: 'rgba(0,0,0,0.6)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column',
                        gap: 8,
                    }}>
                        <RefreshCw size={24} color="#a78bfa" style={{ animation: 'spin 1s linear infinite' }} />
                        <span style={{ fontSize: 11, color: '#a78bfa', fontWeight: 600 }}>Generating...</span>
                    </div>
                )}
            </div>

            {/* Content */}
            <div style={{ padding: '14px 16px' }}>
                <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                    <span style={{
                        fontSize: 10, fontWeight: 700, letterSpacing: 0.5,
                        padding: '2px 8px', borderRadius: 10,
                        background: tc.bg, color: tc.color, border: `1px solid ${tc.border}`,
                    }}>
                        {lesson.track}
                    </span>
                    <span style={{
                        fontSize: 10, fontWeight: 600, color: '#64748b',
                        padding: '2px 8px', borderRadius: 10,
                        background: 'rgba(255,255,255,0.04)',
                    }}>
                        LVL {lesson.level}
                    </span>
                </div>

                <h4 style={{ margin: '0 0 10px', fontSize: 14, fontWeight: 700, color: '#e2e8f0', lineHeight: 1.3 }}>
                    {lesson.title}
                </h4>

                {/* Action button */}
                {ready ? (
                    <button onClick={onWatch} style={{
                        width: '100%', padding: '9px', borderRadius: 8, border: 'none',
                        background: `linear-gradient(135deg, ${tc.color}22, ${tc.color}44)`,
                        border: `1px solid ${tc.border}`,
                        color: tc.color, fontSize: 12, fontWeight: 700, cursor: 'pointer',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                    }}>
                        <Play size={12} fill={tc.color} /> {lesson.watched ? 'Rewatch' : 'Watch'}
                        <span style={{ marginLeft: 'auto', fontSize: 10, color: '#64748b' }}>
                            +35 XP
                        </span>
                    </button>
                ) : failed ? (
                    <button onClick={onGenerate} style={{
                        width: '100%', padding: '9px', borderRadius: 8,
                        border: '1px solid rgba(239,68,68,0.3)',
                        background: 'rgba(239,68,68,0.08)',
                        color: '#ef4444', fontSize: 12, fontWeight: 700, cursor: 'pointer',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                    }}>
                        <RefreshCw size={12} /> Retry Generation
                    </button>
                ) : isGenerating ? (
                    <div style={{ fontSize: 11, color: '#a78bfa', textAlign: 'center', padding: '8px 0' }}>
                        Generating video...
                    </div>
                ) : (
                    <button onClick={onGenerate} style={{
                        width: '100%', padding: '9px', borderRadius: 8,
                        border: '1px solid rgba(124,58,237,0.3)',
                        background: 'rgba(124,58,237,0.08)',
                        color: '#a78bfa', fontSize: 12, fontWeight: 700, cursor: 'pointer',
                        display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                    }}>
                        <Zap size={12} /> Generate Lesson
                    </button>
                )}
            </div>
        </div>
    );
}

function VideoPlayer({ lesson, onBack, onXpGained }) {
    const [scene, setScene]     = useState(0);
    const videoRef              = useRef(null);
    const playlist              = lesson.playlist || [];
    const tc                    = TRACK_COLORS[lesson.track] || { color: '#a78bfa', border: 'rgba(124,58,237,0.3)' };

    const goNext = () => setScene(s => Math.min(s + 1, playlist.length - 1));
    const goPrev = () => setScene(s => Math.max(s - 1, 0));

    const currentScene = playlist[scene];

    return (
        <div style={{ maxWidth: 480, margin: '0 auto', padding: '0 16px' }}>
            <button onClick={onBack} style={{ background: 'none', border: 'none', color: '#a78bfa', fontSize: 13, cursor: 'pointer', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 6 }}>
                ← Back to Academy
            </button>

            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 20, overflow: 'hidden', border: `1px solid ${tc.border}` }}>
                {/* Video area */}
                <div style={{ background: '#000', aspectRatio: '9/16', position: 'relative', maxHeight: 480 }}>
                    {currentScene ? (
                        <video
                            ref={videoRef}
                            key={currentScene.url}
                            src={`${API_BASE_URL.replace('/api', '')}${currentScene.url}`}
                            autoPlay
                            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                            onEnded={goNext}
                        />
                    ) : (
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
                            <div style={{ textAlign: 'center', color: '#64748b' }}>
                                <Film size={40} style={{ marginBottom: 12 }} />
                                <div style={{ fontSize: 13 }}>
                                    {lesson.status === 'ready' ? 'Select a scene' : 'Generate the lesson first'}
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Caption overlay */}
                    {currentScene?.caption && (
                        <div style={{
                            position: 'absolute', bottom: 16, left: 12, right: 12,
                            background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(8px)',
                            borderRadius: 8, padding: '8px 12px',
                            fontSize: 13, fontWeight: 500, color: '#fff', textAlign: 'center',
                        }}>
                            {currentScene.caption}
                        </div>
                    )}

                    {/* Scene counter */}
                    {playlist.length > 0 && (
                        <div style={{
                            position: 'absolute', top: 12, right: 12,
                            background: 'rgba(0,0,0,0.6)',
                            borderRadius: 20, padding: '4px 10px',
                            fontSize: 11, color: '#fff', fontWeight: 600,
                        }}>
                            {scene + 1}/{playlist.length}
                        </div>
                    )}
                </div>

                {/* Info */}
                <div style={{ padding: '16px 20px' }}>
                    <div style={{ fontSize: 10, color: tc.color, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>
                        {lesson.track} · Level {lesson.level}
                    </div>
                    <h3 style={{ margin: '0 0 12px', fontSize: 17, color: '#fff' }}>{lesson.title}</h3>

                    {/* Scene scrubber */}
                    {playlist.length > 0 && (
                        <div style={{ display: 'flex', gap: 4, marginBottom: 14 }}>
                            {playlist.map((_, i) => (
                                <button
                                    key={i}
                                    onClick={() => setScene(i)}
                                    style={{
                                        flex: 1, height: 4, borderRadius: 2, border: 'none', cursor: 'pointer',
                                        background: i === scene ? tc.color : i < scene ? tc.color + '66' : 'rgba(255,255,255,0.15)',
                                        padding: 0,
                                    }}
                                />
                            ))}
                        </div>
                    )}

                    {/* Controls */}
                    <div style={{ display: 'flex', gap: 10 }}>
                        <button onClick={goPrev} disabled={scene === 0} style={navBtn}>← Prev</button>
                        <button onClick={goNext} disabled={scene === playlist.length - 1} style={navBtn}>Next →</button>
                    </div>
                </div>
            </div>
        </div>
    );
}

const navBtn = {
    flex: 1, padding: '10px', borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.1)',
    background: 'rgba(255,255,255,0.05)',
    color: '#94a3b8', fontSize: 13, fontWeight: 600, cursor: 'pointer',
};
