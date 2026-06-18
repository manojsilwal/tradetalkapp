/**
 * sessionStore.js — Persistent session-level action store.
 *
 * Architecture:
 *  - In-memory Map<actionId, ActionRecord> as the primary source for React rendering
 *  - IndexedDB ("tradetalk-sessions") as the persistence layer (survives refresh / tab close)
 *  - BroadcastChannel("tt-session-sync") for real-time cross-tab synchronization
 *
 * ActionRecord shape:
 * {
 *   id: string,           // crypto.randomUUID()
 *   type: string,         // "analysis" | "backtest" | "observer" | "chat"
 *   label: string,        // human label e.g. "AAPL Analysis"
 *   status: string,       // "running" | "done" | "error" | "cancelled"
 *   resumable: boolean,   // if true, re-trigger on mount when status="running"
 *   createdAt: number,    // epoch ms
 *   updatedAt: number,
 *   completedAt: number|null,
 *   progress: number,     // 0–100
 *   activeStep: string,   // human status text
 *   stepsCompleted: string[],
 *   stepsFailed: string[],
 *   meta: object,         // e.g. { ticker, preset }
 * }
 *
 * Results are stored separately under a "results" store keyed by actionId
 * to keep the action list lightweight for tray rendering.
 */

import { get, set, del, keys, createStore } from 'idb-keyval';

// ── IndexedDB stores ──────────────────────────────────────────────────────────
const actionsDB = createStore('tradetalk-sessions', 'actions');
const resultsDB = createStore('tradetalk-sessions', 'results');

// ── In-memory mirror ──────────────────────────────────────────────────────────
const _actions = new Map(); // actionId → ActionRecord
const _listeners = new Set(); // () => void
let _snapshotVersion = 0;
let _cachedSnapshotVersion = -1;
let _cachedSnapshot = [];

function _notify() {
    _snapshotVersion += 1;
    _listeners.forEach((fn) => fn());
}

// ── BroadcastChannel (cross-tab sync) ────────────────────────────────────────
let _channel = null;
try {
    _channel = new BroadcastChannel('tt-session-sync');
    _channel.onmessage = (event) => {
        const { type, payload } = event.data || {};
        if (type === 'ACTION_UPDATE') {
            _actions.set(payload.id, payload);
            _notify();
        } else if (type === 'ACTION_DELETE') {
            _actions.delete(payload.id);
            _notify();
        }
    };
} catch (_) {
    // BroadcastChannel not available (e.g. private browsing in some browsers)
}

function _broadcast(type, payload) {
    try {
        _channel?.postMessage({ type, payload });
    } catch (_) { /* ignore */ }
}

// ── Persistence helpers ───────────────────────────────────────────────────────
async function _persist(record) {
    try {
        await set(record.id, record, actionsDB);
    } catch (_) { /* storage full or private mode — non-fatal */ }
}

async function _remove(id) {
    try {
        await del(id, actionsDB);
        await del(id, resultsDB);
    } catch (_) { /* ignore */ }
}

// ── Public API ────────────────────────────────────────────────────────────────

/** Subscribe to store changes. Returns an unsubscribe function. */
export function subscribe(listener) {
    _listeners.add(listener);
    return () => _listeners.delete(listener);
}

/** Get a snapshot of all current actions (as a plain array). */
export function getSnapshot() {
    // useSyncExternalStore requires referential stability between notifications.
    if (_cachedSnapshotVersion !== _snapshotVersion) {
        _cachedSnapshotVersion = _snapshotVersion;
        _cachedSnapshot = Array.from(_actions.values());
    }
    return _cachedSnapshot;
}

/** Get a single action by ID. */
export function getAction(id) {
    return _actions.get(id) ?? null;
}

/** Get all actions with a given status. */
export function getByStatus(status) {
    return Array.from(_actions.values()).filter((a) => a.status === status);
}

/** Find an action by type and a meta field match. */
export function findAction(type, metaKey, metaValue) {
    return Array.from(_actions.values()).find(
        (a) => a.type === type && a.meta?.[metaKey] === metaValue
    ) ?? null;
}

/**
 * Create a new action record. Returns the created ActionRecord.
 */
