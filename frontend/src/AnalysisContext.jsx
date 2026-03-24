import { createContext, useContext, useState, useCallback } from 'react';

const AnalysisContext = createContext(null);

export function AnalysisProvider({ children }) {
    const [recentAnalyses, setRecentAnalyses] = useState([]);
    const [recentDebates, setRecentDebates] = useState([]);

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

    return (
        <AnalysisContext.Provider value={{
            recentAnalyses, recentDebates,
            addAnalysis, addDebate,
            getLastAnalysis, getLastDebate,
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
