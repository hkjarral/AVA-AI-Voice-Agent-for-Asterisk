import React, { createContext, useContext, useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';

// Decode the `exp` claim (seconds since epoch) from a JWT without verifying it.
// Returns null if the token is malformed or has no exp.
const getTokenExpiry = (jwt: string): number | null => {
    try {
        const payload = JSON.parse(atob(jwt.split('.')[1]));
        return typeof payload.exp === 'number' ? payload.exp : null;
    } catch {
        return null;
    }
};

interface User {
    username: string;
    disabled?: boolean;
}

interface AuthContextType {
    user: User | null;
    token: string | null;
    login: (username: string, password: string) => Promise<void>;
    logout: () => void;
    changePassword: (oldPassword: string, newPassword: string) => Promise<void>;
    isAuthenticated: boolean;
    loading: boolean;
    mustChangePassword: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [user, setUser] = useState<User | null>(null);
    const [token, setToken] = useState<string | null>(localStorage.getItem('token'));
    const [loading, setLoading] = useState(true);
    const [mustChangePassword, setMustChangePassword] = useState(false);

    // Ensure auth headers are available synchronously during initial render.
    // Many pages fetch config on mount; relying only on async interceptor setup can race and produce 401s + empty UI.
    if (token) {
        axios.defaults.headers.common.Authorization = `Bearer ${token}`;
    } else {
        delete axios.defaults.headers.common.Authorization;
    }

    useEffect(() => {
        console.log("AuthProvider effect: token =", token);
        if (token) {
            // Verify token and get user info
            axios.get('/api/auth/me', {
                headers: { Authorization: `Bearer ${token}` }
            })
                .then(response => {
                    console.log("AuthProvider: user loaded");
                    setUser(response.data);
                    setMustChangePassword(response.data.must_change_password || false);
                    setLoading(false);
                })
                .catch((err) => {
                    console.log("AuthProvider: user load failed", err);
                    logout();
                    setLoading(false);
                });
        } else {
            console.log("AuthProvider: no token");
            setLoading(false);
        }
    }, [token]);

    const login = async (username: string, password: string) => {
        const formData = new FormData();
        formData.append('username', username);
        formData.append('password', password);

        const response = await axios.post('/api/auth/login', formData);
        const newToken = response.data.access_token;
        const needsPasswordChange = response.data.must_change_password || false;

        localStorage.setItem('token', newToken);
        setToken(newToken);
        setMustChangePassword(needsPasswordChange);

        // Get user info immediately
        const userResponse = await axios.get('/api/auth/me', {
            headers: { Authorization: `Bearer ${newToken}` }
        });
        setUser(userResponse.data);
    };

    const logout = () => {
        localStorage.removeItem('token');
        setToken(null);
        setUser(null);
        setMustChangePassword(false);
    };

    const changePassword = async (oldPassword: string, newPassword: string) => {
        await axios.post('/api/auth/change-password', {
            old_password: oldPassword,
            new_password: newPassword
        }, {
            headers: { Authorization: `Bearer ${token}` }
        });
        // Clear the flag after successful password change
        setMustChangePassword(false);
    };

    // Add interceptor to attach token to all requests
    useEffect(() => {
        const interceptor = axios.interceptors.request.use(config => {
            if (!config.headers) {
                config.headers = {};
            }
            if (token && !(config.headers as any).Authorization) {
                (config.headers as any).Authorization = `Bearer ${token}`;
            }
            return config;
        });

        return () => {
            axios.interceptors.request.eject(interceptor);
        };
    }, [token]);

    // Proactively warn before the token expires so the operator can re-login
    // without losing in-progress form state to an abrupt 401 logout.
    useEffect(() => {
        if (!token) return;

        const exp = getTokenExpiry(token);
        if (!exp) return;

        const msUntilExpiry = exp * 1000 - Date.now();
        const warnLeadMs = 5 * 60 * 1000; // warn 5 minutes before expiry
        const msUntilWarn = msUntilExpiry - warnLeadMs;

        // Already within the warning window (but not yet expired): warn now.
        if (msUntilExpiry > 0 && msUntilWarn <= 0) {
            toast.warning('Your session is about to expire. Save your work and log in again to avoid losing changes.', {
                duration: 10000,
            });
            return;
        }

        if (msUntilWarn <= 0) return; // already expired; 401 handling will log out

        const timer = setTimeout(() => {
            toast.warning('Your session will expire soon. Save your work and log in again to avoid losing changes.', {
                duration: 10000,
            });
        }, msUntilWarn);

        return () => clearTimeout(timer);
    }, [token]);

    // Add interceptor to handle 401s
    useEffect(() => {
        const interceptor = axios.interceptors.response.use(
            response => response,
            error => {
                if (error.response && error.response.status === 401) {
                    logout();
                }
                return Promise.reject(error);
            }
        );

        return () => {
            axios.interceptors.response.eject(interceptor);
        };
    }, []);

    return (
        <AuthContext.Provider value={{ user, token, login, logout, changePassword, isAuthenticated: !!user, loading, mustChangePassword }}>
            {children}
        </AuthContext.Provider>
    );
};

export const useAuth = () => {
    const context = useContext(AuthContext);
    if (!context) {
        throw new Error('useAuth must be used within an AuthProvider');
    }
    return context;
};
