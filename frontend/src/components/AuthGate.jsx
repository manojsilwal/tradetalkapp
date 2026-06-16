/**
 * AuthGate — shown inside gamification tabs when the user isn't signed in.
 * Replaces the full-screen LoginScreen; the rest of the app stays accessible.
 */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogIn, Zap } from 'lucide-react';
import { GoogleLogin } from '@react-oauth/google';
import { useAuth } from '../AuthContext';
import { GOOGLE_CLIENT_ID } from '../api';

export default function AuthGate({ featureName = 'this feature', featureIcon = '🔒' }) {
    const { login, loginManual, signup, user } = useAuth();
    const navigate = useNavigate();

    useEffect(() => {
        if (user && !user.guest) {
            navigate('/portfolio');
        }
    }, [user, navigate]);
    const [error, setError]     = useState('');
    const [loading, setLoading] = useState(false);
    
    // Manual credentials states
    const [isSignUp, setIsSignUp] = useState(false);
    const [email, setEmail]       = useState('');
    const [password, setPassword] = useState('');
    const [name, setName]         = useState('');

    const isDevMode = !GOOGLE_CLIENT_ID || GOOGLE_CLIENT_ID === 'PLACEHOLDER_SET_AFTER_GOOGLE_SETUP';

    const handleGoogleSuccess = async (credentialResponse) => {
        setLoading(true);
        setError('');
        try { await login(credentialResponse.credential); }
        catch (e) { setError(e.message || 'Login failed'); }
        finally   { setLoading(false); }
    };

    const handleDevLogin = async () => {
        setLoading(true);
        setError('');
        try { await login('dev'); }
        catch (e) { setError(e.message || 'Dev login failed'); }
        finally   { setLoading(false); }
    };

    const handleManualSubmit = async (e) => {
        e.preventDefault();
        setLoading(true);
        setError('');
        try {
            if (isSignUp) {
                await signup(email, password, name);
            } else {
                await loginManual(email, password);
            }
        } catch (err) {
            setError(err.message || 'Authentication failed');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{ position: 'relative', overflow: 'hidden', borderRadius: 16, border: '1px solid rgba(255,255,255,0.08)' }}>
            {/* Blurred preview */}
            <div style={{
                position: 'absolute', inset: 0,
                filter: 'blur(6px)', opacity: 0.4, pointerEvents: 'none',
                padding: 24, overflow: 'hidden',
            }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
                    {[1,2,3].map(i => (
                        <div key={i} style={{
                            background: 'rgba(255,255,255,0.03)', borderRadius: 12, padding: 20, height: 100,
                        }} />
                    ))}
                </div>
                <div style={{ marginTop: 20, height: 200, background: 'rgba(255,255,255,0.03)', borderRadius: 12 }} />
            </div>

            {/* Overlay CTA */}
            <div style={{
                position: 'relative', display: 'flex', flexDirection: 'column',
                alignItems: 'center', justifyContent: 'center',
                background: 'linear-gradient(180deg, rgba(15,17,26,0.3), rgba(15,17,26,0.9))',
                padding: '40px 20px',
                width: '100%',
                boxSizing: 'border-box',
                zIndex: 1,
            }}>
                <div style={{ fontSize: 40, marginBottom: 16 }}>{featureIcon}</div>
                <h3 style={{ fontSize: 20, fontWeight: 700, marginBottom: 8, color: '#f8fafc' }}>
                    Unlock {featureName}
                </h3>
                <p style={{ color: '#94a3b8', fontSize: 14, marginBottom: 20, maxWidth: 300, textAlign: 'center' }}>
                    Sign in to access {featureName.toLowerCase()}, earn XP, and track your progress.
                </p>

                <div style={{
                    background: 'transparent',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: 16, padding: 24, width: '100%', maxWidth: 320,
                    boxSizing: 'border-box',
                }}>
                    {!isDevMode && (
                        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 12 }}>
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

                    {!isDevMode && (
                        <div style={{ display: 'flex', alignItems: 'center', margin: '16px 0', gap: 8 }}>
                            <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.08)' }} />
                            <span style={{ fontSize: 11, color: '#64748b' }}>or email</span>
                            <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.08)' }} />
                        </div>
                    )}

                    {/* Tabs */}
                    <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                        <button
                            type="button"
                            onClick={() => { setIsSignUp(false); setError(''); }}
                            style={{
                                flex: 1, padding: '6px 12px', borderRadius: 8, border: 'none',
                                background: !isSignUp ? 'rgba(124,58,237,0.15)' : 'transparent',
                                color: !isSignUp ? '#e9d5ff' : '#94a3b8',
                                fontSize: 12, fontWeight: 600, cursor: 'pointer',
                            }}
                        >
                            Sign In
                        </button>
                        <button
                            type="button"
                            onClick={() => { setIsSignUp(true); setError(''); }}
                            style={{
                                flex: 1, padding: '6px 12px', borderRadius: 8, border: 'none',
                                background: isSignUp ? 'rgba(124,58,237,0.15)' : 'transparent',
                                color: isSignUp ? '#e9d5ff' : '#94a3b8',
                                fontSize: 12, fontWeight: 600, cursor: 'pointer',
                            }}
                        >
                            Sign Up
                        </button>
                    </div>

                    <form onSubmit={handleManualSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                        {isSignUp && (
                            <input
                                type="text"
                                placeholder="Your Name"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                required
                                style={{
                                    padding: '10px 14px', borderRadius: 8,
                                    border: '1px solid rgba(255,255,255,0.12)',
                                    background: 'rgba(0,0,0,0.2)',
                                    color: '#fff', fontSize: 13, outline: 'none',
                                }}
                            />
                        )}
                        <input
                            type="email"
                            placeholder="Email address"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            required
                            style={{
                                padding: '10px 14px', borderRadius: 8,
                                border: '1px solid rgba(255,255,255,0.12)',
                                background: 'rgba(0,0,0,0.2)',
                                color: '#fff', fontSize: 13, outline: 'none',
                            }}
                        />
                        <input
                            type="password"
                            placeholder="Password (min 6 chars)"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            required
                            minLength={6}
                            style={{
                                padding: '10px 14px', borderRadius: 8,
                                border: '1px solid rgba(255,255,255,0.12)',
                                background: 'rgba(0,0,0,0.2)',
                                color: '#fff', fontSize: 13, outline: 'none',
                            }}
                        />
                        <button
                            type="submit"
                            disabled={loading}
                            style={{
                                padding: '10px 20px', borderRadius: 8, border: 'none',
                                background: loading ? 'rgba(255,255,255,0.1)' : 'linear-gradient(135deg, #7c3aed, #a78bfa)',
                                color: '#fff', fontSize: 13, fontWeight: 700, cursor: loading ? 'not-allowed' : 'pointer',
                                transition: 'all 0.2s', marginTop: 4,
                            }}
                        >
                            {loading ? (isSignUp ? 'Creating account...' : 'Signing in...') : (isSignUp ? 'Sign Up' : 'Sign In')}
                        </button>
                    </form>

                    {isDevMode && (
                        <>
                            <div style={{ display: 'flex', alignItems: 'center', margin: '12px 0', gap: 8 }}>
                                <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.08)' }} />
                                <span style={{ fontSize: 10, color: '#64748b' }}>or bypass</span>
                                <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.08)' }} />
                            </div>
                            <button
                                type="button"
                                onClick={handleDevLogin}
                                disabled={loading}
                                style={{
                                    width: '100%', padding: '8px 12px', borderRadius: 8,
                                    border: 'none',
                                    background: 'rgba(255,255,255,0.05)',
                                    color: '#a78bfa', fontSize: 12, fontWeight: 600,
                                    cursor: loading ? 'not-allowed' : 'pointer',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                                }}
                            >
                                <LogIn size={12} />
                                Dev Mode Bypass
                            </button>
                        </>
                    )}

                    {error && <p style={{ marginTop: 10, fontSize: 12, color: '#ef4444' }}>{error}</p>}

                    <div style={{
                        marginTop: 14, padding: '8px 12px', borderRadius: 8,
                        background: 'rgba(124,58,237,0.08)', border: '1px solid rgba(124,58,237,0.15)',
                        display: 'flex', alignItems: 'center', gap: 8,
                    }}>
                        <Zap size={12} color="#a78bfa" />
                        <span style={{ fontSize: 11, color: '#94a3b8' }}>
                            Earn XP, badges, and track your streak
                        </span>
                    </div>
                </div>
            </div>
        </div>
    );
}
