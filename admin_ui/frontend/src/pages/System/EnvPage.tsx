import axios from 'axios';

import { useState, useEffect } from 'react';
import { Save, Eye, EyeOff, RefreshCw, AlertTriangle, AlertCircle, CheckCircle, XCircle, Loader2 } from 'lucide-react';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { FormInput, FormSelect, FormSwitch } from '../../components/ui/FormComponents';

import { useAuth } from '../../auth/AuthContext';

// SecretInput defined OUTSIDE EnvPage to prevent re-creation on every render
const SecretInput = ({ 
    label, 
    placeholder,
    value,
    onChange,
    showSecret,
    onToggleSecret
}: { 
    label: string;
    placeholder?: string;
    value: string;
    onChange: (value: string) => void;
    showSecret: boolean;
    onToggleSecret: () => void;
}) => (
    <div className="relative">
        <FormInput
            label={label}
            type={showSecret ? 'text' : 'password'}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
        />
        <button
            type="button"
            onClick={onToggleSecret}
            className="absolute right-3 top-[38px] text-muted-foreground hover:text-foreground"
        >
            {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
    </div>
);

const EnvPage = () => {
    const { token, loading: authLoading } = useAuth();
    const [env, setEnv] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({});
    const [ariTestResult, setAriTestResult] = useState<{success: boolean; message?: string; error?: string; asterisk_version?: string} | null>(null);
    const [ariTesting, setAriTesting] = useState(false);
    const [pendingRestart, setPendingRestart] = useState(false);
    const [restartingEngine, setRestartingEngine] = useState(false);
    const [applyPlan, setApplyPlan] = useState<Array<{ service: string; method: string; endpoint: string }>>([]);
    const [changedKeys, setChangedKeys] = useState<string[]>([]);
    const [showAdvancedKokoro, setShowAdvancedKokoro] = useState(false);

    const [error, setError] = useState<string | null>(null);

    const kokoroMode = (env['KOKORO_MODE'] || 'local').toLowerCase();
    const showHfKokoroMode = showAdvancedKokoro || kokoroMode === 'hf';

    useEffect(() => {
        if (!authLoading && token) {
            fetchEnv();
        }
    }, [authLoading, token]);

    const fetchEnv = async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await axios.get('/api/config/env', {
                headers: { Authorization: `Bearer ${token}` }
            });
            const loadedEnv = res.data || {};
            setEnv(loadedEnv);
            if ((loadedEnv['KOKORO_MODE'] || '').toLowerCase() === 'hf') {
                setShowAdvancedKokoro(true);
            }
        } catch (err: any) {
            console.error('Failed to load env', err);
            setError(err.response?.data?.detail || 'Failed to load environment variables');
            if (err.response && err.response.status === 401) {
                // AuthContext handles logout
            }
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        // Validate ARI Port
        const port = parseInt(env['ASTERISK_ARI_PORT'] || '8088');
        if (isNaN(port) || port < 1 || port > 65535) {
            alert('Invalid ARI Port. Must be between 1 and 65535.');
            return;
        }

        setSaving(true);
        try {
            const response = await axios.post('/api/config/env', env, {
                headers: { Authorization: `Bearer ${token}` }
            });
            const plan = (response.data?.apply_plan || []) as Array<{ service: string; method: string; endpoint: string }>;
            const keys = (response.data?.changed_keys || []) as string[];
            setApplyPlan(plan);
            setChangedKeys(keys);
            setPendingRestart(plan.length > 0);
            const services = Array.from(new Set(plan.map((p) => p.service))).sort();
            alert(
                plan.length > 0
                    ? `Environment saved. Apply changes by restarting: ${services.join(', ')}`
                    : 'Environment saved.'
            );
        } catch (err: any) {
            console.error('Failed to save env', err);
            if (err.response && err.response.status === 401) {
                alert('Session expired. Please login again.');
            } else {
                alert('Failed to save environment variables');
            }
        } finally {
            setSaving(false);
        }
    };

    const updateEnv = (key: string, value: string) => {
        setEnv(prev => ({ ...prev, [key]: value }));
    };

    const toggleSecret = (key: string) => {
        setShowSecrets(prev => ({ ...prev, [key]: !prev[key] }));
    };

    const handleApplyChanges = async (force: boolean = false) => {
        setRestartingEngine(true);
        try {
            // Apply in safe order: local-ai-server → ai-engine → admin-ui
            const ordered = ['local-ai-server', 'ai-engine', 'admin-ui'];
            const planByService = new Map(applyPlan.map((p) => [p.service, p]));

            // Warn if applying includes admin-ui restart (can invalidate sessions)
            const touchesAdminUI = planByService.has('admin-ui');
            const jwtChanged = changedKeys.includes('JWT_SECRET');
            if (touchesAdminUI) {
                const msg = jwtChanged
                    ? 'This will restart Admin UI and JWT_SECRET changed. You will be logged out. Continue?'
                    : 'This will restart Admin UI and may interrupt your session. Continue?';
                if (!window.confirm(msg)) return;
            }

            for (const service of ordered) {
                const step = planByService.get(service);
                if (!step) continue;

                if (service === 'ai-engine') {
                    const response = await axios.post(`${step.endpoint}?force=${force}`, {}, {
                        headers: { Authorization: `Bearer ${token}` }
                    });

                    if (response.data.status === 'warning') {
                        const confirmForce = window.confirm(
                            `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`
                        );
                        if (confirmForce) {
                            setRestartingEngine(false);
                            return handleApplyChanges(true);
                        }
                        return;
                    }

                    if (response.data.status === 'degraded') {
                        alert(`AI Engine restarted but may not be fully healthy: ${response.data.output || 'Health check issue'}\n\nPlease verify manually.`);
                        return;
                    }
                } else {
                    await axios.post(step.endpoint, {}, {
                        headers: { Authorization: `Bearer ${token}` }
                    });
                }
            }

            setPendingRestart(false);
            setApplyPlan([]);
            alert('Changes applied.');
        } catch (error: any) {
            alert(`Failed to apply changes: ${error.response?.data?.detail || error.message}`);
        } finally {
            setRestartingEngine(false);
        }
    };

    const testAriConnection = async () => {
        setAriTesting(true);
        setAriTestResult(null);
        
        try {
            const response = await axios.post('/api/system/test-ari', {
                host: env['ASTERISK_HOST'] || '127.0.0.1',
                port: parseInt(env['ASTERISK_ARI_PORT'] || '8088'),
                username: env['ASTERISK_ARI_USERNAME'] || '',
                password: env['ASTERISK_ARI_PASSWORD'] || '',
                scheme: env['ASTERISK_ARI_WEBSOCKET_SCHEME'] === 'wss' ? 'https' : 'http',
                ssl_verify: env['ASTERISK_ARI_SSL_VERIFY'] !== 'false'
            }, {
                headers: { Authorization: `Bearer ${token}` }
            });
            
            setAriTestResult(response.data);
        } catch (err: any) {
            setAriTestResult({
                success: false,
                error: err.response?.data?.detail || 'Failed to test connection'
            });
        } finally {
            setAriTesting(false);
        }
    };

    // Helper to render SecretInput with current state
    const renderSecretInput = (label: string, envKey: string, placeholder?: string) => (
        <SecretInput
            label={label}
            placeholder={placeholder}
            value={env[envKey] || ''}
            onChange={(value) => updateEnv(envKey, value)}
            showSecret={showSecrets[envKey] || false}
            onToggleSecret={() => toggleSecret(envKey)}
        />
    );

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading environment variables...</div>;

    if (error) return (
        <div className="p-8 text-center text-destructive">
            <AlertTriangle className="w-8 h-8 mx-auto mb-4" />
            <h3 className="text-lg font-semibold">Error Loading Configuration</h3>
            <p className="mt-2">{error}</p>
            <button
                onClick={fetchEnv}
                className="mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
            >
                Retry
            </button>
        </div>
    );

    // Define known keys to exclude from "Other Variables"
	    const knownKeys = [
	        'ASTERISK_HOST', 'ASTERISK_ARI_USERNAME', 'ASTERISK_ARI_PASSWORD',
	        'DIAG_ENABLE_TAPS', 'DIAG_TAP_PRE_SECS', 'DIAG_TAP_POST_SECS', 'DIAG_TAP_OUTPUT_DIR',
	        'DIAG_EGRESS_SWAP_MODE', 'DIAG_EGRESS_FORCE_MULAW', 'DIAG_ATTACK_MS',
	        'LOG_LEVEL', 'LOG_FORMAT', 'LOG_COLOR', 'LOG_SHOW_TRACEBACKS',
	        'STREAMING_LOG_LEVEL', 'LOG_TO_FILE', 'LOG_FILE_PATH',
	        'LOCAL_WS_URL', 'LOCAL_WS_CONNECT_TIMEOUT', 'LOCAL_WS_RESPONSE_TIMEOUT', 'LOCAL_WS_CHUNK_MS',
	        'LOCAL_WS_HOST', 'LOCAL_WS_PORT', 'LOCAL_WS_AUTH_TOKEN',
	        // STT backends
	        'LOCAL_STT_BACKEND', 'LOCAL_STT_MODEL_PATH',
        'KROKO_URL', 'KROKO_API_KEY', 'KROKO_LANGUAGE', 'KROKO_EMBEDDED', 'KROKO_MODEL_PATH', 'KROKO_PORT',
        'SHERPA_MODEL_PATH',
        // TTS backends
        'LOCAL_TTS_BACKEND', 'LOCAL_TTS_MODEL_PATH',
        'KOKORO_VOICE', 'KOKORO_LANG', 'KOKORO_MODEL_PATH',
        // LLM
        'LOCAL_LLM_MODEL_PATH', 'LOCAL_LLM_THREADS',
        'LOCAL_LLM_CONTEXT', 'LOCAL_LLM_BATCH', 'LOCAL_LLM_MAX_TOKENS', 'LOCAL_LLM_TEMPERATURE', 'LOCAL_LLM_INFER_TIMEOUT_SEC',
        // Other
        'OPENAI_API_KEY', 'GROQ_API_KEY', 'DEEPGRAM_API_KEY', 'GOOGLE_API_KEY', 'RESEND_API_KEY', 'ELEVENLABS_API_KEY', 'CARTESIA_API_KEY', 'JWT_SECRET',
        'AI_NAME', 'AI_ROLE', 'ASTERISK_ARI_PORT', 'ASTERISK_ARI_WEBSOCKET_SCHEME',
        'HEALTH_CHECK_LOCAL_AI_URL', 'HEALTH_CHECK_AI_ENGINE_URL'
    ];

    const otherSettings = Object.keys(env).filter(k => !knownKeys.includes(k));

    // Helper to check boolean values (handles 'true', '1', 'on', etc.)
    const isTrue = (val: string | undefined) => {
        if (!val) return false;
        const v = val.toLowerCase();
        return v === 'true' || v === '1' || v === 'on' || v === 'yes';
    };

    return (
        <div className="space-y-6">
            <div className={`${pendingRestart ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    {pendingRestart && applyPlan.length > 0
                        ? `Pending changes require restart of: ${Array.from(new Set(applyPlan.map((p) => p.service))).sort().join(', ')}`
                        : 'Changes to environment variables require a service restart to take effect.'}
                </div>
                <button
                    onClick={() => handleApplyChanges(false)}
                    disabled={restartingEngine}
                    className={`flex items-center text-xs px-3 py-1.5 rounded transition-colors ${
                        pendingRestart 
                            ? 'bg-orange-500 text-white hover:bg-orange-600 font-medium' 
                            : 'bg-yellow-500/20 hover:bg-yellow-500/30'
                    } disabled:opacity-50`}
                >
                    {restartingEngine ? (
                        <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
                    ) : (
                        <RefreshCw className="w-3 h-3 mr-1.5" />
                    )}
                    {restartingEngine ? 'Applying...' : 'Apply Changes'}
                </button>
            </div>
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Environment Variables</h1>
                    <p className="text-muted-foreground mt-1">
                        Manage system-level configuration and API secrets.
                    </p>
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={() => {
                            if (window.confirm('Warning: Running the Setup Wizard will overwrite your current configuration. Are you sure you want to continue?')) {
                                window.location.href = '/wizard';
                            }
                        }}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                    >
                        <RefreshCw className="w-4 h-4 mr-2" />
                        Run Setup Wizard
                    </button>
                    <button
                        onClick={fetchEnv}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
                    >
                        <RefreshCw className="w-4 h-4 mr-2" />
                        Refresh
                    </button>
                    <button
                        onClick={handleSave}
                        disabled={saving}
                        className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                    >
                        <Save className="w-4 h-4 mr-2" />
                        {saving ? 'Saving...' : 'Save Changes'}
                    </button>
                </div>
            </div>

            {/* Asterisk Settings */}
            <ConfigSection title="Asterisk Settings" description="Connection details for the Asterisk server.">
                <ConfigCard>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <FormInput
                            label="Asterisk Host"
                            value={env['ASTERISK_HOST'] || ''}
                            onChange={(e) => updateEnv('ASTERISK_HOST', e.target.value)}
                        />
                        <FormInput
                            label="ARI Username"
                            value={env['ASTERISK_ARI_USERNAME'] || ''}
                            onChange={(e) => updateEnv('ASTERISK_ARI_USERNAME', e.target.value)}
                        />
                        {renderSecretInput('ARI Password', 'ASTERISK_ARI_PASSWORD')}
                        <FormInput
                            label="ARI Port"
                            type="number"
                            value={env['ASTERISK_ARI_PORT'] || '8088'}
                            onChange={(e) => updateEnv('ASTERISK_ARI_PORT', e.target.value)}
                        />
                        <FormSelect
                            label="WebSocket Scheme"
                            value={env['ASTERISK_ARI_WEBSOCKET_SCHEME'] || 'ws'}
                            onChange={(e) => {
                                const wsScheme = e.target.value;
                                updateEnv('ASTERISK_ARI_WEBSOCKET_SCHEME', wsScheme);
                                // Sync HTTP scheme: wss requires https, ws uses http
                                updateEnv('ASTERISK_ARI_SCHEME', wsScheme === 'wss' ? 'https' : 'http');
                            }}
                            options={[
                                { value: 'ws', label: 'WS (Unencrypted)' },
                                { value: 'wss', label: 'WSS (Encrypted)' },
                            ]}
                        />
                        {env['ASTERISK_ARI_WEBSOCKET_SCHEME'] === 'wss' && (
                            <div className="space-y-2">
                                <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                                    <input
                                        type="checkbox"
                                        className="w-4 h-4 rounded border border-input"
                                        checked={env['ASTERISK_ARI_SSL_VERIFY'] !== 'false'}
                                        onChange={(e) => updateEnv('ASTERISK_ARI_SSL_VERIFY', e.target.checked ? 'true' : 'false')}
                                    />
                                    Verify SSL Certificate
                                </label>
                                <p className="text-xs text-muted-foreground">
                                    Uncheck for self-signed certificates or IP/hostname mismatches
                                </p>
                            </div>
                        )}
                    </div>
                    
                    {/* Test Connection Button */}
                    <div className="mt-6 pt-4 border-t">
                        <div className="flex items-center gap-4">
                            <button
                                type="button"
                                onClick={testAriConnection}
                                disabled={ariTesting}
                                className="inline-flex items-center px-4 py-2 rounded-md text-sm font-medium bg-secondary text-secondary-foreground hover:bg-secondary/80 disabled:opacity-50"
                            >
                                {ariTesting ? (
                                    <>
                                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                        Testing...
                                    </>
                                ) : (
                                    'Test Connection'
                                )}
                            </button>
                            
                            {ariTestResult && (
                                <div className={`flex items-center gap-2 text-sm ${ariTestResult.success ? 'text-green-600' : 'text-red-600'}`}>
                                    {ariTestResult.success ? (
                                        <>
                                            <CheckCircle className="w-4 h-4" />
                                            <span>{ariTestResult.message}</span>
                                            {ariTestResult.asterisk_version && (
                                                <span className="text-muted-foreground ml-2">
                                                    (Asterisk {ariTestResult.asterisk_version})
                                                </span>
                                            )}
                                        </>
                                    ) : (
                                        <>
                                            <XCircle className="w-4 h-4" />
                                            <span>{ariTestResult.error}</span>
                                        </>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>

            {/* API Keys */}
            <ConfigSection title="API Keys" description="Securely manage API keys for external services.">
                <ConfigCard>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {renderSecretInput('OpenAI API Key', 'OPENAI_API_KEY', 'sk-...')}
                        {renderSecretInput('Groq API Key', 'GROQ_API_KEY', 'gsk_...')}
                        {renderSecretInput('Deepgram API Key', 'DEEPGRAM_API_KEY', 'Token...')}
                        {renderSecretInput('Google API Key', 'GOOGLE_API_KEY', 'AIza...')}
                        {renderSecretInput('ElevenLabs API Key', 'ELEVENLABS_API_KEY', 'xi-...')}
                        {renderSecretInput('Cartesia API Key', 'CARTESIA_API_KEY', 'Token...')}
                        {renderSecretInput('Resend API Key', 'RESEND_API_KEY', 're_...')}
                        {renderSecretInput('JWT Secret', 'JWT_SECRET', 'Secret for auth tokens')}
                    </div>
                </ConfigCard>
            </ConfigSection>

            {/* AI Persona */}
            <ConfigSection title="Default Persona" description="Global identity settings for the AI agent.">
                <ConfigCard>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <FormInput
                            label="AI Name"
                            value={env['AI_NAME'] || ''}
                            onChange={(e) => updateEnv('AI_NAME', e.target.value)}
                            placeholder="Asterisk"
                        />
                        <FormInput
                            label="AI Role"
                            value={env['AI_ROLE'] || ''}
                            onChange={(e) => updateEnv('AI_ROLE', e.target.value)}
                            placeholder="Voice Assistant"
                        />
                    </div>
                </ConfigCard>
            </ConfigSection>

            {/* Logging Section */}
            <ConfigSection title="Logging" description="System logging configuration.">
                <ConfigCard>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <FormSelect
                            label="Log Level"
                            value={(env['LOG_LEVEL'] || 'info').toLowerCase()}
                            onChange={(e) => updateEnv('LOG_LEVEL', e.target.value)}
                            options={[
                                { value: 'debug', label: 'Debug' },
                                { value: 'info', label: 'Info' },
                                { value: 'warning', label: 'Warning' },
                                { value: 'error', label: 'Error' },
                            ]}
                        />
                        <FormSelect
                            label="Log Format"
                            value={env['LOG_FORMAT'] || 'console'}
                            onChange={(e) => updateEnv('LOG_FORMAT', e.target.value)}
                            options={[
                                { value: 'console', label: 'Console' },
                                { value: 'json', label: 'JSON' },
                            ]}
                        />
                        <FormSwitch
                            id="log-color"
                            label="Log Color"
                            description="Enable colored log output."
                            checked={isTrue(env['LOG_COLOR'])}
                            onChange={(e) => updateEnv('LOG_COLOR', e.target.checked ? '1' : '0')}
                        />
                        <FormSelect
                            label="Show Tracebacks"
                            value={env['LOG_SHOW_TRACEBACKS'] || 'auto'}
                            onChange={(e) => updateEnv('LOG_SHOW_TRACEBACKS', e.target.value)}
                            options={[
                                { value: 'auto', label: 'Auto' },
                                { value: 'always', label: 'Always' },
                                { value: 'never', label: 'Never' },
                            ]}
                        />
                        <FormSwitch
                            id="log-to-file"
                            label="Log to File"
                            description="Enable logging to file."
                            checked={isTrue(env['LOG_TO_FILE'])}
                            onChange={(e) => updateEnv('LOG_TO_FILE', e.target.checked ? '1' : '0')}
                        />
                        <div className="col-span-full">
                            <FormInput
                                label="Log File Path"
                                value={env['LOG_FILE_PATH'] || '/mnt/asterisk_media/ai-engine.log'}
                                onChange={(e) => updateEnv('LOG_FILE_PATH', e.target.value)}
                            />
                        </div>
                    </div>
                </ConfigCard>
            </ConfigSection>

            {/* Streaming Logging Section */}
            <ConfigSection title="Streaming Logging" description="Logging settings for streaming operations.">
                <ConfigCard>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <FormSelect
                            label="Streaming Log Level"
                            value={(env['STREAMING_LOG_LEVEL'] || 'info').toLowerCase()}
                            onChange={(e) => updateEnv('STREAMING_LOG_LEVEL', e.target.value)}
                            options={[
                                { value: 'debug', label: 'Debug' },
                                { value: 'info', label: 'Info' },
                                { value: 'warning', label: 'Warning' },
                                { value: 'error', label: 'Error' },
                            ]}
                        />
                    </div>
                </ConfigCard>
            </ConfigSection>

            {/* Diagnostics */}
            <ConfigSection title="Diagnostics" description="Advanced debugging and diagnostic output settings.">
                <ConfigCard>
                    <div className="space-y-6">
                        <FormSwitch
                            id="diag-enable-taps"
                            label="Enable Diagnostic Taps"
                            description="Save audio streams to disk for debugging."
                            checked={isTrue(env['DIAG_ENABLE_TAPS'])}
                            onChange={(e) => updateEnv('DIAG_ENABLE_TAPS', String(e.target.checked))}
                        />

                        {isTrue(env['DIAG_ENABLE_TAPS']) && (
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 pl-4 border-l-2 border-border ml-2">
                                <FormInput
                                    label="Pre-Event Seconds"
                                    type="number"
                                    value={env['DIAG_TAP_PRE_SECS'] || '1'}
                                    onChange={(e) => updateEnv('DIAG_TAP_PRE_SECS', e.target.value)}
                                />
                                <FormInput
                                    label="Post-Event Seconds"
                                    type="number"
                                    value={env['DIAG_TAP_POST_SECS'] || '1'}
                                    onChange={(e) => updateEnv('DIAG_TAP_POST_SECS', e.target.value)}
                                />
                                <FormInput
                                    label="Output Directory"
                                    value={env['DIAG_TAP_OUTPUT_DIR'] || '/tmp/ai-engine-taps'}
                                    onChange={(e) => updateEnv('DIAG_TAP_OUTPUT_DIR', e.target.value)}
                                />
                                <FormSelect
                                    label="Egress Swap Mode"
                                    value={env['DIAG_EGRESS_SWAP_MODE'] || 'none'}
                                    onChange={(e) => updateEnv('DIAG_EGRESS_SWAP_MODE', e.target.value)}
                                    options={[
                                        { value: 'none', label: 'None (Normal)' },
                                        { value: 'swap', label: 'Swap Channels' },
                                        { value: 'left_only', label: 'Left Channel Only' },
                                        { value: 'right_only', label: 'Right Channel Only' }
                                    ]}
                                />
                                <FormSwitch
                                    id="diag-egress-force-mulaw"
                                    label="Force MuLaw"
                                    description="Force MuLaw encoding for egress."
                                    checked={isTrue(env['DIAG_EGRESS_FORCE_MULAW'])}
                                    onChange={(e) => updateEnv('DIAG_EGRESS_FORCE_MULAW', String(e.target.checked))}
                                />
                                <FormInput
                                    label="Attack MS"
                                    type="number"
                                    value={env['DIAG_ATTACK_MS'] || '0'}
                                    onChange={(e) => updateEnv('DIAG_ATTACK_MS', e.target.value)}
                                />
                            </div>
                        )}
                    </div>
                </ConfigCard>
            </ConfigSection>

            {/* Local AI Server Connection Settings */}
            <ConfigSection title="Local AI Server" description="Connection and model settings for local AI services.">
                {/* Connection Settings */}
	                <ConfigCard>
	                    <h3 className="text-sm font-semibold text-muted-foreground mb-4">Connection Settings</h3>
	                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
	                        <FormInput
	                            label="WebSocket URL"
	                            value={env['LOCAL_WS_URL'] || 'ws://127.0.0.1:8765'}
	                            onChange={(e) => updateEnv('LOCAL_WS_URL', e.target.value)}
	                            tooltip="Client URL used by ai-engine to reach local-ai-server."
	                        />
	                        <FormInput
	                            label="Bind Host (local-ai-server)"
	                            value={env['LOCAL_WS_HOST'] || '0.0.0.0'}
	                            onChange={(e) => updateEnv('LOCAL_WS_HOST', e.target.value)}
	                            tooltip="Address local-ai-server binds to (default 0.0.0.0)."
	                        />
	                        <FormInput
	                            label="Bind Port (local-ai-server)"
	                            type="number"
	                            value={env['LOCAL_WS_PORT'] || '8765'}
	                            onChange={(e) => updateEnv('LOCAL_WS_PORT', e.target.value)}
	                            tooltip="Port local-ai-server listens on; update LOCAL_WS_URL to match if changed."
	                        />
	                        <FormInput
	                            label="Auth Token (optional)"
	                            type="password"
	                            value={env['LOCAL_WS_AUTH_TOKEN'] || ''}
	                            onChange={(e) => updateEnv('LOCAL_WS_AUTH_TOKEN', e.target.value)}
	                            tooltip="If set, local-ai-server requires an auth handshake. Must match providers.local*.auth_token."
	                        />
	                        <FormInput
	                            label="Connect Timeout (s)"
	                            type="number"
	                            value={env['LOCAL_WS_CONNECT_TIMEOUT'] || '2.0'}
	                            onChange={(e) => updateEnv('LOCAL_WS_CONNECT_TIMEOUT', e.target.value)}
                        />
                        <FormInput
                            label="Response Timeout (s)"
                            type="number"
                            value={env['LOCAL_WS_RESPONSE_TIMEOUT'] || '5.0'}
                            onChange={(e) => updateEnv('LOCAL_WS_RESPONSE_TIMEOUT', e.target.value)}
                        />
                        <FormInput
                            label="Chunk Size (ms)"
                            type="number"
                            value={env['LOCAL_WS_CHUNK_MS'] || '320'}
                            onChange={(e) => updateEnv('LOCAL_WS_CHUNK_MS', e.target.value)}
                        />
                    </div>
                </ConfigCard>

                {/* STT Backend Settings */}
                <ConfigCard>
                    <h3 className="text-sm font-semibold text-muted-foreground mb-4">STT (Speech-to-Text)</h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <FormSelect
                            label="STT Backend"
                            value={env['LOCAL_STT_BACKEND'] || 'vosk'}
                            onChange={(e) => updateEnv('LOCAL_STT_BACKEND', e.target.value)}
                            options={[
                                { value: 'vosk', label: 'Vosk (Local)' },
                                { value: 'kroko', label: 'Kroko (Cloud/Embedded)' },
                                { value: 'sherpa', label: 'Sherpa-ONNX (Local)' },
                            ]}
                        />

                        {/* Vosk Settings */}
                        {(env['LOCAL_STT_BACKEND'] || 'vosk') === 'vosk' && (
                            <FormInput
                                label="Vosk Model Path"
                                value={env['VOSK_MODEL_PATH'] || '/app/models/stt/vosk-model-en-us-0.22'}
                                onChange={(e) => updateEnv('VOSK_MODEL_PATH', e.target.value)}
                            />
                        )}

                        {/* Kroko Settings */}
                        {env['LOCAL_STT_BACKEND'] === 'kroko' && (
                            <>
                                <FormSwitch
                                    id="kroko-embedded"
                                    label="Embedded Mode"
                                    description="Run Kroko locally (requires model download)."
                                    checked={isTrue(env['KROKO_EMBEDDED'])}
                                    onChange={(e) => updateEnv('KROKO_EMBEDDED', String(e.target.checked))}
                                />
                                {isTrue(env['KROKO_EMBEDDED']) ? (
                                    <>
                                        <FormInput
                                            label="Kroko Model Path"
                                            value={env['KROKO_MODEL_PATH'] || '/app/models/stt/kroko'}
                                            onChange={(e) => updateEnv('KROKO_MODEL_PATH', e.target.value)}
                                        />
                                        <FormInput
                                            label="Kroko Port"
                                            type="number"
                                            value={env['KROKO_PORT'] || '6006'}
                                            onChange={(e) => updateEnv('KROKO_PORT', e.target.value)}
                                        />
                                    </>
                                ) : (
                                    <>
                                        <FormInput
                                            label="Kroko URL"
                                            value={env['KROKO_URL'] || 'wss://app.kroko.ai/api/v1/transcripts/streaming'}
                                            onChange={(e) => updateEnv('KROKO_URL', e.target.value)}
                                        />
                                        {renderSecretInput('Kroko API Key', 'KROKO_API_KEY', 'Your Kroko API key')}
                                    </>
                                )}
                                <FormSelect
                                    label="Language"
                                    value={env['KROKO_LANGUAGE'] || 'en-US'}
                                    onChange={(e) => updateEnv('KROKO_LANGUAGE', e.target.value)}
                                    options={[
                                        { value: 'en-US', label: 'English (US)' },
                                        { value: 'en-GB', label: 'English (UK)' },
                                        { value: 'es-ES', label: 'Spanish' },
                                        { value: 'fr-FR', label: 'French' },
                                        { value: 'de-DE', label: 'German' },
                                    ]}
                                />
                            </>
                        )}

                        {/* Sherpa Settings */}
                        {env['LOCAL_STT_BACKEND'] === 'sherpa' && (
                            <FormInput
                                label="Sherpa Model Path"
                                value={env['SHERPA_MODEL_PATH'] || '/app/models/stt/sherpa-onnx-streaming-zipformer-en-2023-06-26'}
                                onChange={(e) => updateEnv('SHERPA_MODEL_PATH', e.target.value)}
                            />
                        )}
                    </div>
                </ConfigCard>

                {/* TTS Backend Settings */}
                <ConfigCard>
                    <h3 className="text-sm font-semibold text-muted-foreground mb-4">TTS (Text-to-Speech)</h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <FormSelect
                            label="TTS Backend"
                            value={env['LOCAL_TTS_BACKEND'] || 'piper'}
                            onChange={(e) => updateEnv('LOCAL_TTS_BACKEND', e.target.value)}
                            options={[
                                { value: 'piper', label: 'Piper (Local)' },
                                { value: 'kokoro', label: 'Kokoro (Local, Premium)' },
                            ]}
                        />

                        {/* Piper Settings */}
                        {(env['LOCAL_TTS_BACKEND'] || 'piper') === 'piper' && (
                            <FormInput
                                label="Piper Model Path"
                                value={env['PIPER_MODEL_PATH'] || '/app/models/tts/en_US-lessac-medium.onnx'}
                                onChange={(e) => updateEnv('PIPER_MODEL_PATH', e.target.value)}
                            />
                        )}

	                        {/* Kokoro Settings */}
	                        {env['LOCAL_TTS_BACKEND'] === 'kokoro' && (
	                            <>
	                                <FormSelect
	                                    label="Mode"
	                                    value={kokoroMode}
	                                    onChange={(e) => updateEnv('KOKORO_MODE', e.target.value)}
	                                    options={[
	                                        { value: 'local', label: 'Local (On-Premise)' },
	                                        { value: 'api', label: 'Kokoro Web API (Cloud)' },
	                                        ...(showHfKokoroMode ? [{ value: 'hf', label: 'HuggingFace (Auto-download, Advanced)' }] : []),
	                                    ]}
	                                />
	                                <div className="col-span-full">
	                                    <FormSwitch
	                                        id="kokoro-advanced"
	                                        label="Show advanced modes"
	                                        description="Enables HuggingFace auto-download mode. Recommended only if you can tolerate runtime downloads."
	                                        checked={showAdvancedKokoro}
	                                        onChange={(e) => setShowAdvancedKokoro(e.target.checked)}
	                                    />
	                                </div>
	                                <FormSelect
	                                    label="Voice"
	                                    value={env['KOKORO_VOICE'] || 'af_heart'}
	                                    onChange={(e) => updateEnv('KOKORO_VOICE', e.target.value)}
	                                    options={[
                                        { value: 'af_heart', label: 'Heart (Female, American)' },
                                        { value: 'af_bella', label: 'Bella (Female, American)' },
                                        { value: 'af_nicole', label: 'Nicole (Female, American)' },
                                        { value: 'af_sarah', label: 'Sarah (Female, American)' },
                                        { value: 'af_sky', label: 'Sky (Female, American)' },
                                        { value: 'am_adam', label: 'Adam (Male, American)' },
                                        { value: 'am_michael', label: 'Michael (Male, American)' },
                                        { value: 'bf_emma', label: 'Emma (Female, British)' },
                                        { value: 'bf_isabella', label: 'Isabella (Female, British)' },
                                        { value: 'bm_george', label: 'George (Male, British)' },
                                        { value: 'bm_lewis', label: 'Lewis (Male, British)' },
                                    ]}
                                />
	                                {kokoroMode === 'api' ? (
	                                    <>
	                                        <FormInput
	                                            label="Kokoro Web API Base URL"
	                                            value={env['KOKORO_API_BASE_URL'] || 'https://voice-generator.pages.dev/api/v1'}
	                                            onChange={(e) => updateEnv('KOKORO_API_BASE_URL', e.target.value)}
	                                        />
	                                        {renderSecretInput(
	                                            'Kokoro Web API Token (optional)',
	                                            'KOKORO_API_KEY',
	                                            'Bearer token (optional); Dashboard only shows Cloud/API option when a token is set'
	                                        )}
	                                    </>
	                                ) : kokoroMode === 'hf' ? (
	                                    <div className="text-xs text-muted-foreground">
	                                        HuggingFace mode forces Kokoro to load via the HuggingFace cache in the container and may download
	                                        weights/voices on first use. Rebuilding the container can trigger re-downloads unless the cache is
	                                        persisted; for production, prefer Local mode with downloaded files.
	                                    </div>
	                                ) : (
	                                    <FormInput
	                                        label="Model Path"
	                                        value={env['KOKORO_MODEL_PATH'] || '/app/models/tts/kokoro'}
                                        onChange={(e) => updateEnv('KOKORO_MODEL_PATH', e.target.value)}
                                    />
                                )}
                            </>
                        )}
                    </div>
                </ConfigCard>

                {/* LLM Settings */}
                <ConfigCard>
                    <h3 className="text-sm font-semibold text-muted-foreground mb-4">LLM (Large Language Model)</h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div className="col-span-full">
                            <FormInput
                                label="LLM Model Path"
                                value={env['LOCAL_LLM_MODEL'] || '/app/models/llm/phi-3-mini-4k-instruct.Q4_K_M.gguf'}
                                onChange={(e) => updateEnv('LOCAL_LLM_MODEL', e.target.value)}
                            />
                        </div>
                        <FormInput
                            label="Context Size"
                            type="number"
                            value={env['LOCAL_LLM_CONTEXT'] || '4096'}
                            onChange={(e) => updateEnv('LOCAL_LLM_CONTEXT', e.target.value)}
                        />
                        <FormInput
                            label="Batch Size"
                            type="number"
                            value={env['LOCAL_LLM_BATCH'] || '256'}
                            onChange={(e) => updateEnv('LOCAL_LLM_BATCH', e.target.value)}
                        />
                        <FormInput
                            label="Max Tokens"
                            type="number"
                            value={env['LOCAL_LLM_MAX_TOKENS'] || '128'}
                            onChange={(e) => updateEnv('LOCAL_LLM_MAX_TOKENS', e.target.value)}
                        />
                        <FormInput
                            label="Temperature"
                            type="number"
                            step="0.1"
                            value={env['LOCAL_LLM_TEMPERATURE'] || '0.7'}
                            onChange={(e) => updateEnv('LOCAL_LLM_TEMPERATURE', e.target.value)}
                        />
                        <FormInput
                            label="Threads"
                            type="number"
                            value={env['LOCAL_LLM_THREADS'] || '4'}
                            onChange={(e) => updateEnv('LOCAL_LLM_THREADS', e.target.value)}
                        />
                        <FormInput
                            label="Infer Timeout (s)"
                            type="number"
                            value={env['LOCAL_LLM_INFER_TIMEOUT_SEC'] || '30'}
                            onChange={(e) => updateEnv('LOCAL_LLM_INFER_TIMEOUT_SEC', e.target.value)}
                        />
                    </div>
                </ConfigCard>
            </ConfigSection>



            {/* Health Checks */}
	            <ConfigSection title="Health Checks" description="URLs used for system health monitoring.">
	                <ConfigCard>
	                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
	                        <FormInput
	                            label="Local AI Health URL"
	                            value={env['HEALTH_CHECK_LOCAL_AI_URL'] || 'ws://127.0.0.1:8765'}
	                            onChange={(e) => updateEnv('HEALTH_CHECK_LOCAL_AI_URL', e.target.value)}
	                            placeholder="ws://127.0.0.1:8765"
	                        />
                        <FormInput
                            label="AI Engine Health URL"
                            value={env['HEALTH_CHECK_AI_ENGINE_URL'] || 'http://ai_engine:15000/health'}
                            onChange={(e) => updateEnv('HEALTH_CHECK_AI_ENGINE_URL', e.target.value)}
                            placeholder="http://ai_engine:15000/health"
                        />
                    </div>
                </ConfigCard>
            </ConfigSection>

            {otherSettings.length > 0 && (
                <ConfigSection title="Other Variables" description="Additional environment variables found in .env file.">
                    <ConfigCard>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            {otherSettings.map(key => (
                                <FormInput
                                    key={key}
                                    label={key}
                                    value={env[key] || ''}
                                    onChange={(e) => updateEnv(key, e.target.value)}
                                />
                            ))}
                        </div>
                    </ConfigCard>
                </ConfigSection>
            )}
        </div>
    );
};

export default EnvPage;
