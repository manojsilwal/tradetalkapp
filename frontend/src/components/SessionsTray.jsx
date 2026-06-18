/**
 * SessionsTray.jsx — Persistent multi-session floating widget.
 *
 * Replaces GlobalLoadingBar. Shows all active and recently completed
 * session actions regardless of which page the user is on.
 *
 * Behaviors:
 * - Shows as a collapsed pill (badge count) when minimized
 * - Expands into a panel showing each action's status, progress, step
 * - Individual cancel buttons per running action
 * - Click a completed action to navigate to its result
 * - Dismiss (X) on completed/error/cancelled actions
 * - Auto-hides when there are zero actions
 */

import React, { useState, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Loader2, CheckCircle2, XCircle, X, ChevronUp, ChevronDown, AlertTriangle, RefreshCw } from 'lucide-react';
import { useSession } from '../SessionContext';
import * as sessionStore from '../store/sessionStore';

// Pages where the tray should not render (they have their own progress UI)
const HIDDEN_PATHS = new Set(['/login']);

const STATUS_ICON = {
    running:   (size) => <Loader2 size={size} style={{ animation: 'spin 1.2s linear infinite', color: '#3b82f6' }} />,
    done:      (size) => <CheckCircle2 size={size} style={{ color: '#10b981' }} />,
    error:     (size) => <AlertTriangle size={size} style={{ color: '#ef4444' }} />,
    cancelled: (size) => <XCircle size={size} style={{ color: '#94a3b8' }} />,
};

const STATUS_COLOR = {
    running:   '#3b82f6',
    done:      '#10b981',
    error:     '#ef4444',
    cancelled: '#64748b',
};

function ProgressBar({ value, color }) {
    return (
        <div style={{
            height: '3px',
            background: 'rgba(255,255,255,0.07)',
            borderRadius: '2px',
            overflow: 'hidden',
            marginTop: '6px',
        }}>
            <div style={{
                height: '100%',
                width: `${Math.min(100, value)}%`,
                background: color,
                borderRadius: '2px',
                transition: 'width 0.4s ease',
            }} />
        </div>
    );
}

function ActionRow({ action, onCancel, onDismiss, onNavigate }) {
    const isRunning = action.status === 'running';
    const isDone = action.status === 'done';
    const isError = action.status === 'error';
    const color = STATUS_COLOR[action.status] || '#94a3b8';

    const elapsed = action.completedAt
        ? Math.round((Date.now() - action.completedAt) / 60000)
        : Math.round((Date.now() - action.createdAt) / 60000);

    return (
        <div style={{
            padding: '10px 14px',
            borderBottom: '1px solid rgba(255,255,255,0.05)',
            cursor: (isDone && onNavigate) ? 'pointer' : 'default',
        }}
            onClick={() => isDone && onNavigate && onNavigate(action)}
        >
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1, minWidth: 0 }}>
                    {STATUS_ICON[action.status]?.(14)}
                    <span style={{
                        color: '#f1f5f9',
                        fontSize: '0.82rem',
                        fontWeight: 600,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                    }}>
                        {action.label}
                    </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
                    {isRunning && (
                        <span style={{ color: color, fontSize: '0.72rem', fontWeight: 700 }}>
                            {action.progress}%
                        </span>
                    )}
                    {isRunning && (
                        <button
                            title="Cancel"
                            onClick={(e) => { e.stopPropagation(); onCancel(action.id); }}
                            style={{
                                background: 'none', border: 'none', cursor: 'pointer',
                                padding: '2px', color: '#475569', lineHeight: 1,
                            }}
                        >
                            <X size={12} />
                        </button>
                    )}
                    {!isRunning && (
                        <button
                            title="Dismiss"
                            onClick={(e) => { e.stopPropagation(); onDismiss(action.id); }}
                            style={{
                                background: 'none', border: 'none', cursor: 'pointer',
                                padding: '2px', color: '#475569', lineHeight: 1,
                            }}
                        >
                            <X size={12} />
                        </button>
                    )}
                    {isError && (
                        <button
                            title="Retry"
                            onClick={(e) => { e.stopPropagation(); onNavigate && onNavigate(action, true); }}
                            style={{
                                background: 'none', border: 'none', cursor: 'pointer',
                                padding: '2px', color: '#fbbf24', lineHeight: 1,
                            }}
                        >
                            <RefreshCw size={12} />
                        </button>
                    )}
                </div>
            </div>

            {/* Step text */}
            <div style={{
                color: '#475569',
                fontSize: '0.72rem',
                marginTop: '3px',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
            }}>
                {isRunning
                    ? action.activeStep
                    : isDone
                        ? `Completed ${elapsed > 0 ? `${elapsed}m ago` : 'just now'}`
                        : isError
                            ? (action.activeStep || 'Failed')
                            : 'Cancelled'
                }
            </div>

            {/* Progress bar (only for running) */}
            {isRunning && <ProgressBar value={action.progress} color={color} />}
        </div>
    );
}

