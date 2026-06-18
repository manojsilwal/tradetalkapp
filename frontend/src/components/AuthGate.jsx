/**
 * AuthGate — sign-up (Google) and sign-in (email + password + email OTP).
 */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { LogIn, Zap } from 'lucide-react';
import { GoogleLogin } from '@react-oauth/google';
import { useAuth } from '../AuthContext';
import SetPasswordForm from './SetPasswordForm';

const inputStyle = {
    padding: '10px 14px',
    borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.12)',
    background: 'rgba(0,0,0,0.2)',
    color: '#fff',
    fontSize: 13,
    outline: 'none',
};

export default function AuthGate({ featureName = 'this feature', featureIcon = '🔒' }) {
    const { googleSignup, login, loginManual, verifyOtp, user, isDevAuth } = useAuth();
    const navigate = useNavigate();

    const [step, setStep] = useState('auth'); // auth | set-password | otp
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);
    const [info, setInfo] = useState('');

    const [isSignUp, setIsSignUp] = useState(false);
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [otpCode, setOtpCode] = useState('');
    const [setupToken, setSetupToken] = useState('');
    const [signupEmail, setSignupEmail] = useState('');
    const [otpSessionId, setOtpSessionId] = useState('');

    useEffect(() => {
        if (user && !user.guest) {
            navigate('/portfolio');
        }
    }, [user, navigate]);

    const handleGoogleSignupSuccess = async (credentialResponse) => {
        setLoading(true);
        setError('');
        setInfo('');
        try {
            const data = await googleSignup(credentialResponse.credential);
            setSetupToken(data.setup_token);
            setSignupEmail(data.email);
            setStep('set-password');
        } catch (e) {
            if (e.status === 409) {
                setError(e.message || 'Account already exists.');
                setIsSignUp(false);
            } else {
                setError(e.message || 'Google signup failed.');
            }
        } finally {
            setLoading(false);
        }
    };

    const handleDevSignup = async () => {
        setLoading(true);
        setError('');
        try {
            const data = await googleSignup('dev');
            setSetupToken(data.setup_token);
            setSignupEmail(data.email);
            setStep('set-password');
        } catch (e) {
            if (e.status === 409) {
                setError('Dev account already has a password. Sign in instead.');
                setIsSignUp(false);
            } else {
                setError(e.message || 'Dev signup failed.');
            }
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
            setError(e.message || 'Dev login failed.');
        } finally {
            setLoading(false);
        }
    };

    const handlePasswordSetupComplete = ({ email: completedEmail }) => {
        setStep('auth');
        setIsSignUp(false);
        setEmail(completedEmail || signupEmail);
        setPassword('');
        setSetupToken('');
        setInfo('Account created. Sign in with your email and password.');
        setError('');
    };

    const handleSignInSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setInfo('');
        const emailTrim = email.trim().toLowerCase();
        if (!emailTrim || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailTrim)) {
            setError('Enter a valid email address.');
            return;
        }
        if (password.length < 6) {
            setError('Password must be at least 6 characters.');
            return;
        }
        setLoading(true);
        try {
            const data = await loginManual(emailTrim, password);
            setOtpSessionId(data.otp_session_id);
            setStep('otp');
            setInfo(
                data.otp_dev_bypass
                    ? 'Dev mode: enter any 6-digit code (check server logs for the real code).'
                    : `We sent a verification code to ${data.email}.`,
            );
        } catch (err) {
            const msg = err.message || 'Authentication failed';
            setError(msg.includes('HTTP') ? 'Could not reach the server. Try again.' : msg);
        } finally {
            setLoading(false);
        }
    };

    const handleOtpSubmit = async (e) => {
        e.preventDefault();
        setError('');
        if (!/^\d{6}$/.test(otpCode.trim())) {
            setError('Enter the 6-digit verification code.');
            return;
        }
        setLoading(true);
        try {
            await verifyOtp(otpSessionId, otpCode.trim());
        } catch (err) {
            setError(err.message || 'Verification failed.');
        } finally {
            setLoading(false);
        }
    };

    const switchTab = (signUp) => {
        setIsSignUp(signUp);
        setError('');
        setInfo('');
        setStep('auth');
    };

    return (
        <div style={{ position: 'relative', overflow: 'hidden', borderRadius: 16, border: '1px solid rgba(255,255,255,0.08)' }}>
            <div style={{
                position: 'absolute', inset: 0,
                filter: 'blur(6px)', opacity: 0.4, pointerEvents: 'none',
                padding: 24, overflow: 'hidden',
            }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
                    {[1, 2, 3].map((i) => (
                        <div key={i} style={{
                            background: 'rgba(255,255,255,0.03)', borderRadius: 12, padding: 20, height: 100,
                        }} />
                    ))}
                </div>
                <div style={{ marginTop: 20, height: 200, background: 'rgba(255,255,255,0.03)', borderRadius: 12 }} />
            </div>

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
                    {step === 'set-password' && (
                        <SetPasswordForm
                            setupToken={setupToken}
                            email={signupEmail}
                            onComplete={handlePasswordSetupComplete}
                        />
                    )}

                    {step === 'otp' && (
                        <form onSubmit={handleOtpSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                            <p style={{ color: '#94a3b8', fontSize: 13, margin: 0, textAlign: 'center' }}>
                                Enter the 6-digit code sent to your email.
                            </p>
                            <input
                                type="text"
                                inputMode="numeric"
                                placeholder="000000"
                                value={otpCode}
                                onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                                disabled={loading}
                                autoComplete="one-time-code"
                                aria-label="Verification code"
                                style={{ ...inputStyle, textAlign: 'center', letterSpacing: 6, fontSize: 18 }}
                            />
                            <button
                                type="submit"
                                disabled={loading}
                                style={{
                                    padding: '10px 20px', borderRadius: 8, border: 'none',
                                    background: loading ? 'rgba(255,255,255,0.1)' : 'linear-gradient(135deg, #7c3aed, #a78bfa)',
                                    color: '#fff', fontSize: 13, fontWeight: 700,
                                    cursor: loading ? 'not-allowed' : 'pointer',
                                }}
                            >
                                {loading ? 'Verifying...' : 'Verify & Sign In'}
                            </button>
                            <button
                                type="button"
                                onClick={() => { setStep('auth'); setOtpCode(''); setError(''); setInfo(''); }}
                                style={{
                                    padding: '8px', border: 'none', background: 'transparent',
                                    color: '#94a3b8', fontSize: 12, cursor: 'pointer',
                                }}
                            >
                                Back to sign in
                            </button>
                        </form>
                    )}

                    {step === 'auth' && (
                        <>
                            <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                                <button
                                    type="button"
                                    onClick={() => switchTab(false)}
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
                                    onClick={() => switchTab(true)}
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

                            {isSignUp ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                                    <p style={{ color: '#94a3b8', fontSize: 13, margin: 0, textAlign: 'center' }}>
                                        Create your account with Google, then set a password for sign-in.
                                    </p>
                                    {!isDevAuth ? (
                                        <div style={{ display: 'flex', justifyContent: 'center' }}>
                                            <GoogleLogin
                                                onSuccess={handleGoogleSignupSuccess}
                                                onError={() => setError('Google signup failed. Please try again.')}
                                                theme="filled_black"
                                                size="large"
                                                text="signup_with"
                                                shape="rectangular"
                                            />
                                        </div>
                                    ) : (
                                        <button
                                            type="button"
                                            onClick={handleDevSignup}
                                            disabled={loading}
                                            style={{
                                                width: '100%', padding: '10px 20px', borderRadius: 8, border: 'none',
                                                background: 'linear-gradient(135deg, #7c3aed, #a78bfa)',
                                                color: '#fff', fontSize: 13, fontWeight: 700, cursor: 'pointer',
                                            }}
                                        >
                                            Dev Sign Up (Google bypass)
                                        </button>
                                    )}
                                </div>
                            ) : (
                                <form onSubmit={handleSignInSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                                    <input
                                        type="email"
                                        placeholder="Email address"
                                        value={email}
                                        onChange={(e) => setEmail(e.target.value)}
                                        disabled={loading}
                                        autoComplete="email"
                                        aria-label="Email address"
                                        style={inputStyle}
                                    />
                                    <input
                                        type="password"
                                        placeholder="Password (min 6 chars)"
                                        value={password}
                                        onChange={(e) => setPassword(e.target.value)}
                                        disabled={loading}
                                        minLength={6}
                                        autoComplete="current-password"
                                        aria-label="Password"
                                        style={inputStyle}
                                    />
                                    <button
                                        type="submit"
                                        disabled={loading}
                                        style={{
                                            padding: '10px 20px', borderRadius: 8, border: 'none',
                                            background: loading ? 'rgba(255,255,255,0.1)' : 'linear-gradient(135deg, #7c3aed, #a78bfa)',
                                            color: '#fff', fontSize: 13, fontWeight: 700,
                                            cursor: loading ? 'not-allowed' : 'pointer',
                                        }}
                                    >
                                        {loading ? 'Sending code...' : 'Continue'}
                                    </button>
                                </form>
                            )}

                            {isDevAuth && (
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
                                            width: '100%', padding: '8px 12px', borderRadius: 8, border: 'none',
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
                        </>
                    )}

                    {info && <p style={{ marginTop: 10, fontSize: 12, color: '#94a3b8' }}>{info}</p>}
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
