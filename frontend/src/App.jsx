import React, { useState, useCallback, Suspense } from 'react'
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom'
import { Activity, LayoutDashboard, Terminal, Globe, Swords, FlaskConical, Zap, BookOpen, Film, Target, LogOut, LogIn, Network, Coins, Menu, Gauge, MessageCircle, Scale } from 'lucide-react'
import NotificationBell from './NotificationBell'
import XPBar from './components/XPBar'
import BadgePopup from './components/BadgePopup'
import AuthGate from './components/AuthGate'
import { useAuth } from './AuthContext'
import OnboardingOverlay from './components/OnboardingOverlay.jsx'
import { API_BASE_URL, getToken } from './api'

const ConsumerUI = React.lazy(() => import('./ConsumerUI'))
const DecisionTerminalUI = React.lazy(() => import('./DecisionTerminalUI'))
const MacroUI = React.lazy(() => import('./MacroUI'))
const GoldAdvisorUI = React.lazy(() => import('./GoldAdvisorUI'))
const DebateUI = React.lazy(() => import('./DebateUI'))
const BacktestUI = React.lazy(() => import('./BacktestUI'))
const ObserverUI = React.lazy(() => import('./ObserverUI'))
const SystemMapUI = React.lazy(() => import('./SystemMapUI'))
const SystemDiagramsUI = React.lazy(() => import('./SystemDiagramsUI'))
const DailyChallengeUI = React.lazy(() => import('./DailyChallengeUI'))
const PaperPortfolioUI = React.lazy(() => import('./PaperPortfolioUI'))
const LearningPathUI = React.lazy(() => import('./LearningPathUI'))
const VideoAcademyUI = React.lazy(() => import('./VideoAcademyUI'))
const ChatUI = React.lazy(() => import('./ChatUI'))
const ScorecardUI = React.lazy(() => import('./ScorecardUI'))

/**
 * Wraps gamification tabs — shows AuthGate when user is not signed in.
 * Keeps the real component unmounted (so no hook violations).
 */
function GamificationTab({ user, featureName, featureIcon, children }) {
    if (!user) return <AuthGate featureName={featureName} featureIcon={featureIcon} />
    return children
}

const ROUTE_TO_KEY = {
    '/': 'consumer',
    '/decision-terminal': 'decision_terminal',
    '/macro': 'macro',
    '/gold': 'gold',
    '/debate': 'debate',
    '/backtest': 'backtest',
    '/scorecard': 'scorecard',
    '/observer': 'observer',
    '/systemmap': 'systemmap',
    '/challenge': 'challenge',
    '/portfolio': 'portfolio',
    '/learning': 'learning',
    '/academy': 'academy',
    '/chat': 'chat',
}

