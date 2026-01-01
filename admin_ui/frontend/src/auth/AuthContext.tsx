import React, { createContext, useContext, useState, useEffect } from 'react';
import axios from 'axios';

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
