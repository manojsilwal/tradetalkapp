/**
 * AdminGate — restricts Developer suite pages to admin users only.
 */
import { useNavigate } from 'react-router-dom';
import { ShieldAlert } from 'lucide-react';
import { useAuth } from '../AuthContext';

export default function AdminGate({ children, featureName = 'this page' }) {
    const { user, loading } = useAuth();
    const navigate = useNavigate();

    if (loading) {
        return (
            <div style={{ padding: 40, textAlign: 'center', color: '#94a3b8' }}>
                Loading...
            </div>
        );
    }

    if (!user?.is_admin) {
        return (
            <div style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                minHeight: '50vh',
                padding: '40px 20px',
                textAlign: 'center',
            }}>
                <ShieldAlert size={48} color="#f59e0b" style={{ marginBottom: 16 }} />
                <h2 style={{ fontSize: 22, fontWeight: 700, color: '#f8fafc', marginBottom: 8 }}>
                    Admin access required
                </h2>
                <p style={{ color: '#94a3b8', fontSize: 14, maxWidth: 360, marginBottom: 20 }}>
                    {featureName} is limited to administrators.
                    {!user || user.guest
                        ? ' Sign in with an admin account to continue.'
                        : ' Your account does not have admin privileges.'}
                </p>
                <div style={{ display: 'flex', gap: 12 }}>
                    <button
                        type="button"
                        onClick={() => navigate('/')}
                        style={{
                            padding: '10px 18px', borderRadius: 8, border: 'none',
                            background: 'rgba(255,255,255,0.08)', color: '#e2e8f0',
                            fontSize: 13, fontWeight: 600, cursor: 'pointer',
                        }}
                    >
                        Go Home
                    </button>
                    {(!user || user.guest) && (
                        <button
                            type="button"
                            onClick={() => navigate('/login')}
                            style={{
                                padding: '10px 18px', borderRadius: 8, border: 'none',
                                background: 'linear-gradient(135deg, #7c3aed, #a78bfa)',
                                color: '#fff', fontSize: 13, fontWeight: 700, cursor: 'pointer',
                            }}
                        >
                            Sign In
                        </button>
                    )}
                </div>
            </div>
        );
    }

    return children;
}
