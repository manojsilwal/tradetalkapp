import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { API_BASE_URL, apiFetch, setToken, getToken, clearToken, GOOGLE_CLIENT_ID } from './api';
import { AUTH_REQUIRED, GUEST_USER } from './authConfig';

const AuthContext = createContext(null);

const IS_DEV_AUTH =
    !GOOGLE_CLIENT_ID || GOOGLE_CLIENT_ID === 'PLACEHOLDER_SET_AFTER_GOOGLE_SETUP';

function applySession(setUser, data) {
    setToken(data.token);
    setUser({
        user_id: data.user_id,
        email: data.email,
        name: data.name,
        avatar: data.avatar,
        dev_mode: data.dev_mode,
        has_password: data.has_password,
        is_admin: Boolean(data.is_admin),
    });
}

export function AuthProvider({ children }) {
    const [user, setUser] = useState(null);
    const [loading, setLoading] = useState(true);

    const login = useCallback(async (googleToken) => {
        const data = await apiFetch(`${API_BASE_URL}/auth/google`, {
            method: 'POST',
            body: JSON.stringify({ token: googleToken }),
        });
        applySession(setUser, data);
        return data;
    }, []);

    const googleSignup = useCallback(async (googleToken) => {
        const data = await apiFetch(`${API_BASE_URL}/auth/google/signup`, {
            method: 'POST',
            body: JSON.stringify({ token: googleToken }),
        });
        return data;
    }, []);

    const setPassword = useCallback(async (setupToken, password) => {
        return apiFetch(`${API_BASE_URL}/auth/set-password`, {
            method: 'POST',
            body: JSON.stringify({ setup_token: setupToken, password }),
        });
    }, []);

    const loginManual = useCallback(async (email, password) => {
        return apiFetch(`${API_BASE_URL}/auth/login-manual`, {
            method: 'POST',
            body: JSON.stringify({ email, password }),
        });
    }, []);

    const verifyOtp = useCallback(async (otpSessionId, code) => {
        const data = await apiFetch(`${API_BASE_URL}/auth/verify-otp`, {
            method: 'POST',
            body: JSON.stringify({ otp_session_id: otpSessionId, code }),
        });
        applySession(setUser, data);
        return data;
    }, []);

    const signup = useCallback(async (email, password, name) => {
        const data = await apiFetch(`${API_BASE_URL}/auth/signup`, {
            method: 'POST',
            body: JSON.stringify({ email, password, name }),
        });
        applySession(setUser, data);
        return data;
    }, []);

    const trySilentDevLogin = useCallback(async () => {
        if (!IS_DEV_AUTH) return;
        try {
            await login('dev');
        } catch (e) {
            if (!String(e.message || '').includes('Failed to fetch')) {
                console.warn('[Auth] silent dev login failed', e.message || e);
            }
        }
    }, [login]);

    useEffect(() => {
        const restore = async () => {
            if (!AUTH_REQUIRED) {
                setUser(GUEST_USER);
                setLoading(false);
                if (!getToken() && IS_DEV_AUTH) {
                    void trySilentDevLogin();
                }
                return;
            }

            const token = getToken();
            if (!token) {
                if (IS_DEV_AUTH) {
                    try {
                        await login('dev');
                    } catch (e) {
                        if (!e.message?.includes('Failed to fetch')) {
                            console.error('Auto dev login failed', e);
                        }
                    }
                }
                setLoading(false);
                return;
            }
            try {
                const data = await apiFetch(`${API_BASE_URL}/auth/me`);
                setUser(data);
            } catch {
                clearToken();
                if (IS_DEV_AUTH) {
                    try {
                        await login('dev');
                    } catch (e) {
                        if (!e.message?.includes('Failed to fetch')) {
                            console.error('Session restore failed', e);
                        }
                    }
                }
            } finally {
                setLoading(false);
            }
        };
        restore();
    }, [login, trySilentDevLogin]);

    const logout = useCallback(() => {
        clearToken();
        setUser(AUTH_REQUIRED ? null : GUEST_USER);
    }, []);

    useEffect(() => {
        const handleAuthExpired = () => {
            console.warn('[Auth] Session expired or invalid, logging out...');
            logout();
        };
        window.addEventListener('auth-expired', handleAuthExpired);
        return () => {
            window.removeEventListener('auth-expired', handleAuthExpired);
        };
    }, [logout]);

    return (
        <AuthContext.Provider
            value={{
                user,
                loading,
                login,
                googleSignup,
                setPassword,
                loginManual,
                verifyOtp,
                signup,
                logout,
                isDevAuth: IS_DEV_AUTH,
            }}
        >
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
    return ctx;
}
