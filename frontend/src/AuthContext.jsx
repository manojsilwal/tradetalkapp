import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { API_BASE_URL, apiFetch, setToken, getToken, clearToken } from './api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
    const [user, setUser]       = useState(null);
    const [loading, setLoading] = useState(true);

    /**
     * Called after Google sign-in succeeds.
     * `googleToken` is the credential string from @react-oauth/google.
     */
    const login = useCallback(async (googleToken) => {
        const data = await apiFetch(`${API_BASE_URL}/auth/google`, {
            method: 'POST',
            body:   JSON.stringify({ token: googleToken }),
        });
        setToken(data.token);
        setUser({
            user_id:  data.user_id,
            email:    data.email,
            name:     data.name,
            avatar:   data.avatar,
            dev_mode: data.dev_mode,
        });
        return data;
    }, []);

    // On mount, try to restore session from localStorage
    useEffect(() => {
        const restore = async () => {
            const token = getToken();
            if (!token) {
                // Auto dev-login to disable login requirement
                try {
                    await login('dev');
                } catch (e) {
                    console.error('Auto dev login failed', e);
                } finally {
                    setLoading(false);
                }
                return;
            }
            try {
                const data = await apiFetch(`${API_BASE_URL}/auth/me`);
                setUser(data);
            } catch {
                clearToken();  // expired or invalid
            } finally {
                setLoading(false);
            }
        };
        restore();
    }, [login]);



    const logout = useCallback(() => {
        clearToken();
        setUser(null);
    }, []);

    return (
        <AuthContext.Provider value={{ user, loading, login, logout }}>
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    const ctx = useContext(AuthContext);
    if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
    return ctx;
}
