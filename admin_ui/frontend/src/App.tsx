import React, { useEffect, useState, Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import AppShell from './components/layout/AppShell';
import Dashboard from './pages/Dashboard';
import CallHistoryPage from './pages/CallHistoryPage';
import CallSchedulingPage from './pages/CallSchedulingPage';
import axios from 'axios';

// Auth
import { AuthProvider } from './auth/AuthContext';
import { RequireAuth } from './auth/RequireAuth';
import LoginPage from './pages/LoginPage';

// Core Configuration Pages
import ProvidersPage from './pages/ProvidersPage';
import PipelinesPage from './pages/PipelinesPage';
import ContextsPage from './pages/ContextsPage';
import ProfilesPage from './pages/ProfilesPage';
import ToolsPage from './pages/ToolsPage';
import MCPPage from './pages/MCPPage';

// Advanced Configuration Pages
import VADPage from './pages/Advanced/VADPage';
import StreamingPage from './pages/Advanced/StreamingPage';
import LLMPage from './pages/Advanced/LLMPage';
import TransportPage from './pages/Advanced/TransportPage';
import BargeInPage from './pages/Advanced/BargeInPage';

// System Pages (eagerly loaded)
import EnvPage from './pages/System/EnvPage';
import DockerPage from './pages/System/DockerPage';

// Help
import HelpPage from './pages/HelpPage';

// Lazy-loaded heavy pages (code-splitting for better initial load)
const Wizard = lazy(() => import('./pages/Wizard'));
const RawYamlPage = lazy(() => import('./pages/Advanced/RawYamlPage'));
const LogsPage = lazy(() => import('./pages/System/LogsPage'));
const TerminalPage = lazy(() => import('./pages/System/TerminalPage'));
const ModelsPage = lazy(() => import('./pages/System/ModelsPage'));

// Loading fallback for lazy-loaded pages
const PageLoader = () => (
    <div className="flex items-center justify-center min-h-[400px]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
    </div>
);

// Auth/Setup Guard
const SetupGuard = ({ children }: { children: React.ReactNode }) => {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const navigate = useNavigate();
    const location = useLocation();

    useEffect(() => {
        let mounted = true;

        const checkStatus = async () => {
            try {
                // Add timeout to prevent hanging
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 5000);

                const res = await axios.get('/api/wizard/status', {
                    signal: controller.signal
                });

                clearTimeout(timeoutId);

                if (mounted) {
                    // If not configured and not already on wizard, redirect
                    if (!res.data.configured && location.pathname !== '/wizard') {
                        navigate('/wizard');
                    }
                    setLoading(false);
                }
            } catch (err) {
                console.error('Failed to check setup status', err);
                if (mounted) {
                    // If API fails, we assume not configured or backend down
                    // But we shouldn't block the UI entirely
                    setError('Failed to connect to backend API');
                    setLoading(false);
                }
            }
        };

        checkStatus();

        return () => {
            mounted = false;
        };
    }, [navigate, location.pathname]);

    if (loading) {
        console.log("SetupGuard: loading");
        return (
            <div className="min-h-screen flex items-center justify-center flex-col gap-4">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
                <p className="text-muted-foreground text-sm">Connecting to system...</p>
            </div>
        );
    }

    if (error && location.pathname !== '/wizard') {
        console.warn("Rendering app despite setup check failure:", error);
    }

    console.log("SetupGuard: rendering children");
    return <>{children}</>;
};

function App() {
    return (
        <AuthProvider>
            <Router>
                <Routes>
                    <Route path="/login" element={<LoginPage />} />

                    <Route path="*" element={
                        <RequireAuth>
                            <SetupGuard>
                                <Suspense fallback={<PageLoader />}>
                                    <Routes>
                                        {/* Setup Wizard Route (lazy) */}
                                        <Route path="/wizard" element={<Wizard />} />

                                        {/* Main Application Layout */}
                                        <Route element={<AppShell />}>
                                            <Route path="/" element={<Dashboard />} />
                                            <Route path="/history" element={<CallHistoryPage />} />
                                            <Route path="/scheduling" element={<CallSchedulingPage />} />

                                            {/* Core Configuration */}
                                            <Route path="/providers" element={<ProvidersPage />} />
                                            <Route path="/pipelines" element={<PipelinesPage />} />
                                            <Route path="/contexts" element={<ContextsPage />} />
                                            <Route path="/profiles" element={<ProfilesPage />} />
                                            <Route path="/tools" element={<ToolsPage />} />
                                            <Route path="/mcp" element={<MCPPage />} />

                                            {/* Advanced Settings */}
                                            <Route path="/vad" element={<VADPage />} />
                                            <Route path="/streaming" element={<StreamingPage />} />
                                            <Route path="/llm" element={<LLMPage />} />
                                            <Route path="/transport" element={<TransportPage />} />
                                            <Route path="/barge-in" element={<BargeInPage />} />
                                            <Route path="/yaml" element={<RawYamlPage />} />

                                            {/* System Management */}
                                            <Route path="/env" element={<EnvPage />} />
                                            <Route path="/docker" element={<DockerPage />} />
                                            <Route path="/logs" element={<LogsPage />} />
                                            <Route path="/terminal" element={<TerminalPage />} />
                                            <Route path="/models" element={<ModelsPage />} />

                                            {/* Help */}
                                            <Route path="/help" element={<HelpPage />} />

                                            {/* Fallback */}
                                            <Route path="*" element={<Navigate to="/" replace />} />
                                        </Route>
                                    </Routes>
                                </Suspense>
                            </SetupGuard>
                        </RequireAuth>
                    } />
                </Routes>
            </Router>
        </AuthProvider>
    );
}

export default App;
