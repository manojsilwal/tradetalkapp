import { useState } from 'react';
import { useAuth } from '../AuthContext';

const inputStyle = {
    padding: '10px 14px',
    borderRadius: 8,
    border: '1px solid rgba(255,255,255,0.12)',
    background: 'rgba(0,0,0,0.2)',
    color: '#fff',
    fontSize: 13,
    outline: 'none',
};

export default function SetPasswordForm({ setupToken, email, onComplete }) {
    const { setPassword } = useAuth();
    const [password, setPasswordValue] = useState('');
    const [confirm, setConfirm] = useState('');
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        if (password.length < 6) {
            setError('Password must be at least 6 characters.');
            return;
        }
        if (password !== confirm) {
            setError('Passwords do not match.');
            return;
        }
        setLoading(true);
        try {
            await setPassword(setupToken, password);
            onComplete?.({ email });
        } catch (err) {
            setError(err.message || 'Could not set password.');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div>
            <p style={{ color: '#94a3b8', fontSize: 13, marginBottom: 16, textAlign: 'center' }}>
                Set a password for <strong style={{ color: '#e2e8f0' }}>{email}</strong> to complete signup.
            </p>
            <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <input
                    type="password"
                    placeholder="Password (min 6 chars)"
                    value={password}
                    onChange={(e) => setPasswordValue(e.target.value)}
                    disabled={loading}
                    minLength={6}
                    autoComplete="new-password"
                    aria-label="Password"
                    style={inputStyle}
                />
                <input
                    type="password"
                    placeholder="Confirm password"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    disabled={loading}
                    minLength={6}
                    autoComplete="new-password"
                    aria-label="Confirm password"
                    style={inputStyle}
                />
                <button
                    type="submit"
                    disabled={loading}
                    style={{
                        padding: '10px 20px',
                        borderRadius: 8,
                        border: 'none',
                        background: loading ? 'rgba(255,255,255,0.1)' : 'linear-gradient(135deg, #7c3aed, #a78bfa)',
                        color: '#fff',
                        fontSize: 13,
                        fontWeight: 700,
                        cursor: loading ? 'not-allowed' : 'pointer',
                    }}
                >
                    {loading ? 'Saving...' : 'Create Account'}
                </button>
            </form>
            {error && <p style={{ marginTop: 10, fontSize: 12, color: '#ef4444' }}>{error}</p>}
        </div>
    );
}
