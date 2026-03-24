import { useNavigate } from 'react-router-dom';

const CONCEPT_MAP = {
    'short interest': { path: '/learning', label: 'Learn about Short Interest' },
    'short squeeze': { path: '/learning', label: 'Learn about Short Squeezes' },
    'social sentiment': { path: '/learning', label: 'Learn about Sentiment Analysis' },
    'polymarket': { path: '/learning', label: 'Learn about Prediction Markets' },
    'fundamentals': { path: '/learning', label: 'Learn about Fundamental Analysis' },
    'momentum': { path: '/learning', label: 'Learn about Momentum Trading' },
    'value investing': { path: '/learning', label: 'Learn about Value Investing' },
    'fama-french': { path: '/learning', label: 'Learn about Fama-French Factors' },
    'sharpe ratio': { path: '/learning', label: 'Learn about Risk-Adjusted Returns' },
    'max drawdown': { path: '/learning', label: 'Learn about Drawdown Risk' },
    'cagr': { path: '/learning', label: 'Learn about CAGR' },
    'vix': { path: '/macro', label: 'View VIX on Macro Dashboard' },
    'credit stress': { path: '/macro', label: 'View Credit Stress Index' },
    'yield curve': { path: '/macro', label: 'View Yield Curve Data' },
};

export function EducationTooltip({ term }) {
    const navigate = useNavigate();
    const key = Object.keys(CONCEPT_MAP).find(k => term.toLowerCase().includes(k));
    if (!key) return null;
    const { path, label } = CONCEPT_MAP[key];

    return (
        <button
            onClick={() => navigate(path)}
            title={label}
            style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '2px 8px', marginLeft: 4, borderRadius: 6,
                fontSize: 11, fontWeight: 600, cursor: 'pointer',
                border: '1px solid rgba(139,92,246,0.3)',
                background: 'rgba(139,92,246,0.08)',
                color: '#a78bfa',
            }}
        >
            📚 {label}
        </button>
    );
}

export function enrichText(text) {
    if (!text || typeof text !== 'string') return text;
    const terms = Object.keys(CONCEPT_MAP);
    let enriched = text;
    for (const term of terms) {
        const regex = new RegExp(`\\b(${term})\\b`, 'gi');
        if (regex.test(enriched)) {
            enriched = enriched.replace(regex, `**$1**`);
            break;
        }
    }
    return enriched;
}
