import React from 'react';

const CHUNK_RELOAD_KEY = 'tradetalk_chunk_reload';

export function isChunkLoadError(error) {
    const msg = String(error?.message || error || '');
    return (
        error?.name === 'ChunkLoadError'
        || /ChunkLoadError/i.test(msg)
        || /Failed to fetch dynamically imported module/i.test(msg)
        || /Loading chunk \d+ failed/i.test(msg)
        || /Importing a module script failed/i.test(msg)
    );
}

/** React.lazy wrapper — one-shot reload on stale hashed chunks after deploy. */
export function lazyWithRetry(importFn) {
    return React.lazy(() => importFn().catch((err) => {
        if (isChunkLoadError(err) && !sessionStorage.getItem(CHUNK_RELOAD_KEY)) {
            sessionStorage.setItem(CHUNK_RELOAD_KEY, '1');
            window.location.reload();
            return new Promise(() => {});
        }
        throw err;
    }));
}
