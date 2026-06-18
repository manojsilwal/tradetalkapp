import React, { useState, useCallback, Suspense, useEffect, useRef } from 'react'
import { Routes, Route, useNavigate, useLocation } from 'react-router-dom'
import { Activity, LayoutDashboard, Terminal, Globe, Swords, FlaskConical, Zap, BookOpen, Film, Target, LogOut, LogIn, Network, Coins, Menu, Gauge, Scale, Sparkles, Newspaper, Cpu, Loader2, FileCode2, Home, Maximize2, Minimize2, Settings, Bell, HelpCircle, FileText, ChevronRight, ExternalLink, MoreHorizontal, BarChart2 } from 'lucide-react'
import NotificationBell from './NotificationBell'
import XPBar from './components/XPBar'
import BadgePopup from './components/BadgePopup'
import AuthGate from './components/AuthGate'
import { useAuth } from './AuthContext'
import { AUTH_REQUIRED } from './authConfig'
import OnboardingOverlay from './components/OnboardingOverlay.jsx'
import { API_BASE_URL, getToken, apiFetch } from './api'
import AppAssistantPanel from './AppAssistantPanel'
import { useAnalysisHistory, analysisStillRunning } from './AnalysisContext.jsx'
import SessionsTray from './components/SessionsTray'

const ConsumerUI = React.lazy(() => import('./UnifiedDashboardUI'))
const DecisionTerminalUI = React.lazy(() => import('./DecisionTerminalUI'))
const MacroUI = React.lazy(() => import('./MacroUI'))

const BacktestUI = React.lazy(() => import('./BacktestUI'))
const ObserverUI = React.lazy(() => import('./ObserverUI'))
const SwarmScoreUI = React.lazy(() => import('./SwarmScoreUI'))
const UbdsBenchmarkUI = React.lazy(() => import('./UbdsBenchmarkUI'))
const SystemMapUI = React.lazy(() => import('./SystemMapUI'))
const SystemDiagramsUI = React.lazy(() => import('./SystemDiagramsUI'))
const ApiCatalogUI = React.lazy(() => import('./ApiCatalogUI'))
const AcademyUI = React.lazy(() => import('./AcademyUI'))
const PaperPortfolioUI = React.lazy(() => import('./PaperPortfolioUI'))
const ChatUI = React.lazy(() => import('./ChatUI'))

const DailyBriefUI = React.lazy(() => import('./DailyBriefUI'))
const LlmCallsUI = React.lazy(() => import('./LlmCallsUI'))

/**
 * Wraps gamification tabs — shows AuthGate when auth is required and user is not signed in.
 * Set VITE_AUTH_REQUIRED=true to re-enable the sign-in wall.
 */
function GamificationTab({ user, featureName, featureIcon, children }) {
    if (AUTH_REQUIRED && !user) return <AuthGate featureName={featureName} featureIcon={featureIcon} />
    return children
}

const ROUTE_TO_KEY = {
    '/': 'daily_brief',
    '/dashboard': 'consumer',
    '/decision-terminal': 'decision_terminal',
    '/macro': 'macro',

    '/backtest': 'backtest',

    '/daily-brief': 'daily_brief',
    '/observer': 'observer',
    '/swarm-score': 'swarm_score',
    '/ubds': 'ubds',
    '/systemmap': 'systemmap',
    '/api-catalog': 'api_catalog',
    '/challenge': 'academy',
    '/portfolio': 'portfolio',
    '/learning': 'academy',
    '/chat': 'chat',
    '/llm-calls': 'llm_calls',
}

