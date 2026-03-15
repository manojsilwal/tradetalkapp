import { useState } from 'react'
import { Activity, LayoutDashboard, Terminal, Globe, Swords, FlaskConical } from 'lucide-react'
import ObserverUI from './ObserverUI'
import ConsumerUI from './ConsumerUI'
import MacroUI from './MacroUI'
import DebateUI from './DebateUI'
import BacktestUI from './BacktestUI'
import NotificationBell from './NotificationBell'

function App() {
    const [activeTab, setActiveTab] = useState('consumer')

    return (
        <div className="app-container">
            {/* Premium Glassmorphic Sidebar */}
            <aside className="sidebar glass-panel">
                <div className="brand" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <Activity className="brand-icon" size={28} />
                        <h1>K2-Optimus</h1>
                    </div>
                    <NotificationBell />
                </div>

                <nav className="nav-menu">
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
                    {activeTab === 'consumer' && <ConsumerUI />}
                    {activeTab === 'macro' && <MacroUI />}
                    {activeTab === 'debate' && <DebateUI />}
                    {activeTab === 'backtest' && <BacktestUI />}
                    {activeTab === 'observer' && <ObserverUI />}
                </div>
            </main>
        </div>
    )
}

export default App