function App() {
    const { user, login, logout } = useAuth()
    const navigate = useNavigate()
    const location = useLocation()
    const [newBadges, setNewBadges] = useState([])
    const [xpFlash, setXpFlash]    = useState(null)
    const [sidebarCollapsed, setSidebarCollapsed] = useState(true)
    const [chatPrefetch, setChatPrefetch] = useState(null)

    React.useEffect(() => {
        const headers = { 'Content-Type': 'application/json', ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}) }
        Promise.all([
            fetch(`${API_BASE_URL}/chat/bootstrap`).then((r) => r.json()),
            fetch(`${API_BASE_URL}/chat/user-context`, { headers }).then((r) => r.json()),
        ])
            .then(([boot, user]) => setChatPrefetch({ boot, user }))
            .catch(() => {})
    }, [])

    const activeTab = ROUTE_TO_KEY[location.pathname] || 'consumer'

    const handleXpGained = useCallback((progress) => {
        if (!progress) return
        if (progress.new_badges?.length > 0) {
            setNewBadges(b => [...b, ...progress.new_badges])
        }
        if (progress.xp_awarded) {
            setXpFlash(`+${progress.xp_awarded} XP`)
            setTimeout(() => setXpFlash(null), 2000)
        }
    }, [])

    return (
        <div className="app-container">
            <OnboardingOverlay />

            {/* XP flash toast */}
            {xpFlash && (
                <div style={{
                    position: 'fixed', top: 20, right: 24, zIndex: 9998,
                    background: 'linear-gradient(135deg, rgba(124,58,237,0.95), rgba(167,139,250,0.95))',
                    backdropFilter: 'blur(16px)',
                    borderRadius: 12, padding: '10px 18px',
                    fontSize: 14, fontWeight: 800, color: '#fff',
                    boxShadow: '0 4px 20px rgba(124,58,237,0.4)',
                    animation: 'fadeIn 0.3s',
                    pointerEvents: 'none',
                }}>
                    ⚡ {xpFlash}
                </div>
            )}

            {/* Badge popup */}
            <BadgePopup badges={newBadges} />

            {/* Premium Glassmorphic Sidebar */}
            <aside className={`sidebar glass-panel ${sidebarCollapsed ? 'collapsed' : ''}`}>
                <div className="brand" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <Activity className="brand-icon" size={28} />
                        <h1>TradeTalk</h1>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <NotificationBell />
                        <button className="mobile-menu-toggle" onClick={() => setSidebarCollapsed(!sidebarCollapsed)} aria-label="Toggle navigation menu">
                            <Menu size={20} />
                        </button>
                        {user ? (
                            <button
                                onClick={logout}
                                title={`${user.name || user.email} — click to sign out`}
                                style={{
                                    width: 30, height: 30, borderRadius: '50%', border: 'none',
                                    background: user.avatar ? 'transparent' : 'rgba(124,58,237,0.3)',
                                    cursor: 'pointer', padding: 0, overflow: 'hidden',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                }}
                            >
                                {user.avatar
                                    ? <img src={user.avatar} alt={user.name} style={{ width: 30, height: 30, borderRadius: '50%' }} />
                                    : <LogOut size={14} color="#a78bfa" />
                                }
                            </button>
                        ) : (
                            <button
                                onClick={() => navigate('/challenge')}
                                title="Sign in to track XP, streaks & portfolio"
                                style={{
                                    width: 30, height: 30, borderRadius: '50%', border: '1px solid rgba(167,139,250,0.4)',
                                    background: 'rgba(124,58,237,0.15)',
                                    cursor: 'pointer', padding: 0,
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                }}
                            >
                                <LogIn size={14} color="#a78bfa" />
                            </button>
                        )}
                    </div>
                </div>

                {/* XP bar */}
                <XPBar />

                <nav className="nav-menu" aria-label="Main navigation">
                    {/* --- Core tools --- */}
                    <div style={{ fontSize: 9, color: '#475569', fontWeight: 700, letterSpacing: 1.5, padding: '8px 12px 4px', marginTop: 4 }}>
                        ANALYSIS
                    </div>
                    <button
                        className={`nav-btn ${activeTab === 'consumer' ? 'active' : ''}`}
                        onClick={() => { navigate('/'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/' ? 'page' : undefined}
                    >
                        <LayoutDashboard size={20} />
                        <span>Valuation Dashboard</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'decision_terminal' ? 'active' : ''}`}
                        onClick={() => { navigate('/decision-terminal'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/decision-terminal' ? 'page' : undefined}
                    >
                        <Gauge size={20} />
                        <span>Decision Terminal</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'macro' ? 'active' : ''}`}
                        onClick={() => { navigate('/macro'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/macro' ? 'page' : undefined}
                    >
                        <Globe size={20} />
                        <span>Global Macro</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'gold' ? 'active' : ''}`}
                        onClick={() => { navigate('/gold'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/gold' ? 'page' : undefined}
                    >
                        <Coins size={20} />
                        <span>Gold Advisor</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'chat' ? 'active' : ''}`}
                        onClick={() => { navigate('/chat'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/chat' ? 'page' : undefined}
                    >
                        <MessageCircle size={20} />
                        <span>Assistant</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'debate' ? 'active' : ''}`}
                        onClick={() => { navigate('/debate'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/debate' ? 'page' : undefined}
                    >
                        <Swords size={20} />
                        <span>AI Debate</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'backtest' ? 'active' : ''}`}
                        onClick={() => { navigate('/backtest'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/backtest' ? 'page' : undefined}
                    >
                        <FlaskConical size={20} />
                        <span>Strategy Lab</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'scorecard' ? 'active' : ''}`}
                        onClick={() => { navigate('/scorecard'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/scorecard' ? 'page' : undefined}
                    >
                        <Scale size={20} />
                        <span>Risk/Return Scorecard</span>
                    </button>

                    {/* --- Engagement features --- */}
                    <div style={{ fontSize: 9, color: '#475569', fontWeight: 700, letterSpacing: 1.5, padding: '12px 12px 4px' }}>
                        DAILY ENGAGEMENT
                    </div>

                    <button
                        className={`nav-btn ${activeTab === 'challenge' ? 'active' : ''}`}
                        onClick={() => { navigate('/challenge'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/challenge' ? 'page' : undefined}
                    >
                        <Zap size={20} />
                        <span>Daily Challenge</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'portfolio' ? 'active' : ''}`}
                        onClick={() => { navigate('/portfolio'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/portfolio' ? 'page' : undefined}
                    >
                        <Target size={20} />
                        <span>Paper Portfolio</span>
                    </button>

                    {/* --- Learning --- */}
                    <div style={{ fontSize: 9, color: '#475569', fontWeight: 700, letterSpacing: 1.5, padding: '12px 12px 4px' }}>
                        LEARNING
                    </div>

                    <button
                        className={`nav-btn ${activeTab === 'learning' ? 'active' : ''}`}
                        onClick={() => { navigate('/learning'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/learning' ? 'page' : undefined}
                    >
                        <BookOpen size={20} />
                        <span>Learning Path</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'academy' ? 'active' : ''}`}
                        onClick={() => { navigate('/academy'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/academy' ? 'page' : undefined}
                    >
                        <Film size={20} />
                        <span>Video Academy</span>
                    </button>

                    {/* --- Developer --- */}
                    <div style={{ fontSize: 9, color: '#475569', fontWeight: 700, letterSpacing: 1.5, padding: '12px 12px 4px' }}>
                        DEVELOPER
                    </div>

                    <button
                        className={`nav-btn ${activeTab === 'observer' ? 'active' : ''}`}
                        onClick={() => { navigate('/observer'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/observer' ? 'page' : undefined}
                    >
                        <Terminal size={20} />
                        <span>Developer Trace</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'systemmap' ? 'active' : ''}`}
                        onClick={() => { navigate('/systemmap'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/systemmap' ? 'page' : undefined}
                    >
                        <Network size={20} />
                        <span>System Map</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'systemdiagrams' ? 'active' : ''}`}
                        onClick={() => { navigate('/system-diagrams'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/system-diagrams' ? 'page' : undefined}
                    >
                        <Network size={20} />
                        <span>System Diagrams</span>
                    </button>
                </nav>
            </aside>

            {/* Main Content Area */}
            <main className="main-content">
                <div className="content-wrapper fade-in">
                    <Suspense fallback={<div style={{ padding: 40, textAlign: 'center', color: '#94a3b8' }}>Loading...</div>}>
                        <Routes>
                            <Route path="/" element={<ConsumerUI />} />
                            <Route path="/decision-terminal" element={<DecisionTerminalUI />} />
                            <Route path="/macro" element={<MacroUI />} />
                            <Route path="/gold" element={<GoldAdvisorUI />} />
                            <Route path="/chat" element={<ChatUI prefetch={chatPrefetch} />} />
                            <Route path="/debate" element={<DebateUI />} />
                            <Route path="/backtest" element={<BacktestUI />} />
                            <Route path="/scorecard" element={<ScorecardUI />} />
                            <Route path="/observer" element={<ObserverUI />} />
                            <Route path="/systemmap" element={<SystemMapUI />} />
                            <Route path="/system-diagrams" element={<SystemDiagramsUI />} />
                            <Route path="/challenge" element={
                                <GamificationTab user={user} featureName="Daily Challenges" featureIcon="⚡">
                                    <DailyChallengeUI onXpGained={handleXpGained} />
                                </GamificationTab>
                            } />
                            <Route path="/portfolio" element={
                                <GamificationTab user={user} featureName="Paper Portfolio" featureIcon="📈">
                                    <PaperPortfolioUI onXpGained={handleXpGained} />
                                </GamificationTab>
                            } />
                            <Route path="/learning" element={
                                <GamificationTab user={user} featureName="Learning Path" featureIcon="📚">
                                    <LearningPathUI onXpGained={handleXpGained} />
                                </GamificationTab>
                            } />
                            <Route path="/academy" element={
                                <GamificationTab user={user} featureName="Video Academy" featureIcon="🎬">
                                    <VideoAcademyUI onXpGained={handleXpGained} />
                                </GamificationTab>
                            } />
                        </Routes>
                    </Suspense>
                </div>
            </main>
        </div>
    )
}

export default App
