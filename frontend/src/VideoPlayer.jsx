import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, Film } from 'lucide-react';
import { API_BASE_URL } from './api';

const TRACK_COLORS = {
    'Value Investing':  { bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.3)', color: '#10b981' },
    'Market Structure': { bg: 'rgba(59,130,246,0.12)',  border: 'rgba(59,130,246,0.3)',  color: '#3b82f6' },
    'Quant Strategies': { bg: 'rgba(124,58,237,0.12)',  border: 'rgba(124,58,237,0.3)',  color: '#a78bfa' },
    'AI in Finance':    { bg: 'rgba(245,158,11,0.12)',  border: 'rgba(245,158,11,0.3)',  color: '#f59e0b' },
};

const staticOrigin = API_BASE_URL.replace('/api', '');

const navBtn = {
    flex: 1, padding: '10px', borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.1)',
    background: 'rgba(255,255,255,0.05)',
    color: '#94a3b8', fontSize: 13, fontWeight: 600, cursor: 'pointer',
};

export default function VideoPlayer({ lesson, onBack, onXpGained }) {
    const [scene, setScene]     = useState(0);
    const [playbackStarted, setPlaybackStarted] = useState(false);
    const videoRef              = useRef(null);
    const playlist              = lesson.playlist || [];
    const tc                    = TRACK_COLORS[lesson.track] || { color: '#a78bfa', border: 'rgba(124,58,237,0.3)' };

    const goNext = useCallback(() => {
        setScene(s => Math.min(s + 1, playlist.length - 1));
    }, [playlist.length]);
    const goPrev = () => setScene(s => Math.max(s - 1, 0));

    const currentScene = playlist[scene];
    const isTextFallback = currentScene && (currentScene.media === 'text_fallback' || (!currentScene.url && currentScene.fallback_body));
    const hasVideoUrl = !!(currentScene?.url && !isTextFallback);

    useEffect(() => {
        setPlaybackStarted(false);
    }, [scene]);

    useEffect(() => {
        if (!isTextFallback || !currentScene) return undefined;
        const sec = Math.min(60, Math.max(3, Number(currentScene.duration) || 4));
        const t = setTimeout(goNext, sec * 1000);
        return () => clearTimeout(t);
    }, [scene, isTextFallback, currentScene, goNext]);

    return (
        <div style={{ maxWidth: 480, margin: '0 auto', padding: '0 16px' }}>
            {onBack && (
                <button onClick={onBack} style={{ background: 'none', border: 'none', color: '#a78bfa', fontSize: 13, cursor: 'pointer', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 6 }}>
                    ← Back to Module
                </button>
            )}

            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 20, overflow: 'hidden', border: `1px solid ${tc.border}` }}>
                {/* Video area */}
                <div style={{ background: '#000', aspectRatio: '9/16', position: 'relative', maxHeight: 480 }}>
                    {currentScene && isTextFallback ? (
                        <div style={{
                            width: '100%', height: '100%', minHeight: 280,
                            display: 'flex', flexDirection: 'column', justifyContent: 'center',
                            alignItems: 'center', padding: 20,
                            background: 'linear-gradient(165deg, rgba(30,27,75,0.95), rgba(15,23,42,0.98))',
                        }}>
                            <div style={{
                                fontSize: 10, fontWeight: 700, letterSpacing: 1.2, color: tc.color, marginBottom: 12,
                                textTransform: 'uppercase',
                            }}>
                                Text slide · Veo unavailable
                            </div>
                            <p style={{
                                margin: 0, fontSize: 15, lineHeight: 1.55, color: '#e2e8f0', textAlign: 'center',
                                maxWidth: 360,
                            }}>
                                {currentScene.fallback_body}
                            </p>
                        </div>
                    ) : hasVideoUrl ? (
                        playbackStarted ? (
                            <video
                                ref={videoRef}
                                key={currentScene.url}
                                src={`${staticOrigin}${currentScene.url}`}
                                preload="none"
                                playsInline
                                autoPlay
                                style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                                onEnded={goNext}
                            />
                        ) : (
                            <button
                                type="button"
                                onClick={() => setPlaybackStarted(true)}
                                style={{
                                    position: 'absolute', inset: 0, border: 'none', padding: 0, margin: 0,
                                    cursor: 'pointer', display: 'flex', flexDirection: 'column',
                                    alignItems: 'center', justifyContent: 'center',
                                    background: `linear-gradient(165deg, ${tc.color}18, rgba(15,23,42,0.96))`,
                                }}
                                aria-label="Load and play video"
                            >
                                <div style={{
                                    position: 'absolute', inset: 0,
                                    background: 'rgba(0,0,0,0.5)',
                                }} />
                                <div style={{
                                    position: 'relative', zIndex: 1,
                                    width: 72, height: 72, borderRadius: '50%',
                                    background: 'rgba(0,0,0,0.88)',
                                    border: '2px solid rgba(255,255,255,0.92)',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                    boxShadow: '0 8px 32px rgba(0,0,0,0.55)',
                                }}>
                                    <Play size={34} color="#fff" fill="#fff" style={{ marginLeft: 6 }} />
                                </div>
                                <span style={{
                                    position: 'relative', zIndex: 1, marginTop: 16, fontSize: 12,
                                    color: '#94a3b8', fontWeight: 600,
                                }}>
                                    Tap to load &amp; play
                                </span>
                            </button>
                        )
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
