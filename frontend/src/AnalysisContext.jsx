import { createContext, useContext, useState, useCallback, useRef, useEffect } from 'react';
import { API_BASE_URL, apiFetch } from './api';

const AnalysisContext = createContext(null);

export function AnalysisProvider({ children }) {
    // Legacy / History Panel Cache
    const [recentAnalyses, setRecentAnalyses] = useState([]);
    const [recentDebates, setRecentDebates] = useState([]);

    // 1. Dashboard / Ticker Analysis States
    const [analyses, setAnalyses] = useState({});
    const analysesRef = useRef({});

    useEffect(() => {
        analysesRef.current = analyses;
    }, [analyses]);

    // 2. Daily Brief States
    const [dailyBriefState, setDailyBriefState] = useState({
        data: null,
        screenerData: null,
        loading: true,
        error: null,
        deepStatus: null,
        deepBusy: false,
        activeTab: 'movers',
    });

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
          };
        }
        return null;
    }, [recentAnalyses]);

    // Dashboard Analysis Action
    const analyzeTicker = useCallback(async (tickerSymbol, forceRefresh = false) => {
        const sym = tickerSymbol.trim().toUpperCase();
        if (!sym) return;

        // Check if currently fetching
        const current = analysesRef.current[sym];
        if (current?.status === 'loading' && !forceRefresh) {
            return;
        }

        // Check if already fetched
        const existing = getAnalysisState(sym);
        if (existing?.status === 'success' && !forceRefresh) {
            if (!analysesRef.current[sym]) {
                setAnalyses(prev => ({
                    ...prev,
                    [sym]: existing
                }));
            }
            return;
        }

        // Initialize state
        const initialTickerState = {
            status: 'loading',
            loadingStep: 'Validating symbol…',
            error: null,
            loading: true,
            traceData: null,
            traceLoading: true,
            metricsData: null,
            metricsLoading: true,
            capBucket: null,
            smallCapData: null,
            smallCapLoading: false,
            debateData: null,
            debateLoading: true,
            debateError: null,
            decisionData: null,
            decisionLoading: true,
            scorecardData: null,
            scorecardLoading: true,
            scorecardError: null,
            predMarketsData: null,
            predMarketsLoading: true,
        };

        setAnalyses(prev => ({
            ...prev,
            [sym]: initialTickerState
        }));

        let validationFailed = false;

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
                        scorecardLoading: false,
                        predMarketsLoading: false,
                    }
                }));
                validationFailed = true;
            }
        } catch (_) { /* continue */ }

        if (validationFailed) return;

        setAnalyses(prev => ({
            ...prev,
            [sym]: {
                ...prev[sym],
                loadingStep: 'Loading data…'
            }
        }));

        let successCount = 0;
        let lastErr = null;

        const onSuccess = () => {
            successCount += 1;
        };
        const onFail = (err) => {
            if (err) lastErr = err;
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

        const SMALL_CAP_BUCKETS = new Set(['Small Cap', 'Micro Cap']);

        const jobs = [
            apiFetch(`${API_BASE_URL}/metrics/${sym}`)
                .then((res) => {
                    const bucket = res?.cap_bucket ?? null;
                    onSuccess();
                    updateTickerState({
                        metricsData: res?.metrics ?? null,
                        capBucket: bucket,
                        metricsLoading: false
                    });

                    if (bucket && SMALL_CAP_BUCKETS.has(bucket)) {
                        updateTickerState({ smallCapLoading: true });
                        apiFetch(`${API_BASE_URL}/small-cap-assessment/${encodeURIComponent(sym)}`)
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
                    updateTickerState({
                        metricsData: null,
                        capBucket: null,
                        metricsLoading: false,
                        smallCapData: null,
                        smallCapLoading: false
                    });
                }),

            apiFetch(`${API_BASE_URL}/prediction-markets?ticker=${sym}`)
                .then((res) => {
                    onSuccess();
                    updateTickerState({ predMarketsData: res, predMarketsLoading: false });
                })
                .catch((err) => {
                    onFail(err);
                    updateTickerState({ predMarketsData: null, predMarketsLoading: false });
                }),

            apiFetch(`${API_BASE_URL}/trace?ticker=${sym}`)
                .then((res) => {
                    onSuccess();
                    updateTickerState({ traceData: res, traceLoading: false });
                })
                .catch((err) => {
                    onFail(err);
                    updateTickerState({ traceData: null, traceLoading: false });
                }),

            apiFetch(`${API_BASE_URL}/debate?ticker=${sym}`)
                .then((res) => {
                    onSuccess();
                    updateTickerState({ debateData: res, debateError: null, debateLoading: false });
                })
                .catch((err) => {
                    onFail(err);
                    updateTickerState({
                        debateError: 'Debate temporarily unavailable.',
                        debateData: null,
                        debateLoading: false
                    });
                }),

            apiFetch(`${API_BASE_URL}/decision-terminal?ticker=${sym}`)
                .then((res) => {
                    onSuccess();
                    updateTickerState({ decisionData: res, decisionLoading: false });
                })
                .catch((err) => {
                    onFail(err);
                    updateTickerState({ decisionData: null, decisionLoading: false });
                }),

            apiFetch(`${API_BASE_URL}/scorecard/${encodeURIComponent(sym)}?preset=balanced`)
                .then((res) => {
                    onSuccess();
                    updateTickerState({ scorecardData: res, scorecardError: null, scorecardLoading: false });
                })
                .catch((err) => {
                    onFail(err);
                    updateTickerState({
                        scorecardError: err?.message || 'Scorecard unavailable',
                        scorecardData: null,
                        scorecardLoading: false
                    });
                }),
        ];

        Promise.allSettled(jobs).then(() => {
            setAnalyses(prev => {
                const current = prev[sym];
                if (!current) return prev;

                const isSuccess = successCount > 0;
                let finalError = null;

                if (!isSuccess) {
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
                    error: finalError
                };

                if (isSuccess && (updated.metricsData || updated.decisionData || updated.traceData)) {
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
                        });
                    }, 0);
                }

                return {
                    ...prev,
                    [sym]: updated
                };
            });
        });

    }, [addAnalysis]);

    // Daily Brief Action
    const loadDailyBrief = useCallback(async (forceRefresh = false) => {
        if (dailyBriefState.loading && dailyBriefState.data && !forceRefresh) {
            return;
        }
        if (dailyBriefState.data && !forceRefresh) {
            return;
        }

        setDailyBriefState(prev => ({
            ...prev,
            loading: true,
            error: null
        }));

        try {
            const q = forceRefresh ? '?refresh=true' : '';
            const briefJson = await apiFetch(`${API_BASE_URL}/daily-brief${q}`);
            let screenerJson = null;
            let activeTab = 'movers';

            try {
                screenerJson = await apiFetch(`${API_BASE_URL}/daily-brief/screener`);
                if (screenerJson && screenerJson.rows && screenerJson.rows.length > 0) {
                    activeTab = 'growth';
                }
            } catch (screenerErr) {
                console.warn('Failed to load screener data, falling back to movers', screenerErr);
            }

            setDailyBriefState(prev => ({
                ...prev,
                data: briefJson,
                screenerData: screenerJson,
                activeTab,
                deepStatus: briefJson.deep_refresh || prev.deepStatus,
                loading: false
            }));
        } catch (e) {
            setDailyBriefState(prev => ({
                ...prev,
                error: e.message || 'Failed to load daily brief',
                loading: false
            }));
        }
    }, [dailyBriefState.loading, dailyBriefState.data]);

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
            analyses, analyzeTicker,
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
