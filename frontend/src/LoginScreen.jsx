import { useState } from 'react';
import { GoogleLogin } from '@react-oauth/google';
import { Activity, TrendingUp, Zap, BookOpen, Target } from 'lucide-react';
import { useAuth } from './AuthContext';
import { GOOGLE_CLIENT_ID, API_BASE_URL, apiFetch, setToken } from './api';

const FEATURES = [
    { icon: Zap,       label: 'Daily Challenges',   desc: 'Earn XP every day with market calls, debate duels & quizzes' },
    { icon: TrendingUp,label: 'Paper Portfolio',     desc: 'Track $10k virtual positions vs SPY with live P&L' },
    { icon: BookOpen,  label: 'Learning Path',       desc: '5-level curriculum from P/E basics to quant strategies' },
    { icon: Target,    label: 'AI Debate Arena',     desc: '5 AI agents argue both sides of any stock — you decide' },
];

export default function LoginScreen() {
    const { login }         = useAuth();
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const isDevMode = !GOOGLE_CLIENT_ID || GOOGLE_CLIENT_ID === 'PLACEHOLDER_SET_AFTER_GOOGLE_SETUP';

    const handleGoogleSuccess = async (credentialResponse) => {
        setLoading(true);
        setError('');
        try {
            await login(credentialResponse.credential);
        } catch (e) {
            setError(e.message || 'Login failed');
        } finally {
            setLoading(false);
        }
    };

    const handleDevLogin = async () => {
        setLoading(true);
        setError('');
        try {
            await login('dev');
        } catch (e) {
            setError(e.message || 'Dev login failed');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{
            minHeight: '100vh',
            background: 'linear-gradient(135deg, #0f0f1a 0%, #1a0f2e 50%, #0f1a1a 100%)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '20px',
            fontFamily: 'system-ui, -apple-system, sans-serif',
        }}>
            <div style={{ maxWidth: 480, width: '100%' }}>
                {/* Logo + headline */}
                <div style={{ textAlign: 'center', marginBottom: 40 }}>
                    <div style={{
                        width: 64, height: 64, borderRadius: 16, margin: '0 auto 16px',
                        background: 'linear-gradient(135deg, #7c3aed, #a78bfa)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                        boxShadow: '0 0 40px rgba(124,58,237,0.4)',
                    }}>
                        <Activity size={32} color="#fff" />
                    </div>
                    <h1 style={{ fontSize: 28, fontWeight: 800, color: '#fff', margin: '0 0 8px' }}>
                        K2-Optimus
                    </h1>
                    <p style={{ fontSize: 16, color: '#94a3b8', margin: 0 }}>
                        AI-powered investment intelligence platform
                    </p>
                </div>

                {/* Feature highlights */}
                <div style={{
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: 16,
                    padding: '24px',
                    marginBottom: 28,
                }}>
                    <p style={{ fontSize: 12, color: '#64748b', fontWeight: 700, letterSpacing: 1.5, marginBottom: 16, margin: '0 0 16px' }}>
                        WHAT YOU GET
                    </p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                        {FEATURES.map(f => (
                            <div key={f.label} style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                                <div style={{
                                    width: 36, height: 36, borderRadius: 10, flexShrink: 0,
                                    background: 'rgba(124,58,237,0.2)',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                }}>
                                    <f.icon size={16} color="#a78bfa" />
                                </div>
                                <div>
                                    <div style={{ fontSize: 14, fontWeight: 600, color: '#e2e8f0', marginBottom: 2 }}>
                                        {f.label}
                                    </div>
                                    <div style={{ fontSize: 12, color: '#64748b', lineHeight: 1.4 }}>
                                        {f.desc}
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

                {/* Login area */}
                <div style={{
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: 16,
                    padding: '24px',
                    textAlign: 'center',
                }}>
                    <p style={{ fontSize: 14, color: '#94a3b8', marginBottom: 20 }}>
                        Sign in to save your progress, XP, and portfolio
                    </p>

                    {/* Google Sign In button */}
                    {!isDevMode && (
                        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 16 }}>
                            <GoogleLogin
                                onSuccess={handleGoogleSuccess}
                                onError={() => setError('Google login failed. Please try again.')}
                                theme="filled_black"
                                size="large"
                                text="signin_with"
                                shape="rectangular"
                            />
                        </div>
                    )}

                    {/* Dev mode bypass */}
                    {isDevMode && (
                        <div>
                            <div style={{
                                marginBottom: 16, padding: '10px 14px', borderRadius: 8,
                                background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.3)',
                                fontSize: 12, color: '#f59e0b',
                            }}>
                                Dev mode active — Google Client ID not configured yet.
                                Add <code>VITE_GOOGLE_CLIENT_ID</code> to enable Google login.
                            </div>
                            <button
                                onClick={handleDevLogin}
                                disabled={loading}
                                style={{
                                    width: '100%', padding: '14px', borderRadius: 10,
                                    border: 'none',
                                    background: loading ? 'rgba(255,255,255,0.1)' : 'linear-gradient(135deg, #7c3aed, #a78bfa)',
                                    color: '#fff', fontSize: 15, fontWeight: 700,
                                    cursor: loading ? 'not-allowed' : 'pointer',
                                    transition: 'all 0.2s',
                                }}
                            >
                                {loading ? 'Signing in...' : 'Continue as Dev User'}
                            </button>
                        </div>
                    )}

                    {error && (
                        <div style={{ marginTop: 12, fontSize: 13, color: '#ef4444' }}>{error}</div>
                    )}

                    <p style={{ marginTop: 16, fontSize: 11, color: '#475569' }}>
                        Your data is stored privately and never shared.
                    </p>
                </div>
            </div>
        </div>
    );
}
