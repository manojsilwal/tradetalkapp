import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { isChunkLoadError } from '../lazyWithRetry';

const CHUNK_RELOAD_KEY = 'tradetalk_chunk_reload';

/**
 * Catches render/lazy-import failures so routes show a recoverable panel
 * instead of a blank screen. ChunkLoadError triggers one automatic reload.
 */
export default class ErrorBoundary extends React.Component {
    constructor(props) {
        super(props);
        this.state = { error: null };
    }

    static getDerivedStateFromError(error) {
        return { error };
    }

    componentDidCatch(error, info) {
        console.error('[ErrorBoundary]', error, info?.componentStack);
        if (isChunkLoadError(error) && !sessionStorage.getItem(CHUNK_RELOAD_KEY)) {
            sessionStorage.setItem(CHUNK_RELOAD_KEY, '1');
            window.location.reload();
        }
    }

    handleReload = () => {
        sessionStorage.removeItem(CHUNK_RELOAD_KEY);
        window.location.reload();
    };

    render() {
        const { error } = this.state;
        if (!error) {
            return this.props.children;
        }

        const chunk = isChunkLoadError(error);

        return (
            <div style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                minHeight: '40vh',
                padding: '40px 20px',
                textAlign: 'center',
            }}>
                <AlertTriangle size={48} color="#f59e0b" style={{ marginBottom: 16 }} />
                <h2 style={{ fontSize: 20, fontWeight: 700, color: '#f8fafc', marginBottom: 8 }}>
                    {chunk ? 'App update available' : 'Something went wrong'}
                </h2>
                <p style={{ color: '#94a3b8', fontSize: 14, maxWidth: 420, marginBottom: 20 }}>
                    {chunk
                        ? 'A newer version of this page was deployed. Reload to fetch the latest bundle.'
                        : (error.message || 'An unexpected error occurred while loading this page.')}
                </p>
                <button
                    type="button"
                    onClick={this.handleReload}
                    style={{
                        padding: '10px 18px',
                        borderRadius: 8,
                        border: 'none',
                        background: 'linear-gradient(135deg, #7c3aed, #a78bfa)',
                        color: '#fff',
                        fontSize: 13,
                        fontWeight: 700,
                        cursor: 'pointer',
                    }}
                >
                    Reload page
                </button>
            </div>
        );
    }
}
