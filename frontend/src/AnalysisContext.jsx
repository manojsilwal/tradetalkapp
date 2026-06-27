import { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react';
import { API_BASE_URL, apiFetch, apiFetchTimed } from './api';
import { isBriefSessionTrustworthy, shouldSkipDailyBriefRefetch } from './freshness';
import * as sessionStore from './store/sessionStore';
import { addToast } from './SessionContext';
import { _abortControllers } from './components/SessionsTray';
import { SP500_TICKERS } from './sp500';

const SP500_TICKER_SET = new Set(SP500_TICKERS);

// Max concurrent ticker analyses (prevents API flooding)
const MAX_CONCURRENT_ANALYSES = 3;

const FAST_TIMEOUT_MS = 30000;
const MEDIUM_TIMEOUT_MS = 90000;
const LLM_TIMEOUT_MS = 240000; // 240s — cold GCP decision-terminal can exceed 150s (swarm + debate + LLM)
const LIVE_POLL_FAST_MS = 30000;
const LIVE_POLL_SLOW_MS = 5 * 60 * 1000;
const LIVE_POLL_ENABLED = (() => {
    try {
        const v = import.meta?.env?.VITE_DASHBOARD_LIVE_POLL;
        return v !== '0' && v !== 'false';
    } catch {
        return true;
    }
})();

function metricsKeyActivityMissing(metrics) {
    const block = metrics?.metrics ?? metrics;
    if (!block || Object.keys(block).length === 0) return true;
    const cur = block?.momentum_rsi?.current;
    return !cur || cur === 'N/A';
}

function fundamentalsMissing(fundamentals) {
    return !fundamentals?.metrics;
}

function decisionTerminalMissing(state) {
    return !state?.decisionData?.valuation;
}

function mergeDecisionData(prev, patch) {
    if (!patch) return prev ?? null;
    return {
        ...(prev || {}),
        ...patch,
        valuation: patch.valuation ?? prev?.valuation,
        quality: patch.quality ?? prev?.quality,
        verdict: patch.verdict ?? prev?.verdict,
        roadmap: patch.roadmap ?? prev?.roadmap,
        swarm: patch.swarm ?? prev?.swarm,
        debate: patch.debate ?? prev?.debate,
        brain: patch.brain ?? prev?.brain,
        scorecard_summary: patch.scorecard_summary ?? prev?.scorecard_summary,
        spot: patch.spot ?? prev?.spot,
        disclaimer: patch.disclaimer ?? prev?.disclaimer,
        data_freshness: patch.data_freshness ?? prev?.data_freshness,
        market_data_degraded: patch.market_data_degraded ?? prev?.market_data_degraded,
        spot_price_source: patch.spot_price_source ?? prev?.spot_price_source,
        generated_at_utc: patch.generated_at_utc ?? prev?.generated_at_utc,
        verdict_captured_at_utc: patch.verdict_captured_at_utc ?? prev?.verdict_captured_at_utc,
        macro_fetched_at_utc: patch.macro_fetched_at_utc ?? prev?.macro_fetched_at_utc,
        verdict_from_cache: patch.verdict_from_cache ?? prev?.verdict_from_cache,
    };
}

export function analysisStillRunning(state) {
    if (!state || state.status !== 'loading') return false;
    return (
        state.metricsLoading ||
        state.scorecardLoading ||
        state.debateLoading ||
        state.traceLoading ||
        state.decisionLoading ||
        state.predMarketsLoading ||
        state.smallCapLoading ||
        state.fundamentalsLoading ||
        state.roadmapLoading
    );
}

const AnalysisContext = createContext(null);

export function AnalysisProvider({ children }) {
    // Legacy / History Panel Cache
    const [recentAnalyses, setRecentAnalyses] = useState([]);
    const [recentDebates, setRecentDebates] = useState([]);

    // 1. Dashboard / Ticker Analysis States
    const [analyses, setAnalyses] = useState({});
    const analysesRef = useRef({});
    // Maps ticker → session action ID (for sessionStore integration)
    const sessionActionIds = useRef({});
    // Synchronous per-ticker lock so concurrent analyzeTicker calls cannot double-start
    const inFlightRef = useRef({});
    const analysisToastFlagsRef = useRef({});

    useEffect(() => {
        analysesRef.current = analyses;
    }, [analyses]);

    const dashboardPollerRef = useRef({
        sym: null,
        fastId: null,
        slowId: null,
        visHandler: null,
        lastFastMs: 0,
    });

    const stopDashboardPoller = useCallback(() => {
        const p = dashboardPollerRef.current;
        if (p.fastId) clearInterval(p.fastId);
        if (p.slowId) clearInterval(p.slowId);
        if (p.visHandler) {
            document.removeEventListener('visibilitychange', p.visHandler);
        }
        dashboardPollerRef.current = {
            sym: null,
            fastId: null,
            slowId: null,
            visHandler: null,
            lastFastMs: 0,
        };
    }, []);

    const refreshDashboardLiveData = useCallback(async (sym) => {
        const ticker = sym.trim().toUpperCase();
        if (!ticker) return;

        const update = (patch) => {
            setAnalyses((prev) => {
                const cur = prev[ticker];
                if (!cur || cur.status !== 'success') return prev;
                return { ...prev, [ticker]: { ...cur, ...patch } };
            });
        };

        try {
            const [metricsRes, quoteRes] = await Promise.allSettled([
                apiFetchTimed(`${API_BASE_URL}/metrics/${ticker}`, {}, FAST_TIMEOUT_MS),
                apiFetchTimed(
                    `${API_BASE_URL}/mcp/sp500/live-quote?symbol=${encodeURIComponent(ticker)}`,
                    {},
                    FAST_TIMEOUT_MS,
                ).catch(() =>
                    apiFetchTimed(`${API_BASE_URL}/stock-fundamentals/${ticker}`, {}, FAST_TIMEOUT_MS),
                ),
            ]);

            const patch = {};
            if (metricsRes.status === 'fulfilled' && metricsRes.value?.metrics) {
                patch.metricsData = metricsRes.value.metrics;
                patch.metricsFreshness = metricsRes.value.data_freshness ?? null;
            }

            if (quoteRes.status === 'fulfilled' && quoteRes.value) {
                const q = quoteRes.value;
                if (q.price != null) {
                    patch.liveSpotData = q;
                    setAnalyses((prev) => {
                        const cur = prev[ticker];
                        if (!cur || cur.status !== 'success') return prev;
                        const fd = cur.fundamentalsData;
                        const nextFund = fd
                            ? {
                                ...fd,
                                company_info: {
                                    ...(fd.company_info || {}),
                                    current_price: q.price,
                                    price_change: q.change ?? fd.company_info?.price_change,
                                    price_change_pct: q.change_pct ?? fd.company_info?.price_change_pct,
                                },
                                spot_freshness: q.data_freshness ?? fd.spot_freshness,
                            }
                            : fd;
                        const nextDecision = cur.decisionData
                            ? {
                                ...cur.decisionData,
                                spot: {
                                    price_usd: q.price,
                                    source: q.source,
                                    captured_at_utc: q.captured_at ?? q.data_freshness?.captured_at,
                                    degraded: q.degraded ?? q.data_freshness?.degraded,
                                },
                                data_freshness: q.data_freshness ?? cur.decisionData.data_freshness,
                            }
                            : cur.decisionData;
                        return {
                            ...prev,
                            [ticker]: {
                                ...cur,
                                ...patch,
                                fundamentalsData: nextFund,
                                decisionData: nextDecision,
                            },
                        };
                    });
                    dashboardPollerRef.current.lastFastMs = Date.now();
                    return;
                }
                if (q.company_info || q.metrics) {
                    patch.fundamentalsData = q;
                }
            }

            if (Object.keys(patch).length) {
                update(patch);
            }
            dashboardPollerRef.current.lastFastMs = Date.now();
        } catch {
            /* best-effort background refresh */
        }
    }, []);

    const startDashboardPoller = useCallback((sym) => {
        if (!LIVE_POLL_ENABLED) return;
        const ticker = sym.trim().toUpperCase();
        if (!ticker) return;
        stopDashboardPoller();

        const runFast = () => {
            if (document.visibilityState === 'hidden') return;
            refreshDashboardLiveData(ticker);
        };
        const runSlow = () => {
            if (document.visibilityState === 'hidden') return;
            apiFetchTimed(`${API_BASE_URL}/prediction-markets?ticker=${ticker}`, {}, FAST_TIMEOUT_MS)
                .then((res) => {
                    setAnalyses((prev) => {
                        const cur = prev[ticker];
                        if (!cur || cur.status !== 'success') return prev;
                        return { ...prev, [ticker]: { ...cur, predMarketsData: res } };
                    });
                })
                .catch(() => {});
        };

        const onVis = () => {
            if (document.visibilityState !== 'visible') return;
            const last = dashboardPollerRef.current.lastFastMs || 0;
            if (Date.now() - last > 60_000) runFast();
        };

        document.addEventListener('visibilitychange', onVis);
        dashboardPollerRef.current = {
            sym: ticker,
            fastId: setInterval(runFast, LIVE_POLL_FAST_MS),
            slowId: setInterval(runSlow, LIVE_POLL_SLOW_MS),
            visHandler: onVis,
            lastFastMs: Date.now(),
        };
    }, [stopDashboardPoller, refreshDashboardLiveData]);

    useEffect(() => () => stopDashboardPoller(), [stopDashboardPoller]);

    // 2. Daily Brief States
    const [dailyBriefState, setDailyBriefState] = useState({
        data: null,
        screenerData: null,
        loading: true,
        refreshing: false,
        lastSyncedAt: null,
        error: null,
        deepStatus: null,
        deepBusy: false,
        activeTab: 'movers',
    });
    const dailyBriefDataRef = useRef(null);
    const dailyBriefLoadingRef = useRef(true);
    const dailyBriefFetchedAtRef = useRef(0);
    const dailyBriefScreenerRef = useRef(null);

    useEffect(() => {
        dailyBriefDataRef.current = dailyBriefState.data;
        dailyBriefLoadingRef.current = dailyBriefState.loading;
        dailyBriefScreenerRef.current = dailyBriefState.screenerData;
    }, [dailyBriefState.data, dailyBriefState.loading, dailyBriefState.screenerData]);

    // 3. Global Macro States
    const [macroState, setMacroState] = useState({
        data: null,
        loading: true,
        error: null,
        flowPeriod: '1d',
    });

    const addAnalysis = useCallback((ticker, result) => {
        setRecentAnalyses(prev => {
            const filtered = prev.filter(a => a.ticker !== ticker);
            return [{ ticker, result, timestamp: Date.now() }, ...filtered].slice(0, 10);
        });
    }, []);

    const addDebate = useCallback((ticker, result) => {
        setRecentDebates(prev => {
            const filtered = prev.filter(d => d.ticker !== ticker);
            return [{ ticker, result, timestamp: Date.now() }, ...filtered].slice(0, 10);
        });
    }, []);

    const getLastAnalysis = useCallback((ticker) => {
        return recentAnalyses.find(a => a.ticker === ticker)?.result || null;
    }, [recentAnalyses]);

    const getLastDebate = useCallback((ticker) => {
        return recentDebates.find(d => d.ticker === ticker)?.result || null;
    }, [recentDebates]);

    const getAnalysisState = useCallback((ticker) => {
        const normalized = ticker.trim().toUpperCase();
        if (analysesRef.current[normalized]) {
          return analysesRef.current[normalized];
        }
        const cached = recentAnalyses.find(a => a.ticker === normalized)?.result;
        if (cached) {
          return {
            status: 'success',
            loadingStep: '',
            error: null,
            loading: false,
            traceData: cached.trace,
            traceLoading: false,
            metricsData: cached.metrics,
            metricsLoading: false,
            capBucket: cached.capBucket,
            smallCapData: cached.smallCap,
            smallCapLoading: false,
            debateData: cached.debate,
            debateLoading: false,
            debateError: null,
            decisionData: cached.dt,
            decisionLoading: false,
            scorecardData: cached.scorecard,
            scorecardLoading: false,
            scorecardError: null,
            predMarketsData: cached.predMarkets,
            predMarketsLoading: false,
            fundamentalsData: cached.fundamentals,
            fundamentalsLoading: false,
          };
        }
        return null;
    }, [recentAnalyses]);

    // Cancel a session action by its session action ID
    const cancelAnalysis = useCallback(async (actionId) => {
        // Abort HTTP requests
        const ctrl = _abortControllers.get(actionId);
        if (ctrl) {
            ctrl.abort();
            _abortControllers.delete(actionId);
        }
        // Find the ticker for this action
        const ticker = Object.keys(sessionActionIds.current).find(
            (t) => sessionActionIds.current[t] === actionId
        );
        if (ticker) {
            setAnalyses((prev) => {
                const cur = prev[ticker];
                if (!cur || cur.status !== 'loading') return prev;
                return {
                    ...prev,
                    [ticker]: {
                        ...cur,
                        status: 'cancelled',
                        loading: false,
                        loadingStep: '',
                        metricsLoading: false,
                        scorecardLoading: false,
                        debateLoading: false,
                        traceLoading: false,
                        decisionLoading: false,
                        predMarketsLoading: false,
                        smallCapLoading: false,
                        fundamentalsLoading: false,
                    },
                };
            });
        }
        await sessionStore.cancelAction(actionId);
    }, []);

    // Dashboard Analysis Action
    const analyzeTicker = useCallback(async (tickerSymbol, forceRefresh = false, _resumedActionId = null) => {
        const sym = tickerSymbol.trim().toUpperCase();
        if (!sym) return;

        if (inFlightRef.current[sym] && !forceRefresh) {
            if (!_resumedActionId) {
                addToast(`${sym} is already being analyzed`, 'info', 3000);
            }
            return;
        }

        // Check if currently fetching
        const current = analysesRef.current[sym];
        if (current?.status === 'loading' && !forceRefresh) {
            // Dedup: already running — surface a toast if user triggered manually
            if (!_resumedActionId) {
                const existingActionId = sessionActionIds.current[sym];
                if (existingActionId) {
                    addToast(`${sym} is already being analyzed`, 'info', 3000);
                }
            }
            return;
        }

        // Concurrency gate — don't flood the API
        const runningCount = Object.values(analysesRef.current).filter(
            (a) => a?.status === 'loading'
        ).length;
        if (runningCount >= MAX_CONCURRENT_ANALYSES && !forceRefresh) {
            addToast(`Max ${MAX_CONCURRENT_ANALYSES} analyses at once — wait for one to finish`, 'warning', 4000);
            return;
        }

        // Check if already fetched (re-fetch when key metrics were empty from a prior rate-limit)
        const existing = getAnalysisState(sym);
        if (
            existing?.status === 'success'
            && !forceRefresh
            && !metricsKeyActivityMissing(existing.metricsData)
            && !fundamentalsMissing(existing.fundamentalsData)
            && !decisionTerminalMissing(existing)
        ) {
            if (!analysesRef.current[sym]) {
                setAnalyses(prev => ({
                    ...prev,
                    [sym]: existing
                }));
            }
            startDashboardPoller(sym);
            return;
        }

        stopDashboardPoller();
        inFlightRef.current[sym] = true;
        analysisToastFlagsRef.current[sym] = { fundamentals: false, verdict: false };

        // Create a session action record (or reuse resumed / running one)
        let sessionActionId = _resumedActionId;
        if (!sessionActionId) {
            const existingRunning = sessionStore.findAction('analysis', 'ticker', sym);
            if (existingRunning?.status === 'running') {
                sessionActionId = existingRunning.id;
            } else {
                const action = sessionStore.createAction({
                    type: 'analysis',
                    label: `${sym} Analysis`,
                    resumable: true,
                    meta: { ticker: sym },
                });
                sessionActionId = action.id;
            }
        }
        sessionActionIds.current[sym] = sessionActionId;

        // Create an AbortController for this analysis so cancel works properly
        const abortController = new AbortController();
        _abortControllers.set(sessionActionId, abortController);
        const abortSignal = abortController.signal;
        const skipTickerValidation = SP500_TICKER_SET.has(sym);

        // Initialize state
        const initialTickerState = {
            status: 'loading',
            loadingStep: skipTickerValidation ? 'Loading data…' : 'Validating symbol…',
            error: null,
            loading: true,
            traceData: null,
            traceLoading: true,
            metricsData: null,
            metricsLoading: true,
            metricsFreshness: null,
            liveSpotData: null,
            capBucket: null,
            smallCapData: null,
            smallCapLoading: false,
            debateData: null,
            debateLoading: true,
            debateError: null,
            decisionData: null,
            decisionLoading: true,
            roadmapLoading: true,
            scorecardData: null,
            scorecardLoading: true,
            scorecardError: null,
            predMarketsData: null,
            predMarketsLoading: true,
            fundamentalsData: null,
            fundamentalsLoading: true,
        };

        // Note: we no longer cancel other in-flight analyses here.
        // All tickers run concurrently (up to MAX_CONCURRENT_ANALYSES).
        setAnalyses(prev => ({
            ...prev,
            [sym]: initialTickerState,
        }));

        let validationFailed = false;

        if (!skipTickerValidation) {
            try {
                const probe = await apiFetch(`${API_BASE_URL}/metrics/validate/${encodeURIComponent(sym)}`).catch(() => null);
                const probeSoftFail = probe?.reason === 'probe_timeout' || probe?.reason === 'probe_failed';
                if (probe && probe.exists === false && !probeSoftFail) {
                    const msg = probe.reason === 'invalid_format'
                        ? `Ticker "${sym}" looks invalid. Check the symbol format and try again.`
                        : `Could not find a market quote for "${sym}". Check the symbol and try again.`;

                    setAnalyses(prev => ({
                        ...prev,
                        [sym]: {
                            ...prev[sym],
                            status: 'error',
                            error: msg,
                            loading: false,
                            loadingStep: '',
                            traceLoading: false,
                            metricsLoading: false,
                            smallCapLoading: false,
                            debateLoading: false,
                            decisionLoading: false,
                            roadmapLoading: false,
                            scorecardLoading: false,
                            predMarketsLoading: false,
                            fundamentalsLoading: false,
                        }
                    }));
                    validationFailed = true;
                }
            } catch (_) { /* continue */ }
        }

        if (validationFailed) {
            delete inFlightRef.current[sym];
            _abortControllers.delete(sessionActionId);
            await sessionStore.failAction(sessionActionId, 'Ticker validation failed');
            return;
        }

        let successCount = 0;
        let lastErr = null;
        let insufficientErr = null;

        const onSuccess = () => {
            successCount += 1;
        };
        const onFail = (err) => {
            if (err) lastErr = err;
            // Truthful-data contract: the backend explicitly refused to return
            // a fabricated result. Surface this to the user instead of showing
            // a partial "success".
            if (err?.isInsufficientData && !insufficientErr) insufficientErr = err;
        };

        const updateTickerState = (updates) => {
            setAnalyses(prev => {
                if (!prev[sym]) return prev;
                return {
                    ...prev,
                    [sym]: {
                        ...prev[sym],
                        ...updates
                    }
                };
            });
        };

        updateTickerState({
            loading: false,
            loadingStep: 'Loading data…',
        });

        const SMALL_CAP_BUCKETS = new Set(['Small Cap', 'Micro Cap']);

        // Helper to emit progress to the session tray
        const TOTAL_STEPS = 8;
        let stepsCompleted = 0;
        const emitStep = (stepName) => {
            stepsCompleted++;
            sessionStore.updateProgress(sessionActionId, {
                progress: Math.round((stepsCompleted / TOTAL_STEPS) * 100),
                activeStep: stepName,
                stepCompleted: stepName,
            });
        };
        const emitStepFailed = (stepName) => {
            sessionStore.updateProgress(sessionActionId, {
                stepFailed: stepName,
            });
        };

        const dtBase = `${API_BASE_URL}/decision-terminal`;
        const dtQ = `?ticker=${encodeURIComponent(sym)}${forceRefresh ? '&force=true' : ''}`;

        const mergeDtPatch = (patch) => {
            setAnalyses((prev) => {
                const cur = prev[sym];
                if (!cur) return prev;
                return {
                    ...prev,
                    [sym]: {
                        ...cur,
                        decisionData: mergeDecisionData(cur.decisionData, patch),
                    },
                };
            });
        };

        const coreJobs = [
            apiFetchTimed(`${API_BASE_URL}/metrics/${sym}`, {}, FAST_TIMEOUT_MS, abortSignal)
                .then(async (res) => {
                    let metrics = res?.metrics ?? null;
                    if (metricsKeyActivityMissing(metrics)) {
                        try {
                            const retry = await apiFetchTimed(
                                `${API_BASE_URL}/metrics/${sym}`,
                                {},
                                FAST_TIMEOUT_MS,
                                abortSignal,
                            );
                            if (retry?.metrics && !metricsKeyActivityMissing(retry.metrics)) {
                                metrics = retry.metrics;
                                res = retry;
                            }
                        } catch {
                            /* keep first response */
                        }
                    }
                    const bucket = res?.cap_bucket ?? null;
                    onSuccess();
                    emitStep('Metrics & market data');
                    updateTickerState({
                        metricsData: metrics,
                        metricsFreshness: res?.data_freshness ?? null,
                        capBucket: bucket,
                        metricsLoading: false
                    });

                    if (bucket && SMALL_CAP_BUCKETS.has(bucket)) {
                        updateTickerState({ smallCapLoading: true });
                        apiFetchTimed(`${API_BASE_URL}/small-cap-assessment/${encodeURIComponent(sym)}`, {}, FAST_TIMEOUT_MS, abortSignal)
                            .then(smallCapRes => {
                                updateTickerState({ smallCapData: smallCapRes, smallCapLoading: false });
                            })
                            .catch(() => {
                                updateTickerState({ smallCapData: null, smallCapLoading: false });
                            });
                    } else {
                        updateTickerState({ smallCapData: null, smallCapLoading: false });
                    }
                })
                .catch((err) => {
                    onFail(err);
                    emitStepFailed('Metrics');
                    updateTickerState({
                        metricsData: null,
                        capBucket: null,
                        metricsLoading: false,
                        smallCapData: null,
                        smallCapLoading: false
                    });
                }),

            apiFetchTimed(`${API_BASE_URL}/prediction-markets?ticker=${sym}`, {}, FAST_TIMEOUT_MS, abortSignal)
                .then((res) => {
                    onSuccess();
                    emitStep('Prediction markets');
                    updateTickerState({ predMarketsData: res, predMarketsLoading: false });
                })
                .catch((err) => {
                    onFail(err);
                    emitStepFailed('Prediction markets');
                    updateTickerState({ predMarketsData: null, predMarketsLoading: false });
                }),

            apiFetchTimed(`${dtBase}/snapshot${dtQ}`, {}, FAST_TIMEOUT_MS, abortSignal)
                .then((snap) => {
                    onSuccess();
                    emitStep('Valuation snapshot');
                    mergeDtPatch({
                        ticker: sym,
                        disclaimer: snap.disclaimer,
                        generated_at_utc: snap.generated_at_utc,
                        valuation: snap.valuation,
                        quality: snap.quality,
                        scorecard_summary: snap.scorecard_summary,
                        spot: snap.spot,
                        data_freshness: snap.data_freshness,
                        market_data_degraded: snap.market_data_degraded,
                        spot_price_source: snap.spot_price_source,
                    });
                    updateTickerState({ decisionLoading: false });
                })
                .catch((err) => {
                    onFail(err);
                    emitStepFailed('Valuation snapshot');
                    updateTickerState({ decisionLoading: false });
                }),

            apiFetchTimed(`${dtBase}/swarm${dtQ}`, {}, MEDIUM_TIMEOUT_MS, abortSignal)
                .then((sw) => {
                    onSuccess();
                    emitStep('Swarm consensus trace');
                    mergeDtPatch({
                        ticker: sym,
                        swarm: sw.swarm,
                        verdict: sw.verdict,
                        macro_fetched_at_utc: sw.macro_fetched_at_utc,
                        generated_at_utc: sw.generated_at_utc,
                    });
                    updateTickerState({
                        traceData: sw?.swarm ?? null,
                        traceLoading: false,
                    });
                    // The base /stock-fundamentals job already fetched with the
                    // default (BULL_NORMAL) regime. Only refetch with the live
                    // regime when it actually differs, so we never double-fetch
                    // in the common case while preserving regime-adjusted accuracy
                    // under stress regimes.
                    const regime = sw?.swarm?.macro_state?.market_regime;
                    const regimeUpper = (regime || '').toUpperCase();
                    if (regimeUpper && regimeUpper !== 'BULL_NORMAL') {
                        apiFetchTimed(
                            `${API_BASE_URL}/stock-fundamentals/${encodeURIComponent(sym)}?market_regime=${encodeURIComponent(regime)}`,
                            {},
                            FAST_TIMEOUT_MS,
                            abortSignal,
                        )
                            .then((fundRes) => {
                                updateTickerState({ fundamentalsData: fundRes });
                            })
                            .catch(() => {});
                    }
                })
                .catch((err) => {
                    onFail(err);
                    emitStepFailed('Swarm trace');
                    updateTickerState({
                        traceData: null,
                        traceLoading: false,
                    });
                }),

            apiFetchTimed(`${dtBase}/roadmap${dtQ}`, {}, MEDIUM_TIMEOUT_MS, abortSignal)
                .then((rd) => {
                    onSuccess();
                    emitStep('Future price roadmap');
                    mergeDtPatch({
                        ticker: sym,
                        roadmap: rd.roadmap,
                        generated_at_utc: rd.generated_at_utc,
                    });
                    updateTickerState({ roadmapLoading: false });
                })
                .catch((err) => {
                    onFail(err);
                    emitStepFailed('Roadmap');
                    updateTickerState({ roadmapLoading: false });
                }),

            apiFetchTimed(
                `${API_BASE_URL}/scorecard/${encodeURIComponent(sym)}?preset=balanced&skip_llm_scores=true`,
                {},
                FAST_TIMEOUT_MS,
                abortSignal,
            )
                .then((res) => {
                    onSuccess();
                    emitStep('Scorecard');
                    updateTickerState({ scorecardData: res, scorecardError: null, scorecardLoading: false });
                })
                .catch((err) => {
                    onFail(err);
                    emitStepFailed('Scorecard');
                    updateTickerState({
                        scorecardError: err?.message || 'Scorecard unavailable',
                        scorecardData: null,
                        scorecardLoading: false
                    });
                }),

            apiFetchTimed(`${API_BASE_URL}/stock-fundamentals/${sym}`, {}, FAST_TIMEOUT_MS, abortSignal)
                .then((res) => {
                    onSuccess();
                    emitStep('Fundamentals');
                    updateTickerState({ fundamentalsData: res, fundamentalsLoading: false });
                    const flags = analysisToastFlagsRef.current[sym];
                    if (res?.metrics && !flags?.fundamentals) {
                        analysisToastFlagsRef.current[sym] = { ...flags, fundamentals: true };
                        addToast(`${sym} fundamentals ready`, 'info', 4000);
                    }
                })
                .catch((err) => {
                    onFail(err);
                    emitStepFailed('Fundamentals');
                    updateTickerState({ fundamentalsData: null, fundamentalsLoading: false });
                }),
        ];

        const debateJob = apiFetchTimed(`${dtBase}/debate${dtQ}`, {}, LLM_TIMEOUT_MS, abortSignal)
            .then((vd) => {
                onSuccess();
                emitStep('Multi-agent debate');
                mergeDtPatch({
                    ticker: sym,
                    verdict: vd.verdict,
                    swarm: vd.swarm,
                    debate: vd.debate,
                    brain: vd.brain,
                    verdict_captured_at_utc: vd.verdict_captured_at_utc,
                    macro_fetched_at_utc: vd.macro_fetched_at_utc,
                    generated_at_utc: vd.generated_at_utc,
                    verdict_from_cache: vd.slice_from_cache,
                });
                updateTickerState({
                    debateData: vd?.debate ?? null,
                    debateError: null,
                    debateLoading: false,
                });
                const flags = analysisToastFlagsRef.current[sym];
                if (!flags?.verdict) {
                    analysisToastFlagsRef.current[sym] = { ...flags, verdict: true };
                    addToast(`${sym} debate & verdict ready ✓`, 'success', 5000);
                }
            })
            .catch((err) => {
                onFail(err);
                emitStepFailed('Multi-agent debate');
                updateTickerState({
                    debateData: null,
                    debateError: err?.isInsufficientData
                        ? (err.message || 'Insufficient live data for the debate.')
                        : 'Debate temporarily unavailable.',
                    debateLoading: false,
                });
            });

        let coreFinished = false;
        const finishCoreAnalysis = () => {
            if (coreFinished) return;
            coreFinished = true;
            _abortControllers.delete(sessionActionId);

            setAnalyses((prev) => {
                const current = prev[sym];
                if (!current) return prev;
                if (current.status === 'cancelled') return prev;

                const isSuccess = successCount > 0 && !insufficientErr;
                let finalError = null;

                if (insufficientErr) {
                    finalError = `Insufficient data: ${insufficientErr.message || 'required live market data could not be fetched.'}`;
                } else if (!isSuccess) {
                    const msg = lastErr?.message || String(lastErr || '');
                    if (/failed to fetch|network|load failed/i.test(msg)) {
                        finalError = `Cannot reach the API at ${API_BASE_URL}. Check VITE_API_BASE_URL (Vercel) and that the backend allows your origin (CORS).`;
                    } else {
                        finalError = msg || 'Analysis failed — all API requests returned errors.';
                    }
                }

                const updated = {
                    ...current,
                    loading: false,
                    loadingStep: '',
                    status: isSuccess ? 'success' : 'error',
                    error: finalError,
                };

                if (isSuccess && (updated.metricsData || updated.decisionData || updated.traceData || updated.fundamentalsData)) {
                    setTimeout(() => {
                        addAnalysis(sym, {
                            trace: updated.traceData,
                            debate: updated.debateData,
                            metrics: updated.metricsData,
                            dt: updated.decisionData,
                            scorecard: updated.scorecardData,
                            predMarkets: updated.predMarketsData,
                            smallCap: updated.smallCapData,
                            capBucket: updated.capBucket,
                            fundamentals: updated.fundamentalsData,
                        });
                        startDashboardPoller(sym);
                        sessionStore.completeAction(sessionActionId, {
                            ticker: sym,
                            completedAt: Date.now(),
                        });
                    }, 0);
                } else if (!isSuccess) {
                    sessionStore.failAction(sessionActionId, finalError || 'Analysis failed');
                    addToast(`${sym} analysis failed`, 'error', 5000);
                }

                return {
                    ...prev,
                    [sym]: updated,
                };
            });
        };

        Promise.allSettled(coreJobs).then(finishCoreAnalysis);

        Promise.allSettled([debateJob]).then(() => {
            setAnalyses((prev) => {
                const current = prev[sym];
                if (!current || current.status !== 'success') return prev;
                addAnalysis(sym, {
                    trace: current.traceData,
                    debate: current.debateData,
                    metrics: current.metricsData,
                    dt: current.decisionData,
                    scorecard: current.scorecardData,
                    predMarkets: current.predMarketsData,
                    smallCap: current.smallCapData,
                    capBucket: current.capBucket,
                    fundamentals: current.fundamentalsData,
                });
                return prev;
            });
        }).finally(() => {
            delete inFlightRef.current[sym];
        });

    }, [addAnalysis, getAnalysisState, startDashboardPoller, stopDashboardPoller]);

    const shouldResumeAnalysis = useCallback((ticker, _actionId) => {
        const sym = ticker.trim().toUpperCase();
        if (inFlightRef.current[sym]) return false;
        if (analysesRef.current[sym]?.status === 'loading') return false;
        return true;
    }, []);

    // Resume an analysis that was interrupted (e.g. by page refresh)
    // Called by SessionProvider on mount when running actions are found in IndexedDB
    const resumeAnalysis = useCallback((ticker, actionId) => {
        analyzeTicker(ticker, false, actionId);
    }, [analyzeTicker]);

    // Daily Brief Action
    const loadDailyBrief = useCallback(async (forceRefresh = false) => {
        const existing = dailyBriefDataRef.current;
        if (dailyBriefLoadingRef.current && existing && !forceRefresh) {
            return;
        }
        if (shouldSkipDailyBriefRefetch(existing, dailyBriefFetchedAtRef.current, forceRefresh)) {
            return;
        }

        const isInitial = !dailyBriefDataRef.current;
        setDailyBriefState(prev => ({
            ...prev,
            ...(isInitial ? { loading: true } : { refreshing: true }),
            error: null,
        }));

        try {
            const useRefresh = forceRefresh || !isBriefSessionTrustworthy(existing);
            const q = useRefresh ? '?refresh=true' : '';
            const briefJson = await apiFetch(`${API_BASE_URL}/daily-brief${q}`);
            dailyBriefFetchedAtRef.current = Date.now();
            dailyBriefDataRef.current = briefJson;

            let screenerJson = dailyBriefScreenerRef.current;
            let activeTab = 'movers';

            if (!briefJson.stale_unavailable && (!screenerJson || forceRefresh)) {
                try {
                    screenerJson = await apiFetch(`${API_BASE_URL}/daily-brief/screener`);
                    if (screenerJson && screenerJson.rows && screenerJson.rows.length > 0) {
                        activeTab = 'growth';
                    }
                } catch (screenerErr) {
                    if (screenerErr?.status !== 429) {
                        console.warn('Failed to load screener data, falling back to movers', screenerErr);
                    }
                }
            }

            setDailyBriefState(prev => ({
                ...prev,
                data: briefJson,
                screenerData: screenerJson ?? prev.screenerData,
                activeTab: activeTab === 'growth' ? 'growth' : prev.activeTab,
                deepStatus: briefJson.deep_refresh || prev.deepStatus,
                loading: false,
                refreshing: false,
                lastSyncedAt: Date.now(),
                error: null,
            }));
        } catch (e) {
            dailyBriefFetchedAtRef.current = Date.now();
            const errMsg = e.status === 429
                ? (e.message || 'Too many requests — wait a minute and try Refresh.')
                : (e.message || 'Failed to load daily brief');
            setDailyBriefState(prev => ({
                ...prev,
                loading: false,
                refreshing: false,
                ...(isInitial ? { error: errMsg } : {}),
            }));
        }
    }, []);

    const startDailyBriefDeepRefresh = useCallback(async () => {
        setDailyBriefState(prev => ({
            ...prev,
            deepBusy: true,
            error: null
        }));

        try {
            const res = await apiFetch(`${API_BASE_URL}/daily-brief/deep-refresh`, {
                method: 'POST',
            });
            
            setDailyBriefState(prev => {
                const next = {
                    ...prev,
                    deepStatus: res.deep_refresh || prev.deepStatus
                };
                if (res.completed && res.rows) {
                    next.data = res;
                    next.deepBusy = false;
                } else if (!res.accepted) {
                    next.deepBusy = false;
                    next.error = 'Deep refresh already running';
                }
                return next;
            });
        } catch (e) {
            setDailyBriefState(prev => ({
                ...prev,
                deepBusy: false,
                error: e.message || 'Failed to start deep refresh'
            }));
        }
    }, []);

    const setDailyBriefActiveTab = useCallback((tab) => {
        setDailyBriefState(prev => ({
            ...prev,
            activeTab: tab
        }));
    }, []);

    // Daily Brief Polling Effect
    const pollRef = useRef(null);

    useEffect(() => {
        if (!dailyBriefState.deepBusy) {
            if (pollRef.current) {
                clearInterval(pollRef.current);
                pollRef.current = null;
            }
            return undefined;
        }

        pollRef.current = setInterval(async () => {
            try {
                const st = await apiFetch(`${API_BASE_URL}/daily-brief/deep-refresh/status`);
                setDailyBriefState(prev => {
                    const next = { ...prev, deepStatus: st };
                    if (st.status === 'done') {
                        next.deepBusy = false;
                        setTimeout(() => loadDailyBrief(true), 0);
                    } else if (st.status === 'error') {
                        next.deepBusy = false;
                        next.error = st.error || 'Deep refresh failed';
                    }
                    return next;
                });
            } catch (e) {
                setDailyBriefState(prev => ({
                    ...prev,
                    deepBusy: false,
                    error: e.message || 'Failed to poll deep refresh'
                }));
            }
        }, 2500);

        return () => {
            if (pollRef.current) {
                clearInterval(pollRef.current);
                pollRef.current = null;
            }
        };
    }, [dailyBriefState.deepBusy, loadDailyBrief]);

    // Global Macro Action
    const loadMacro = useCallback(async (forceRefresh = false) => {
        if (macroState.loading && macroState.data && !forceRefresh) {
            return;
        }
        if (macroState.data && !forceRefresh) {
            return;
        }

        setMacroState(prev => ({
            ...prev,
            loading: true,
            error: null
        }));

        try {
            const json = await apiFetch(`${API_BASE_URL}/macro`);
            setMacroState(prev => ({
                ...prev,
                data: json,
                loading: false
            }));
        } catch (err) {
            setMacroState(prev => ({
                ...prev,
                error: err.message || 'Failed to load macro data',
                loading: false
            }));
        }
    }, [macroState.loading, macroState.data]);

    const setMacroFlowPeriod = useCallback((period) => {
        setMacroState(prev => ({
            ...prev,
            flowPeriod: period
        }));
    }, []);

    return (
        <AnalysisContext.Provider value={{
            recentAnalyses, recentDebates,
            addAnalysis, addDebate,
            getLastAnalysis, getLastDebate,
            analyses, analyzeTicker, cancelAnalysis, resumeAnalysis, shouldResumeAnalysis,
            dailyBriefState, loadDailyBrief, startDailyBriefDeepRefresh, setDailyBriefActiveTab,
            macroState, loadMacro, setMacroFlowPeriod,
        }}>
            {children}
        </AnalysisContext.Provider>
    );
}

export function useAnalysisHistory() {
    const ctx = useContext(AnalysisContext);
    if (!ctx) throw new Error('useAnalysisHistory must be used within AnalysisProvider');
    return ctx;
}
