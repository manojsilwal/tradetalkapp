/**
 * AuthGate — shown inside gamification tabs when the user isn't signed in.
 * Replaces the full-screen LoginScreen; the rest of the app stays accessible.
 */
import { useState } from 'react';
import { LogIn, Zap } from 'lucide-react';
import { GoogleLogin } from '@react-oauth/google';
import { useAuth } from '../AuthContext';
import { GOOGLE_CLIENT_ID } from '../api';

export default function AuthGate({ featureName = 'this feature', featureIcon = '🔒' }) {
    const { login }             = useAuth();
    const [error, setError]     = useState('');
    const [loading, setLoading] = useState(false);
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

    return (
        <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            minHeight: 400, padding: '40px 20px', textAlign: 'center',
        }}>
            <div style={{
                width: 64, height: 64, borderRadius: 16, marginBottom: 20,
                background: 'rgba(124,58,237,0.15)', border: '1px solid rgba(124,58,237,0.3)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 28,
            }}>
                {featureIcon}
            </div>

            <h2 style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', margin: '0 0 8px' }}>
                Sign in to use {featureName}
            </h2>
            <p style={{ fontSize: 14, color: '#64748b', maxWidth: 320, lineHeight: 1.6, margin: '0 0 28px' }}>
                Your progress, XP, streaks and portfolio are saved per account.
                All other analysis tools are free to use without signing in.
            </p>

            <div style={{
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: 16, padding: 24, width: '100%', maxWidth: 320,
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

                {isDevMode && (
                    <button
                        onClick={handleDevLogin}
                        disabled={loading}
                        style={{
                            width: '100%', padding: '12px 20px', borderRadius: 10,
                            border: 'none',
                            background: loading ? 'rgba(255,255,255,0.1)' : 'linear-gradient(135deg, #7c3aed, #a78bfa)',
                            color: '#fff', fontSize: 14, fontWeight: 700,
                            cursor: loading ? 'not-allowed' : 'pointer',
                            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
                        }}
                    >
                        <LogIn size={16} />
                        {loading ? 'Signing in...' : 'Sign in (Dev Mode)'}
                    </button>
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
    );
}
