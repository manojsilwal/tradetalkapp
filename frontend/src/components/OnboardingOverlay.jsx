import { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import { LayoutDashboard, TrendingUp, Brain, FlaskConical, Globe, Sparkles } from 'lucide-react';

const ONBOARDING_KEY = 'k2_onboarding_complete';

const STEPS = [
    {
        icon: <Sparkles size={32} />,
        title: 'Welcome to TradeTalk',
        description: 'Your AI-powered investment analysis platform. Let us show you around.',
        color: '#3b82f6',
    },
    {
        icon: <LayoutDashboard size={32} />,
        title: 'Valuation Dashboard',
        description: 'Enter any stock ticker to get a multi-factor AI swarm analysis — short interest, social sentiment, fundamentals, and prediction markets analyzed simultaneously.',
        color: '#10b981',
    },
    {
        icon: <Brain size={32} />,
        title: 'AI Debate',
        description: '5 specialized AI agents (Bull, Bear, Macro, Value, Momentum) debate any ticker with RAG-powered historical context. Get a panel verdict with confidence scores.',
        color: '#8b5cf6',
    },
    {
        icon: <FlaskConical size={32} />,
        title: 'Strategy Lab',
        description: 'Backtest investment strategies using proven presets (Fama-French, Momentum, Magic Formula) or describe your own strategy in plain English.',
        color: '#f59e0b',
    },
    {
        icon: <Globe size={32} />,
        title: 'Global Macro',
        description: 'Real-time macro dashboard with VIX, credit stress, sector rotation, Treasury yields, USD strength, and FRED economic data.',
        color: '#ef4444',
    },
    {
        icon: <TrendingUp size={32} />,
        title: 'You\'re Ready!',
        description: 'Start by entering a ticker in the Valuation Dashboard. Sign in to unlock XP, daily challenges, paper portfolio, and learning paths.',
        color: '#3b82f6',
    },
];

export default function OnboardingOverlay({ onComplete }) {
    const location = useLocation();
    const initialPathRef = useRef(location.pathname);
    const [step, setStep] = useState(0);
    const [visible, setVisible] = useState(false);

    useEffect(() => {
        if (!localStorage.getItem(ONBOARDING_KEY)) {
            setVisible(true);
        }
    }, []);

    const dismiss = useCallback(() => {
        localStorage.setItem(ONBOARDING_KEY, '1');
        setVisible(false);
        if (onComplete) onComplete();
    }, [onComplete]);

    // Auto-dismiss when user navigates away from first route (tour no longer blocks sidebar).
    useEffect(() => {
        if (!visible) return;
        if (location.pathname !== initialPathRef.current) {
            dismiss();
        }
    }, [location.pathname, visible, dismiss]);

    if (!visible) return null;

    const handleNext = () => {
        if (step < STEPS.length - 1) {
            setStep(step + 1);
        } else {
            dismiss();
        }
    };

    const handleSkip = () => dismiss();

    const current = STEPS[step];

    return (
        <div
            style={{
                position: 'fixed',
                inset: 0,
                zIndex: 9999,
                pointerEvents: 'none',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
            }}
            aria-hidden={false}
        >
            <div
                style={{
                    position: 'absolute',
                    inset: 0,
                    background: 'rgba(0,0,0,0.65)',
                    backdropFilter: 'blur(6px)',
                    pointerEvents: 'none',
                }}
            />
            <div style={{
                background: 'linear-gradient(145deg, #1e293b, #0f172a)',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 20, padding: '48px 40px', maxWidth: 480,
                textAlign: 'center', position: 'relative',
                pointerEvents: 'auto',
                zIndex: 1,
            }}>
                <div style={{
                    width: 64, height: 64, borderRadius: 16,
                    background: `${current.color}22`, color: current.color,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    margin: '0 auto 24px',
                }}>
                    {current.icon}
                </div>
                <h2 style={{ fontSize: 24, fontWeight: 700, marginBottom: 12, color: '#f8fafc' }}>
                    {current.title}
                </h2>
                <p style={{ color: '#94a3b8', fontSize: 15, lineHeight: 1.6, marginBottom: 12 }}>
                    {current.description}
                </p>
                <p style={{ color: '#64748b', fontSize: 12, lineHeight: 1.5, marginBottom: 24 }}>
                    You can use the app behind this tour, or skip anytime.
                </p>
                <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginBottom: 24 }}>
                    {STEPS.map((_, i) => (
                        <div key={i} style={{
                            width: i === step ? 24 : 8, height: 8, borderRadius: 4,
                            background: i === step ? current.color : 'rgba(255,255,255,0.15)',
                            transition: 'all 0.3s',
                        }} />
                    ))}
                </div>
                <div style={{ display: 'flex', gap: 12, justifyContent: 'center' }}>
                    <button onClick={handleSkip} style={{
                        padding: '10px 20px', borderRadius: 10, border: '1px solid rgba(255,255,255,0.1)',
                        background: 'transparent', color: '#94a3b8', fontSize: 14, cursor: 'pointer',
                    }}>
                        Skip tour
                    </button>
                    <button onClick={handleNext} style={{
                        padding: '10px 28px', borderRadius: 10, border: 'none',
                        background: `linear-gradient(135deg, ${current.color}, ${current.color}cc)`,
                        color: 'white', fontSize: 14, fontWeight: 600, cursor: 'pointer',
                    }}>
                        {step === STEPS.length - 1 ? 'Get Started' : 'Next'}
                    </button>
                </div>
            </div>
        </div>
    );
}
