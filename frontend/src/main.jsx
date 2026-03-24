import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { GoogleOAuthProvider } from '@react-oauth/google'
import './index.css'
import App from './App.jsx'
import { AuthProvider } from './AuthContext.jsx'
import { AnalysisProvider } from './AnalysisContext.jsx'
import { GOOGLE_CLIENT_ID } from './api.js'

createRoot(document.getElementById('root')).render(
    <StrictMode>
        <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID || 'dev-placeholder'}>
            <AuthProvider>
                <BrowserRouter>
                    <AnalysisProvider>
                        <App />
                    </AnalysisProvider>
                </BrowserRouter>
            </AuthProvider>
        </GoogleOAuthProvider>
    </StrictMode>,
)
