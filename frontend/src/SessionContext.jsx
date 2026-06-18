/**
 * SessionContext.jsx — React bridge for the sessionStore singleton.
 *
 * Provides:
 *   <SessionProvider> — wraps the app, hydrates store on mount, resumes running analyses
 *   useSession()      — returns { actions, runningCount, hasRunning }
 *   useSessionAction(id) — returns a single ActionRecord by ID
 *
 * Resume-on-mount:
 *   On hydration, any action with status="running" and resumable=true
 *   will be handed off to AnalysisContext.analyzeTicker to re-trigger.
 *   A toast notification confirms the resume.
 */

import {
    createContext,
    useContext,
    useState,
    useEffect,
    useCallback,
    useRef,
    useSyncExternalStore,
} from 'react';
import * as sessionStore from './store/sessionStore';

// ── Context ───────────────────────────────────────────────────────────────────
const SessionContext = createContext(null);

// ── Singleton subscribe/snapshot for useSyncExternalStore ────────────────────
const _getSnapshot = () => sessionStore.getSnapshot();
const _subscribe = (listener) => sessionStore.subscribe(listener);

// ── Toast state (simple global, avoids adding a full toast library) ───────────
let _toastListeners = new Set();
let _toasts = [];
let _toastId = 0;

export function addToast(message, type = 'info', durationMs = 4000) {
    const id = ++_toastId;
    const toast = { id, message, type, createdAt: Date.now() };
    _toasts = [..._toasts, toast];
    _toastListeners.forEach((fn) => fn());
    setTimeout(() => {
        _toasts = _toasts.filter((t) => t.id !== id);
        _toastListeners.forEach((fn) => fn());
    }, durationMs);
    return id;
}

function _getToastSnapshot() { return _toasts; }
function _subscribeToasts(listener) {
    _toastListeners.add(listener);
    return () => _toastListeners.delete(listener);
}

// ── Provider ──────────────────────────────────────────────────────────────────
export function SessionProvider({ children, onResume, shouldResumeAnalysis }) {
    const [hydrated, setHydrated] = useState(false);
    const resumedRef = useRef(new Set());

    useEffect(() => {
        let cancelled = false;
        async function init() {
            // Hydrate from IndexedDB
            const all = await sessionStore.hydrate();
            // Purge old entries (>6h)
            await sessionStore.purgeExpired();

            if (!cancelled) {
                setHydrated(true);

                // Resume any running actions from a previous session
                const running = all.filter(
                    (a) => a.status === 'running' && a.resumable && !resumedRef.current.has(a.id)
                );
                for (const action of running) {
                    resumedRef.current.add(action.id);
                    if (action.type === 'analysis' && action.meta?.ticker && onResume) {
                        const sym = action.meta.ticker.trim().toUpperCase();
                        if (shouldResumeAnalysis && !shouldResumeAnalysis(sym, action.id)) {
                            continue;
                        }
                        addToast(`Resuming ${sym} analysis…`, 'info', 5000);
                        // Slight delay so AnalysisContext is fully mounted
                        setTimeout(() => {
                            onResume(sym, action.id);
                        }, 500);
                    }
                }
            }
        }
        init();
        return () => { cancelled = true; };
    }, [onResume, shouldResumeAnalysis]);

    return (
        <SessionContext.Provider value={{ hydrated }}>
            {children}
            <ToastContainer />
        </SessionContext.Provider>
    );
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

/** Returns all session actions as an array plus convenience flags. */
export function useSession() {
    const ctx = useContext(SessionContext);
    if (!ctx) throw new Error('useSession must be used within <SessionProvider>');
    const actions = useSyncExternalStore(_subscribe, _getSnapshot);
    const runningCount = actions.filter((a) => a.status === 'running').length;
    const doneRecent = actions.filter((a) => a.status === 'done');
    const hasErrors = actions.some((a) => a.status === 'error');
    return {
        actions,
        runningCount,
        hasRunning: runningCount > 0,
        doneRecent,
        hasErrors,
        hydrated: ctx.hydrated,
    };
}

/** Returns a single ActionRecord by ID (live-updating). */
export function useSessionAction(id) {
    const actions = useSyncExternalStore(_subscribe, _getSnapshot);
    return actions.find((a) => a.id === id) ?? null;
}

/** Returns the toast list. */
export function useToasts() {
    return useSyncExternalStore(_subscribeToasts, _getToastSnapshot);
}

// ── Toast UI ──────────────────────────────────────────────────────────────────
const TOAST_COLORS = {
    info:    { bg: 'rgba(59,130,246,0.15)',  border: 'rgba(59,130,246,0.4)',  text: '#93c5fd' },
    success: { bg: 'rgba(16,185,129,0.15)',  border: 'rgba(16,185,129,0.4)',  text: '#6ee7b7' },
    error:   { bg: 'rgba(239,68,68,0.15)',   border: 'rgba(239,68,68,0.4)',   text: '#fca5a5' },
    warning: { bg: 'rgba(245,158,11,0.15)',  border: 'rgba(245,158,11,0.4)',  text: '#fcd34d' },
};

function ToastContainer() {
    const toasts = useToasts();
    if (toasts.length === 0) return null;
    return (
        <div style={{
            position: 'fixed',
            bottom: '100px',
            right: '24px',
            zIndex: 99999,
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            pointerEvents: 'none',
        }}>
            {toasts.map((toast) => {
                const colors = TOAST_COLORS[toast.type] || TOAST_COLORS.info;
                return (
                    <div
                        key={toast.id}
                        style={{
                            background: colors.bg,
                            border: `1px solid ${colors.border}`,
                            borderRadius: '10px',
                            padding: '10px 16px',
                            color: colors.text,
                            fontSize: '0.85rem',
                            fontWeight: 500,
                            maxWidth: '320px',
                            backdropFilter: 'blur(12px)',
                            boxShadow: '0 4px 20px rgba(0,0,0,0.4)',
                            animation: 'fadeIn 0.25s ease-out',
                        }}
                    >
                        {toast.message}
                    </div>
                );
            })}
        </div>
    );
}