export function createAction({ type, label, resumable = true, meta = {} }) {
    const record = {
        id: crypto.randomUUID(),
        type,
        label,
        status: 'running',
        resumable,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        completedAt: null,
        progress: 0,
        activeStep: 'Starting…',
        stepsCompleted: [],
        stepsFailed: [],
        meta,
    };
    _actions.set(record.id, record);
    _notify();
    _persist(record);
    _broadcast('ACTION_UPDATE', record);
    return record;
}

/**
 * Update progress/step info on a running action.
 */
export function updateProgress(id, { progress, activeStep, stepCompleted, stepFailed } = {}) {
    const existing = _actions.get(id);
    if (!existing) return;

    const updated = {
        ...existing,
        updatedAt: Date.now(),
        ...(progress !== undefined && { progress }),
        ...(activeStep !== undefined && { activeStep }),
        ...(stepCompleted && {
            stepsCompleted: [...existing.stepsCompleted, stepCompleted],
        }),
        ...(stepFailed && {
            stepsFailed: [...existing.stepsFailed, stepFailed],
        }),
    };
    _actions.set(id, updated);
    _notify();
    _persist(updated); // fire-and-forget
    _broadcast('ACTION_UPDATE', updated);
}

/**
 * Mark an action as complete and optionally store its result.
 */
export async function completeAction(id, result = null) {
    const existing = _actions.get(id);
    if (!existing) return;

    const updated = {
        ...existing,
        status: 'done',
        progress: 100,
        activeStep: 'Complete',
        completedAt: Date.now(),
        updatedAt: Date.now(),
    };
    _actions.set(id, updated);
    _notify();
    await _persist(updated);
    _broadcast('ACTION_UPDATE', updated);

    if (result !== null) {
        try {
            await set(id, result, resultsDB);
        } catch (_) { /* ignore storage errors */ }
    }
}

/**
 * Mark an action as errored.
 */
export async function failAction(id, errorMessage = 'Unknown error') {
    const existing = _actions.get(id);
    if (!existing) return;

    const updated = {
        ...existing,
        status: 'error',
        activeStep: errorMessage,
        completedAt: Date.now(),
        updatedAt: Date.now(),
    };
    _actions.set(id, updated);
    _notify();
    await _persist(updated);
    _broadcast('ACTION_UPDATE', updated);
}

/**
 * Cancel a running action.
 */
export async function cancelAction(id) {
    const existing = _actions.get(id);
    if (!existing) return;

    const updated = {
        ...existing,
        status: 'cancelled',
        completedAt: Date.now(),
        updatedAt: Date.now(),
        activeStep: 'Cancelled',
    };
    _actions.set(id, updated);
    _notify();
    await _persist(updated);
    _broadcast('ACTION_UPDATE', updated);
}

/**
 * Remove a single action from the tray and storage.
 */
export async function dismissAction(id) {
    _actions.delete(id);
    _notify();
    await _remove(id);
    _broadcast('ACTION_DELETE', { id });
}

/**
 * Retrieve the stored result for a completed action.
 */
export async function getResult(id) {
    try {
        return await get(id, resultsDB) ?? null;
    } catch (_) {
        return null;
    }
}

/**
 * Hydrate the in-memory store from IndexedDB.
 * Call once on app startup. Returns all loaded records.
 */
export async function hydrate() {
    try {
        const allKeys = await keys(actionsDB);
        const records = await Promise.all(allKeys.map((k) => get(k, actionsDB)));
        records.forEach((record) => {
            if (record?.id) {
                _actions.set(record.id, record);
            }
        });
        _notify();
        return Array.from(_actions.values());
    } catch (_) {
        return [];
    }
}

/**
 * Purge old completed/cancelled/errored actions older than maxAgeMs (default 6h).
 * Keeps all running actions regardless of age.
 */
export async function purgeExpired(maxAgeMs = 6 * 60 * 60 * 1000) {
    const now = Date.now();
    const expired = Array.from(_actions.values()).filter(
        (a) =>
            a.status !== 'running' &&
            a.completedAt &&
            now - a.completedAt > maxAgeMs
    );
    for (const action of expired) {
        _actions.delete(action.id);
        await _remove(action.id);
    }
    if (expired.length > 0) _notify();
}

/**
 * Clear ALL session data. Call on logout.
 */
export async function clearAll() {
    const allKeys = await keys(actionsDB);
    await Promise.all(allKeys.map((k) => del(k, actionsDB)));
    const resultKeys = await keys(resultsDB);
    await Promise.all(resultKeys.map((k) => del(k, resultsDB)));
    _actions.clear();
    _notify();
}
