import React, { useEffect, useRef, useState, Suspense, lazy } from 'react';
import { createBrowserRouter, RouterProvider, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom';
import { Toaster } from 'sonner';
import { ConfirmDialogProvider } from './hooks/useConfirmDialog';
import AppShell from './components/layout/AppShell';
import axios from 'axios';
import { loadProvidersPage } from './pages/loadProvidersPage';

// Auth
import { AuthProvider } from './auth/AuthContext';
import { RequireAuth } from './auth/RequireAuth';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const CallHistoryPage = lazy(() => import('./pages/CallHistoryPage'));
const CallSchedulingPage = lazy(() => import('./pages/CallSchedulingPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const ProvidersPage = lazy(loadProvidersPage);
const PipelinesPage = lazy(() => import('./pages/PipelinesPage'));
const AgentsPage = lazy(() => import('./pages/AgentsPage'));
const AgentEditorPage = lazy(() => import('./pages/AgentEditorPage'));
const MigrationStatusPage = lazy(() => import('./pages/MigrationStatusPage'));
const LegacyContextsRedirect = lazy(() => import('./pages/LegacyContextsRedirect'));
const ProfilesPage = lazy(() => import('./pages/ProfilesPage'));
const ToolsPage = lazy(() => import('./pages/ToolsPage'));
const MCPPage = lazy(() => import('./pages/MCPPage'));
const VADPage = lazy(() => import('./pages/Advanced/VADPage'));
const StreamingPage = lazy(() => import('./pages/Advanced/StreamingPage'));
const LLMPage = lazy(() => import('./pages/Advanced/LLMPage'));
const TransportPage = lazy(() => import('./pages/Advanced/TransportPage'));
const BargeInPage = lazy(() => import('./pages/Advanced/BargeInPage'));
const EnvPage = lazy(() => import('./pages/System/EnvPage'));
const DockerPage = lazy(() => import('./pages/System/DockerPage'));
const HelpPage = lazy(() => import('./pages/HelpPage'));
const Wizard = lazy(() => import('./pages/Wizard'));
const RawYamlPage = lazy(() => import('./pages/Advanced/RawYamlPage'));
const LogsPage = lazy(() => import('./pages/System/LogsPage'));
const TerminalPage = lazy(() => import('./pages/System/TerminalPage'));
const ModelsPage = lazy(() => import('./pages/System/ModelsPage'));
const UpdatesPage = lazy(() => import('./pages/System/UpdatesPage'));
const AsteriskPage = lazy(() => import('./pages/System/AsteriskPage'));

// Loading fallback for lazy-loaded pages
const PageLoader = () => (
    <div className="flex items-center justify-center min-h-[400px]">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
    </div>
);

// Auth/Setup Guard
export const SetupGuard = ({ children }: { children: React.ReactNode }) => {
    const [loading, setLoading] = useState(true);
    const [redirectingToWizard, setRedirectingToWizard] = useState(false);
    const [error, setError] = useState<{ kind: 'http' | 'transport'; message: string } | null>(null);
    const [retryCount, setRetryCount] = useState(0);
    const navigate = useNavigate();
    const location = useLocation();
    const navigateRef = useRef(navigate);
    const locationPathRef = useRef(location.pathname);
    navigateRef.current = navigate;
    locationPathRef.current = location.pathname;
    useEffect(() => {
        if (location.pathname === '/wizard') setRedirectingToWizard(false);
    }, [location.pathname]);

    useEffect(() => {
        let mounted = true;
        let activeController: AbortController | null = null;
        let retryTimer: ReturnType<typeof setTimeout> | null = null;

        const checkStatus = async (attempt = 0) => {
            activeController = new AbortController();
            const timeoutId = setTimeout(() => activeController?.abort(), 5000);
            try {
                const res = await axios.get('/api/wizard/status', {
                    signal: activeController.signal
                });

                if (mounted) {
                    // If not configured and not already on wizard, redirect
                    if ((!res.data.configured || res.data.resume_required) && locationPathRef.current !== '/wizard') {
                        setRedirectingToWizard(true);
                        navigateRef.current('/wizard');
                    }
                    setError(null);
                    setLoading(false);
                }
            } catch (err) {
                if (!mounted) return;
                console.error('Failed to check setup status', err);

                // A single delayed probe should not replace the entire UI with a
                // persistent outage screen. Retry once after a short pause.
                if (attempt === 0) {
                    retryTimer = setTimeout(() => checkStatus(1), 500);
                    return;
                }

                const status = axios.isAxiosError(err) ? err.response?.status : undefined;
                const timedOut = axios.isAxiosError(err) && err.code === 'ERR_CANCELED';
                setError({
                    kind: status ? 'http' : 'transport',
                    message: status
                        ? `The backend API returned HTTP ${status} while checking setup status.`
                        : timedOut
                            ? 'The backend API did not respond before the setup check timed out.'
                            : 'Could not reach the backend API.',
                });
                setLoading(false);
            } finally {
                clearTimeout(timeoutId);
            }
        };

        setLoading(true);
        setError(null);
        checkStatus();

        return () => {
            mounted = false;
            activeController?.abort();
            if (retryTimer) clearTimeout(retryTimer);
        };
    }, [retryCount]);

    const Frame = location.pathname === '/wizard' ? React.Fragment : AppShell;

    if (loading || (redirectingToWizard && location.pathname !== '/wizard')) {
        return (
            <Frame><div className="min-h-[400px] flex items-center justify-center flex-col gap-4">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
                <p className="text-muted-foreground text-sm">Connecting to system...</p>
            </div></Frame>
        );
    }

    if (error) {
        const isHttpError = error.kind === 'http';
        console.warn("SetupGuard: setup status check failed:", error.message);
        return (
            <Frame><div className="min-h-[400px] flex items-center justify-center flex-col gap-4 px-6 text-center">
                <h1 className="text-xl font-semibold">
                    {isHttpError ? 'Setup status check failed' : 'Backend unavailable'}
                </h1>
                <p className="text-muted-foreground text-sm max-w-md">
                    {isHttpError
                        ? `${error.message} The backend is reachable, but it could not complete the setup check. Check the Admin UI service logs and permissions, then retry.`
                        : `${error.message} The admin UI cannot load until the AVA backend is running and reachable. Check that the service is up, then retry.`}
                </p>
                <button
                    onClick={() => setRetryCount((c) => c + 1)}
                    className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
                >
                    Retry
                </button>
            </div></Frame>
        );
    }

    return <Frame>{children}</Frame>;
};

function ApplicationRoutes() {
    return (
        <Routes>
            <Route path="/login" element={<Suspense fallback={<PageLoader />}><LoginPage /></Suspense>} />

            <Route path="*" element={
                <RequireAuth>
                    <SetupGuard>
                        <Suspense fallback={<PageLoader />}>
                            <Routes>
                                {/* Setup Wizard Route (lazy) */}
                                <Route path="/wizard" element={<Wizard />} />

                                <Route path="/" element={<Dashboard />} />
                                <Route path="/history" element={<CallHistoryPage />} />
                                <Route path="/scheduling" element={<CallSchedulingPage />} />

                                {/* Core Configuration */}
                                <Route path="/providers" element={<ProvidersPage />} />
                                <Route path="/pipelines" element={<PipelinesPage />} />
                                <Route path="/agents" element={<AgentsPage />} />
                                <Route path="/agents/new" element={<AgentEditorPage />} />
                                <Route path="/agents/:slug/edit" element={<AgentEditorPage />} />
                                <Route path="/agents/migration" element={<MigrationStatusPage />} />
                                <Route path="/contexts" element={<LegacyContextsRedirect />} />
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
                                <Route path="/asterisk" element={<AsteriskPage />} />
                                <Route path="/logs" element={<LogsPage />} />
                                <Route path="/terminal" element={<TerminalPage />} />
                                <Route path="/models" element={<ModelsPage />} />
                                <Route path="/updates" element={<UpdatesPage />} />

                                {/* Help */}
                                <Route path="/help" element={<HelpPage />} />

                                {/* Fallback */}
                                <Route path="*" element={<Navigate to="/" replace />} />
                            </Routes>
                        </Suspense>
                    </SetupGuard>
                </RequireAuth>
            } />
        </Routes>
    );
}

const router = createBrowserRouter([{ path: '*', element: <ApplicationRoutes /> }]);

function App() {
    return <AuthProvider><ConfirmDialogProvider>
        <Toaster position="top-right" richColors closeButton />
        <RouterProvider router={router} />
    </ConfirmDialogProvider></AuthProvider>;
}

export default App;
