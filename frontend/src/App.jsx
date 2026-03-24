import { useState, useCallback } from 'react'
import { Activity, LayoutDashboard, Terminal, Globe, Swords, FlaskConical, Zap, BookOpen, Film, Target, LogOut, LogIn, Network, Coins } from 'lucide-react'
import ObserverUI from './ObserverUI'
import ConsumerUI from './ConsumerUI'
import MacroUI from './MacroUI'
import GoldAdvisorUI from './GoldAdvisorUI'
import DebateUI from './DebateUI'
import BacktestUI from './BacktestUI'
import NotificationBell from './NotificationBell'
import DailyChallengeUI from './DailyChallengeUI'
import LearningPathUI from './LearningPathUI'
import VideoAcademyUI from './VideoAcademyUI'
import PaperPortfolioUI from './PaperPortfolioUI'
import SystemMapUI from './SystemMapUI'
import XPBar from './components/XPBar'
import BadgePopup from './components/BadgePopup'
import AuthGate from './components/AuthGate'
import { useAuth } from './AuthContext'

/**
 * Wraps gamification tabs — shows AuthGate when user is not signed in.
 * Keeps the real component unmounted (so no hook violations).
 */
function GamificationTab({ user, featureName, featureIcon, children }) {
    if (!user) return <AuthGate featureName={featureName} featureIcon={featureIcon} />
    return children
}

function App() {
    const { user, login, logout } = useAuth()
    const [activeTab, setActiveTab] = useState('consumer')
    const [newBadges, setNewBadges] = useState([])
    const [xpFlash, setXpFlash]    = useState(null)

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
                                onClick={() => setActiveTab('challenge')}
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
                        className={`nav-btn ${activeTab === 'gold' ? 'active' : ''}`}
                        onClick={() => setActiveTab('gold')}
                    >
                        <Coins size={20} />
                        <span>Gold Advisor</span>
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

                    <button
                        className={`nav-btn ${activeTab === 'systemmap' ? 'active' : ''}`}
                        onClick={() => setActiveTab('systemmap')}
                    >
                        <Network size={20} />
                        <span>System Map</span>
                    </button>
                </nav>
            </aside>

            {/* Main Content Area */}
            <main className="main-content">
                <div className="content-wrapper fade-in">
                    {activeTab === 'consumer'  && <ConsumerUI />}
                    {activeTab === 'macro'     && <MacroUI />}
                    {activeTab === 'gold'      && <GoldAdvisorUI />}
                    {activeTab === 'debate'    && <DebateUI />}
                    {activeTab === 'backtest'  && <BacktestUI />}
                    {activeTab === 'observer'  && <ObserverUI />}
                    {activeTab === 'systemmap' && <SystemMapUI />}
                    {activeTab === 'challenge' && (
                        <GamificationTab user={user} featureName="Daily Challenges" featureIcon="⚡">
                            <DailyChallengeUI onXpGained={handleXpGained} />
                        </GamificationTab>
                    )}
                    {activeTab === 'portfolio' && (
                        <GamificationTab user={user} featureName="Paper Portfolio" featureIcon="📈">
                            <PaperPortfolioUI onXpGained={handleXpGained} />
                        </GamificationTab>
                    )}
                    {activeTab === 'learning' && (
                        <GamificationTab user={user} featureName="Learning Path" featureIcon="📚">
                            <LearningPathUI onXpGained={handleXpGained} />
                        </GamificationTab>
                    )}
                    {activeTab === 'academy' && (
                        <GamificationTab user={user} featureName="Video Academy" featureIcon="🎬">
                            <VideoAcademyUI onXpGained={handleXpGained} />
                        </GamificationTab>
                    )}
                </div>
            </main>
        </div>
    )
}

export default App