// Map of per-action AbortControllers (set by AnalysisContext)
export const _abortControllers = new Map(); // actionId → AbortController

export default function SessionsTray({ onCancelAction }) {
    const location = useLocation();
    const navigate = useNavigate();
    const { actions, runningCount, hydrated } = useSession();
    const [expanded, setExpanded] = useState(false);

    // Hide on certain pages
    if (HIDDEN_PATHS.has(location.pathname)) return null;

    // Only show when there's at least one action
    const visible = actions.length > 0;
    if (!visible || !hydrated) return null;

    const handleCancel = useCallback(async (id) => {
        // Abort in-flight HTTP requests
        const ctrl = _abortControllers.get(id);
        if (ctrl) {
            ctrl.abort();
            _abortControllers.delete(id);
        }
        // If external cancel handler provided (from AnalysisContext)
        onCancelAction?.(id);
        await sessionStore.cancelAction(id);
    }, [onCancelAction]);

    const handleDismiss = useCallback(async (id) => {
        await sessionStore.dismissAction(id);
    }, []);

    const handleNavigate = useCallback((action, retry = false) => {
        if (action.type === 'analysis' && action.meta?.ticker) {
            // Navigate to the stock analysis page for this ticker
            navigate(`/dashboard?ticker=${action.meta.ticker}`);
        }
    }, [navigate]);

    // Sort: running first, then by updatedAt desc
    const sorted = [...actions].sort((a, b) => {
        if (a.status === 'running' && b.status !== 'running') return -1;
        if (b.status === 'running' && a.status !== 'running') return 1;
        return b.updatedAt - a.updatedAt;
    });

    const errorCount = actions.filter((a) => a.status === 'error').length;

    return (
        <div style={{
            position: 'fixed',
            bottom: '24px',
            left: '264px',
            zIndex: 9998,
            width: '300px',
            background: 'linear-gradient(185deg, #0d1222 0%, #080a12 100%)',
            border: `1px solid ${errorCount > 0 ? 'rgba(239,68,68,0.4)' : runningCount > 0 ? 'rgba(59,130,246,0.35)' : 'rgba(255,255,255,0.08)'}`,
            borderRadius: '14px',
            boxShadow: '0 10px 30px rgba(0,0,0,0.5)',
            overflow: 'hidden',
            animation: 'fadeIn 0.3s ease-out',
        }}>
            {/* Header / Pill */}
            <button
                onClick={() => setExpanded((e) => !e)}
                style={{
                    width: '100%',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    padding: '12px 14px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '8px',
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {runningCount > 0
                        ? <Loader2 size={14} style={{ animation: 'spin 1.2s linear infinite', color: '#3b82f6' }} />
                        : errorCount > 0
                            ? <AlertTriangle size={14} style={{ color: '#ef4444' }} />
                            : <CheckCircle2 size={14} style={{ color: '#10b981' }} />
                    }
                    <span style={{ color: '#f1f5f9', fontSize: '0.82rem', fontWeight: 700 }}>
                        {runningCount > 0
                            ? `${runningCount} Active Session${runningCount > 1 ? 's' : ''}`
                            : errorCount > 0
                                ? `${errorCount} Session${errorCount > 1 ? 's' : ''} Failed`
                                : 'Sessions'
                        }
                    </span>
                    {/* Badge */}
                    {actions.length > 0 && (
                        <span style={{
                            background: runningCount > 0 ? '#3b82f6' : errorCount > 0 ? '#ef4444' : '#334155',
                            color: '#fff',
                            fontSize: '0.65rem',
                            fontWeight: 700,
                            borderRadius: '10px',
                            padding: '1px 7px',
                            lineHeight: '1.6',
                        }}>
                            {actions.length}
                        </span>
                    )}
                </div>
                {expanded ? <ChevronDown size={14} color="#475569" /> : <ChevronUp size={14} color="#475569" />}
            </button>

            {/* Action list */}
            {expanded && (
                <div style={{ maxHeight: '280px', overflowY: 'auto' }}>
                    {sorted.map((action) => (
                        <ActionRow
                            key={action.id}
                            action={action}
                            onCancel={handleCancel}
                            onDismiss={handleDismiss}
                            onNavigate={handleNavigate}
                        />
                    ))}
                </div>
            )}

            {/* Running summary (collapsed state) */}
            {!expanded && runningCount > 0 && (() => {
                const first = sorted.find((a) => a.status === 'running');
                return first ? (
                    <div style={{ padding: '0 14px 10px' }}>
                        <div style={{ color: '#475569', fontSize: '0.72rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {first.activeStep}
                        </div>
                        <ProgressBar value={first.progress} color="#3b82f6" />
                    </div>
                ) : null;
            })()}
        </div>
    );
}
