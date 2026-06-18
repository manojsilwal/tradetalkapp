import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { GoogleOAuthProvider } from '@react-oauth/google'
import './index.css'
import App from './App.jsx'
import { AuthProvider } from './AuthContext.jsx'
import { AnalysisProvider, useAnalysisHistory } from './AnalysisContext.jsx'
import { SessionProvider } from './SessionContext.jsx'
import { GOOGLE_CLIENT_ID } from './api.js'

/**
 * Inner wrapper: sits inside AnalysisProvider so it can pull resumeAnalysis
 * and pass it down to SessionProvider's onResume callback.
 */
function AppWithSession() {
    const { resumeAnalysis, shouldResumeAnalysis } = useAnalysisHistory();
    return (
        <SessionProvider onResume={resumeAnalysis} shouldResumeAnalysis={shouldResumeAnalysis}>
            <App />
        </SessionProvider>
    );
}

createRoot(document.getElementById('root')).render(
    <StrictMode>
        <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID || 'dev-placeholder'}>
            <AuthProvider>
                <BrowserRouter>
                    <AnalysisProvider>
                        <AppWithSession />
                    </AnalysisProvider>
                </BrowserRouter>
            </AuthProvider>
        </GoogleOAuthProvider>
    </StrictMode>,
)