function App() {
    const { user, login, logout } = useAuth()
    const navigate = useNavigate()
    const location = useLocation()
    const [newBadges, setNewBadges] = useState([])
    const [xpFlash, setXpFlash]    = useState(null)
    const [sidebarCollapsed, setSidebarCollapsed] = useState(true)
    const [chatPrefetch, setChatPrefetch] = useState(null)
    const [moreMenuOpen, setMoreMenuOpen] = useState(false)
    const [unreadNotifications, setUnreadNotifications] = useState(3) // default to 3 to match design

    React.useEffect(() => {
        const headers = { 'Content-Type': 'application/json', ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}) }
        apiFetch(`${API_BASE_URL}/notifications/history`, { headers })
            .then(data => {
                if (data && typeof data.unread === 'number') {
                    setUnreadNotifications(data.unread);
                }
            })
            .catch(() => {});
    }, []);

    React.useEffect(() => {
        const token = getToken()
        const headers = { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }
        Promise.all([
            fetch(`${API_BASE_URL}/chat/bootstrap`).then((r) => r.json()),
            fetch(`${API_BASE_URL}/chat/user-context`, { headers }).then((r) => r.json()),
        ])
            .then(([boot, userCtx]) => setChatPrefetch({ boot, user: userCtx }))
            .catch(() => {})
    }, [user?.user_id])

    const activeTab = ROUTE_TO_KEY[location.pathname] || 'consumer'

    // Keep page context in sync so AppAssistantPanel knows which page the user is on
    React.useEffect(() => {
        const pageName = location.pathname === '/'
            ? 'dashboard'
            : location.pathname.replace('/', '').replace('-', ' ')
        window.__tt_page_context__ = {
            ...(window.__tt_page_context__ || {}),
            page: pageName,
        }
    }, [location.pathname])

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
                    background: 'linear-gradient(135deg, #7c3aed, #a78bfa)',
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

            {/* Premium Sidebar */}
            <aside className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
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
                        {user && !user.guest ? (
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
                                onClick={() => navigate('/login')}
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
                        className={`nav-btn ${activeTab === 'daily_brief' ? 'active' : ''}`}
                        onClick={() => { navigate('/'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/' ? 'page' : undefined}
                    >
                        <Home size={20} />
                        <span>Home</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'consumer' ? 'active' : ''}`}
                        onClick={() => { navigate('/dashboard'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/dashboard' ? 'page' : undefined}
                    >
                        <LayoutDashboard size={20} />
                        <span>Stock Analysis</span>
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
                        className={`nav-btn ${activeTab === 'backtest' ? 'active' : ''}`}
                        onClick={() => { navigate('/backtest'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/backtest' ? 'page' : undefined}
                    >
                        <FlaskConical size={20} />
                        <span>Strategy Lab</span>
                    </button>



                    {/* --- Engagement features --- */}
                    <div style={{ fontSize: 9, color: '#475569', fontWeight: 700, letterSpacing: 1.5, padding: '12px 12px 4px' }}>
                        DAILY ENGAGEMENT
                    </div>

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
                        className={`nav-btn ${activeTab === 'academy' ? 'active' : ''}`}
                        onClick={() => { navigate('/learning'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/learning' ? 'page' : undefined}
                    >
                        <BookOpen size={20} />
                        <span>Investor Academy</span>
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
                        className={`nav-btn ${activeTab === 'llm_calls' ? 'active' : ''}`}
                        onClick={() => { navigate('/llm-calls'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/llm-calls' ? 'page' : undefined}
                    >
                        <Cpu size={20} />
                        <span>LLM Call Log</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'swarm_score' ? 'active' : ''}`}
                        onClick={() => { navigate('/swarm-score'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/swarm-score' ? 'page' : undefined}
                    >
                        <Sparkles size={20} />
                        <span>SwarmScore Eval</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'ubds' ? 'active' : ''}`}
                        onClick={() => { navigate('/ubds'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/ubds' ? 'page' : undefined}
                    >
                        <Gauge size={20} />
                        <span>UBDS Benchmark</span>
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
                        className={`nav-btn ${activeTab === 'api_catalog' ? 'active' : ''}`}
                        onClick={() => { navigate('/api-catalog'); setSidebarCollapsed(true); }}
                        aria-current={location.pathname === '/api-catalog' ? 'page' : undefined}
                    >
                        <FileCode2 size={20} />
                        <span>API Catalog</span>
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
                            <Route path="/" element={<DailyBriefUI />} />
                            <Route path="/dashboard" element={<ConsumerUI />} />
                            <Route path="/decision-terminal" element={<DecisionTerminalUI />} />
                            <Route path="/macro" element={<MacroUI />} />

                            <Route path="/chat" element={<ChatUI prefetch={chatPrefetch} />} />

                            <Route path="/backtest" element={<BacktestUI />} />

                            <Route path="/daily-brief" element={<DailyBriefUI />} />
                            <Route path="/observer" element={<ObserverUI />} />
                            <Route path="/swarm-score" element={<SwarmScoreUI />} />
                            <Route path="/ubds" element={<UbdsBenchmarkUI />} />
                            <Route path="/systemmap" element={<SystemMapUI />} />
                            <Route path="/api-catalog" element={<ApiCatalogUI />} />
                            <Route path="/system-diagrams" element={<SystemDiagramsUI />} />
                            <Route path="/challenge" element={
                                <GamificationTab user={user} featureName="Investor Academy" featureIcon="📚">
                                    <AcademyUI onXpGained={handleXpGained} />
                                </GamificationTab>
                            } />
                            <Route path="/portfolio" element={
                                <GamificationTab user={user} featureName="Paper Portfolio" featureIcon="📈">
                                    <PaperPortfolioUI onXpGained={handleXpGained} />
                                </GamificationTab>
                            } />
                            <Route path="/llm-calls" element={<LlmCallsUI />} />
                            <Route path="/login" element={<AuthGate featureName="Your Account" featureIcon="👤" />} />
                            <Route path="/learning" element={
                                <GamificationTab user={user} featureName="Investor Academy" featureIcon="📚">
                                    <AcademyUI onXpGained={handleXpGained} />
                                </GamificationTab>
                            } />
                        </Routes>
                    </Suspense>
                </div>
            </main>

            {/* App-level persistent assistant panel — always available, survives route changes */}
            <AppAssistantPanel prefetch={chatPrefetch} />

            {/* Global sessions tray — shows all active/completed analyses regardless of page */}
            <SessionsTray />

            {/* Mobile Bottom Navigation Bar (Hidden on Desktop) */}
            <nav className="mobile-bottom-nav">
                <button
                    className={`mobile-bottom-nav-btn ${activeTab === 'daily_brief' && !moreMenuOpen ? 'active' : ''}`}
                    onClick={() => { navigate('/'); setMoreMenuOpen(false); }}
                >
                    <Home size={22} />
                    <span>Home</span>
                </button>
                <button
                    className={`mobile-bottom-nav-btn ${activeTab === 'consumer' && !moreMenuOpen ? 'active' : ''}`}
                    onClick={() => { navigate('/dashboard'); setMoreMenuOpen(false); }}
                >
                    <BarChart2 size={22} />
                    <span>Analysis</span>
                </button>
                <button
                    className={`mobile-bottom-nav-btn ${activeTab === 'macro' && !moreMenuOpen ? 'active' : ''}`}
                    onClick={() => { navigate('/macro'); setMoreMenuOpen(false); }}
                >
                    <Globe size={22} />
                    <span>Macro</span>
                </button>
                <button
                    className={`mobile-bottom-nav-btn ${activeTab === 'backtest' && !moreMenuOpen ? 'active' : ''}`}
                    onClick={() => { navigate('/backtest'); setMoreMenuOpen(false); }}
                >
                    <FlaskConical size={22} />
                    <span>Lab</span>
                </button>
                <button
                    className={`mobile-bottom-nav-btn ${moreMenuOpen ? 'active' : ''}`}
                    onClick={() => setMoreMenuOpen(!moreMenuOpen)}
                >
                    <MoreHorizontal size={22} />
                    <span>More</span>
                </button>
            </nav>

            {/* Slide-up Bottom Sheet Drawer (Mobile Only) */}
            {moreMenuOpen && (
                <div className="mobile-drawer-backdrop" onClick={() => setMoreMenuOpen(false)}>
                    <div className="mobile-drawer" onClick={(e) => e.stopPropagation()}>
                        <div className="drawer-handle" onClick={() => setMoreMenuOpen(false)}></div>
                        
                        {/* Profile Header section */}
                        <div className="drawer-profile-section" onClick={() => { setMoreMenuOpen(false); navigate('/portfolio'); }}>
                            <div className="drawer-profile-avatar-container">
                                {user && user.avatar ? (
                                    <img src={user.avatar} className="drawer-profile-avatar" alt={user.name} />
                                ) : (
                                    <img 
                                        src="https://images.unsplash.com/photo-1534528741775-53994a69daeb?q=80&w=256&auto=format&fit=crop" 
                                        className="drawer-profile-avatar" 
                                        alt="Default Avatar" 
                                    />
                                )}
                            </div>
                            <div className="drawer-profile-info">
                                <div className="drawer-profile-name">{user && user.name ? user.name : 'Elias Thorne'}</div>
                                <div className="drawer-profile-tier">{user && !user.guest ? 'QUANT PRO TIER' : 'QUANT PRO TIER'}</div>
                            </div>
                            <ChevronRight className="drawer-chevron-right" size={20} />
                        </div>
                        
                        <div className="drawer-divider"></div>
                        
                        {/* Scrollable menu content */}
                        <div className="drawer-menu-content">
                            {/* Standard options from mockup */}
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); alert('Settings are managed via browser preferences.'); }}>
                                <Settings size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">Settings</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>
                            
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/chat'); }}>
                                <Bell size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">Notifications</span>
                                {unreadNotifications > 0 && (
                                    <span className="drawer-notification-badge">{unreadNotifications} NEW</span>
                                )}
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>
                            
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/learning'); }}>
                                <HelpCircle size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">Help & Support</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>
                            
                            <a 
                                href="https://tradetalk.app/terms" 
                                target="_blank" 
                                rel="noreferrer" 
                                className="drawer-item"
                                onClick={() => setMoreMenuOpen(false)}
                                style={{ textDecoration: 'none', color: 'inherit' }}
                            >
                                <FileText size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">Terms of Service</span>
                                <ExternalLink size={16} className="drawer-chevron-arrow" style={{ opacity: 0.6 }} />
                            </a>

                            <div className="drawer-section-title">TradeTalk Features</div>
                            
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/portfolio'); }}>
                                <Target size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">Paper Portfolio</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>
                            
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/learning'); }}>
                                <BookOpen size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">Investor Academy</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>

                            {/* Dev Suite Section */}
                            <div className="drawer-section-title">Developer Suite</div>
                            
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/observer'); }}>
                                <Terminal size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">Developer Trace</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>
                            
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/llm-calls'); }}>
                                <Cpu size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">LLM Call Log</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>
                            
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/swarm-score'); }}>
                                <Sparkles size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">SwarmScore Eval</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>
                            
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/ubds'); }}>
                                <Gauge size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">UBDS Benchmark</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>
                            
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/systemmap'); }}>
                                <Network size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">System Map</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>
                            
                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/api-catalog'); }}>
                                <FileCode2 size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">API Catalog</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>

                            <div className="drawer-item" onClick={() => { setMoreMenuOpen(false); navigate('/system-diagrams'); }}>
                                <Network size={20} className="drawer-item-icon" />
                                <span className="drawer-item-label">System Diagrams</span>
                                <ChevronRight className="drawer-chevron-arrow" size={16} />
                            </div>
                        </div>
                        
                        <div className="drawer-divider"></div>
                        
                        {/* Log Out Action button */}
                        {user && !user.guest ? (
                            <div 
                                className="drawer-item log-out-item" 
                                onClick={() => { setMoreMenuOpen(false); logout(); }}
                            >
                                <LogOut size={20} className="drawer-item-icon log-out-icon" />
                                <span className="drawer-item-label log-out-label">Log Out</span>
                            </div>
                        ) : (
                            <div 
                                className="drawer-item log-out-item" 
                                onClick={() => { setMoreMenuOpen(false); navigate('/login'); }}
                            >
                                <LogIn size={20} className="drawer-item-icon log-out-icon" />
                                <span className="drawer-item-label log-out-label" style={{ color: '#60a5fa' }}>Log In</span>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}

function GlobalLoadingBar() {
    const location = useLocation()
    const { analyses } = useAnalysisHistory()
    const pageTicker = (window.__tt_page_context__?.ticker || '').trim().toUpperCase()
    const loadingTicker = (pageTicker && analysisStillRunning(analyses[pageTicker]))
        ? pageTicker
        : Object.keys(analyses).find((ticker) => analysisStillRunning(analyses[ticker]))
    const activeAnalysis = loadingTicker ? analyses[loadingTicker] : null

    const [prevTicker, setPrevTicker] = useState('')
    const [isMinimized, setIsMinimized] = useState(true)
    const [isMobile, setIsMobile] = useState(window.innerWidth < 768)

    useEffect(() => {
        const handleResize = () => setIsMobile(window.innerWidth < 768)
        window.addEventListener('resize', handleResize)
        return () => window.removeEventListener('resize', handleResize)
    }, [])

    useEffect(() => {
        if (loadingTicker && loadingTicker !== prevTicker) {
            setIsMinimized(true)
            setPrevTicker(loadingTicker)
        }
    }, [loadingTicker, prevTicker])

    if (location.pathname === '/dashboard' || location.pathname === '/decision-terminal' || location.pathname === '/login') {
        return null
    }

    if (!activeAnalysis) return null

    const steps = [
        { label: 'Retrieving RAG knowledge base & metrics', done: !activeAnalysis.metricsLoading && !activeAnalysis.scorecardLoading },
        { label: 'Assembling multi-agent debate chamber', done: !activeAnalysis.debateLoading },
        { label: 'Executing swarm consensus trace', done: !activeAnalysis.traceLoading },
        { label: 'Synthesizing valuation terminal & roadmap', done: !activeAnalysis.decisionLoading },
        { label: 'Scanning prediction market contracts', done: !activeAnalysis.predMarketsLoading }
    ]

    const doneCount = steps.filter(s => s.done).length
    const progressPct = Math.round((doneCount / steps.length) * 100)
    const activeStep = steps.find(s => !s.done)?.label || 'Completing analysis...'
    const currentActiveIdx = steps.findIndex(s => !s.done)

    if (isMinimized) {
        return (
            <div
                style={{
                    position: 'fixed',
                    bottom: '24px',
                    left: isMobile ? '24px' : '264px',
                    zIndex: 9998,
                    background: 'linear-gradient(185deg, #0d1222 0%, #080a12 100%)',
                    border: '1px solid rgba(59, 130, 246, 0.35)',
                    borderRadius: '16px',
                    padding: '14px 18px',
                    width: '320px',
                    boxShadow: '0 10px 30px rgba(0, 0, 0, 0.5), 0 0 20px rgba(59, 130, 246, 0.15)',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '10px',
                    animation: 'fadeIn 0.3s ease-out',
                }}
            >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <Loader2
                            size={16}
                            className="spinner"
                            style={{
                                color: '#3b82f6',
                                animation: 'spin 1.2s linear infinite'
                            }}
                        />
                        <span style={{ color: '#f8fafc', fontSize: '0.9rem', fontWeight: 700 }}>
                            Analyzing {loadingTicker}
                        </span>
                    </div>
                    <button
                        onClick={() => setIsMinimized(false)}
                        title="Expand view"
                        style={{
                            background: 'transparent',
                            border: 'none',
                            color: '#3b82f6',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            padding: '4px',
                            borderRadius: '4px',
                            transition: 'background 0.2s',
                        }}
                        onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(59, 130, 246, 0.1)'}
                        onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                    >
                        <Maximize2 size={14} />
                    </button>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: '#94a3b8' }}>
                        <span style={{ textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap', maxWidth: '190px' }}>
                            {activeStep}
                        </span>
                        <span style={{ fontWeight: 700, color: '#3b82f6' }}>{progressPct}%</span>
                    </div>
                    <div style={{ width: '100%', height: '6px', borderRadius: '3px', background: 'rgba(255, 255, 255, 0.04)', overflow: 'hidden' }}>
                        <div
                            style={{
                                height: '100%',
                                width: `${progressPct}%`,
                                background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)',
                                transition: 'width 0.4s ease-in-out',
                            }}
                        />
                    </div>
                </div>
            </div>
        )
    }

    return (
        <div className="global-loading-modal-backdrop">
            <div className="global-loading-modal">
                <button
                    onClick={() => setIsMinimized(true)}
                    title="Minimize to background"
                    style={{
                        position: 'absolute',
                        top: '24px',
                        right: '24px',
                        background: 'rgba(255, 255, 255, 0.04)',
                        border: '1px solid rgba(255, 255, 255, 0.1)',
                        borderRadius: '10px',
                        padding: '8px 12px',
                        color: '#94a3b8',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        fontSize: '0.8rem',
                        fontWeight: 600,
                        transition: 'all 0.2s',
                        zIndex: 10,
                    }}
                    onMouseEnter={(e) => {
                        e.currentTarget.style.background = 'rgba(255, 255, 255, 0.08)';
                        e.currentTarget.style.color = '#f8fafc';
                        e.currentTarget.style.borderColor = 'rgba(59, 130, 246, 0.4)';
                    }}
                    onMouseLeave={(e) => {
                        e.currentTarget.style.background = 'rgba(255, 255, 255, 0.04)';
                        e.currentTarget.style.color = '#94a3b8';
                        e.currentTarget.style.borderColor = 'rgba(255, 255, 255, 0.1)';
                    }}
                >
                    <Minimize2 size={14} />
                    Minimize
                </button>

                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '20px' }}>
                    <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        width: '100px',
                        height: '100px',
                        borderRadius: '50%',
                        background: 'rgba(59, 130, 246, 0.08)',
                        border: '2px solid rgba(59, 130, 246, 0.3)',
                        boxShadow: '0 0 30px rgba(59, 130, 246, 0.25), inset 0 0 15px rgba(59, 130, 246, 0.1)',
                        animation: 'pulse 2s ease-in-out infinite',
                        marginBottom: '8px'
                    }}>
                        <Loader2
                            size={44}
                            style={{
                                color: '#3b82f6',
                                animation: 'spin 1.2s linear infinite'
                            }}
                        />
                    </div>
                    <div>
                        <h2 style={{
                            fontSize: '2.2rem',
                            fontWeight: 900,
                            letterSpacing: '-0.025em',
                            margin: '0 0 8px 0',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: '12px',
                            background: 'linear-gradient(to right, #ffffff, #e2e8f0)',
                            WebkitBackgroundClip: 'text',
                            WebkitTextFillColor: 'transparent'
                        }}>
                            Analyzing {loadingTicker}
                        </h2>
                        <span style={{
                            fontSize: '0.8rem',
                            color: '#3b82f6',
                            padding: '4px 14px',
                            borderRadius: '8px',
                            background: 'rgba(59, 130, 246, 0.12)',
                            fontWeight: 800,
                            border: '1px solid rgba(59, 130, 246, 0.3)',
                            display: 'inline-block',
                            letterSpacing: '0.05em',
                            textTransform: 'uppercase',
                            boxShadow: '0 0 10px rgba(59, 130, 246, 0.1)'
                        }}>
                            Swarm Engine Active
                        </span>
                    </div>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', margin: '16px 0 8px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '1rem', fontWeight: 700 }}>
                    <span style={{ color: '#cbd5e1' }}>{activeStep}</span>
                    <span style={{ color: '#3b82f6', fontFamily: 'monospace', fontSize: '1.4rem', fontWeight: 800 }}>{progressPct}%</span>
                  </div>
                  <div style={{ width: '100%', height: '12px', borderRadius: '6px', background: 'rgba(255, 255, 255, 0.04)', border: '1px solid rgba(255, 255, 255, 0.05)', overflow: 'hidden' }}>
                      <div
                          style={{
                              height: '100%',
                              width: `${progressPct}%`,
                              background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)',
                              borderRadius: '6px',
                              transition: 'width 0.4s cubic-bezier(0.4, 0, 0.2, 1)',
                              boxShadow: '0 0 16px rgba(59, 130, 246, 0.6)',
                          }}
                      />
                  </div>
                </div>

                <div style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '10px',
                    textAlign: 'left',
                    borderTop: '1px solid rgba(255, 255, 255, 0.08)',
                    paddingTop: '24px',
                    marginTop: '8px'
                }}>
                    {steps.map((step, idx) => {
                        const isDone = step.done;
                        const isActive = idx === currentActiveIdx;
                        const isPending = idx > currentActiveIdx;

                        let bulletColor = '#475569';
                        let bulletBg = 'transparent';
                        let textColor = '#64748b'; // pending
                        let fontWeight = 500;

                        if (isDone) {
                            bulletColor = '#10b981'; // emerald
                            bulletBg = 'rgba(16, 185, 129, 0.1)';
                            textColor = '#94a3b8'; // slightly dimmed completed step
                        } else if (isActive) {
                            bulletColor = '#3b82f6'; // bright blue
                            bulletBg = 'rgba(59, 130, 246, 0.15)';
                            textColor = '#f8fafc'; // highlighted active text
                            fontWeight = 700;
                        }

                        return (
                            <div
                                key={idx}
                                style={{
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: '16px',
                                    fontSize: '0.95rem',
                                    color: textColor,
                                    transition: 'all 0.3s ease',
                                    opacity: isDone ? 0.75 : 1,
                                    padding: '8px 12px',
                                    borderRadius: '8px',
                                    background: isActive ? 'rgba(255, 255, 255, 0.02)' : 'transparent',
                                    border: isActive ? '1px solid rgba(59, 130, 246, 0.1)' : '1px solid transparent'
                                }}
                            >
                                <div style={{
                                    width: '22px',
                                    height: '22px',
                                    borderRadius: '50%',
                                    border: `1.5px solid ${bulletColor}`,
                                    display: 'flex',
                                    alignItems: 'center',
                                    justifyContent: 'center',
                                    background: bulletBg,
                                    boxShadow: isActive ? '0 0 8px rgba(59, 130, 246, 0.4)' : 'none',
                                    flexShrink: 0,
                                    transition: 'all 0.3s ease',
                                    animation: isActive ? 'pulse 1.5s infinite' : 'none'
                                }}>
                                    {isDone ? (
                                        <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#10b981' }} />
                                    ) : isActive ? (
                                        <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#3b82f6', animation: 'pulse 1s infinite' }} />
                                    ) : (
                                        <span style={{ width: '4px', height: '4px', borderRadius: '50%', background: '#475569' }} />
                                    )}
                                </div>
                                <span style={{ fontWeight }}>
                                    {step.label}
                                </span>
                            </div>
                        )
                    })}
                </div>
            </div>
        </div>
    )
}

export default App
