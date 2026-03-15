import { useState, useEffect, useCallback } from 'react'
import { Activity, LayoutDashboard, Terminal, Globe, Swords, FlaskConical, Zap, BookOpen, Film, Target, LogOut } from 'lucide-react'
import ObserverUI from './ObserverUI'
import ConsumerUI from './ConsumerUI'
import MacroUI from './MacroUI'
import DebateUI from './DebateUI'
import BacktestUI from './BacktestUI'
import NotificationBell from './NotificationBell'
import DailyChallengeUI from './DailyChallengeUI'
import LearningPathUI from './LearningPathUI'
import VideoAcademyUI from './VideoAcademyUI'
import PaperPortfolioUI from './PaperPortfolioUI'
import XPBar from './components/XPBar'
import BadgePopup from './components/BadgePopup'
import LoginScreen from './LoginScreen'
import { useAuth } from './AuthContext'
import { API_BASE_URL } from './api'

function App() {
    const { user, loading, logout } = useAuth()
    const [activeTab, setActiveTab] = useState('consumer')
    const [newBadges, setNewBadges] = useState([])
    const [xpFlash, setXpFlash]    = useState(null)

    // Show login screen if not authenticated
    if (loading) return (
        <div style={{ minHeight: '100vh', background: '#0f0f1a', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ width: 40, height: 40, border: '3px solid rgba(255,255,255,0.1)', borderTopColor: '#a78bfa', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
        </div>
    )
    if (!user) return <LoginScreen />

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
            <aside className="sidebar glass-panel">
                <div className="brand" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <Activity className="brand-icon" size={28} />
                        <h1>K2-Optimus</h1>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <NotificationBell />
                        <button
                            onClick={logout}
                            title={`Logged in as ${user.name || user.email}\nClick to sign out`}
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
                    </div>
                </div>

                {/* XP bar */}
                <XPBar />

                <nav className="nav-menu">
                    {/* --- Core tools --- */}
                    <div style={{ fontSize: 9, color: '#475569', fontWeight: 700, letterSpacing: 1.5, padding: '8px 12px 4px', marginTop: 4 }}>
                        ANALYSIS
                    </div>
                    <button
                        className={`nav-btn ${activeTab === 'consumer' ? 'active' : ''}`}
                        onClick={() => setActiveTab('consumer')}
                    >
                        <LayoutDashboard size={20} />
                        <span>Valuation Dashboard</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'macro' ? 'active' : ''}`}
                        onClick={() => setActiveTab('macro')}
                    >
                        <Globe size={20} />
                        <span>Global Macro</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'debate' ? 'active' : ''}`}
                        onClick={() => setActiveTab('debate')}
                    >
                        <Swords size={20} />
                        <span>AI Debate</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'backtest' ? 'active' : ''}`}
                        onClick={() => setActiveTab('backtest')}
                    >
                        <FlaskConical size={20} />
                        <span>Strategy Lab</span>
                    </button>

                    {/* --- Engagement features --- */}
                    <div style={{ fontSize: 9, color: '#475569', fontWeight: 700, letterSpacing: 1.5, padding: '12px 12px 4px' }}>
                        DAILY ENGAGEMENT
                    </div>

                    <button
                        className={`nav-btn ${activeTab === 'challenge' ? 'active' : ''}`}
                        onClick={() => setActiveTab('challenge')}
                    >
                        <Zap size={20} />
                        <span>Daily Challenge</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'portfolio' ? 'active' : ''}`}
                        onClick={() => setActiveTab('portfolio')}
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
                        onClick={() => setActiveTab('learning')}
                    >
                        <BookOpen size={20} />
                        <span>Learning Path</span>
                    </button>

                    <button
                        className={`nav-btn ${activeTab === 'academy' ? 'active' : ''}`}
                        onClick={() => setActiveTab('academy')}
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
                        onClick={() => setActiveTab('observer')}
                    >
                        <Terminal size={20} />
                        <span>Developer Trace</span>
                    </button>
                </nav>
            </aside>

            {/* Main Content Area */}
            <main className="main-content">
                <div className="content-wrapper fade-in">
                    {activeTab === 'consumer'   && <ConsumerUI />}
                    {activeTab === 'macro'       && <MacroUI />}
                    {activeTab === 'debate'      && <DebateUI />}
                    {activeTab === 'backtest'    && <BacktestUI />}
                    {activeTab === 'challenge'   && <DailyChallengeUI onXpGained={handleXpGained} />}
                    {activeTab === 'portfolio'   && <PaperPortfolioUI onXpGained={handleXpGained} />}
                    {activeTab === 'learning'    && <LearningPathUI onXpGained={handleXpGained} />}
                    {activeTab === 'academy'     && <VideoAcademyUI onXpGained={handleXpGained} />}
                    {activeTab === 'observer'    && <ObserverUI />}
                </div>
            </main>
        </div>
    )
}

export default App
