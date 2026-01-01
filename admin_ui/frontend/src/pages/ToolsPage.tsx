import React, { useState, useEffect } from 'react';
import axios from 'axios';
import yaml from 'js-yaml';
import { Save, Wrench, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import ToolForm from '../components/config/ToolForm';
import { useAuth } from '../auth/AuthContext';
import { sanitizeConfigForSave } from '../utils/configSanitizers';

const ToolsPage = () => {
    const { token } = useAuth();
    const [config, setConfig] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [pendingRestart, setPendingRestart] = useState(false);
    const [restartingEngine, setRestartingEngine] = useState(false);

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            const parsed = yaml.load(res.data.content) as any;
            setConfig(parsed || {});
        } catch (err) {
            console.error('Failed to load config', err);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const sanitized = sanitizeConfigForSave(config);
            await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) }, {
                headers: { Authorization: `Bearer ${token}` },
                timeout: 30000  // 30 second timeout
            });
            setPendingRestart(true);
            alert('Tools configuration saved successfully');
        } catch (err: any) {
            console.error('Failed to save config', err);
            const detail = err.response?.data?.detail || err.message || 'Unknown error';
            alert(`Failed to save configuration: ${detail}`);
        } finally {
            setSaving(false);
        }
    };

    const handleRestartAIEngine = async (force: boolean = false) => {
        setRestartingEngine(true);
        try {
            const response = await axios.post(`/api/system/containers/ai_engine/restart?force=${force}`, {}, {
                headers: { Authorization: `Bearer ${token}` }
            });

            if (response.data.status === 'warning') {
                const confirmForce = window.confirm(
                    `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`
                );
                if (confirmForce) {
                    setRestartingEngine(false);
                    return handleRestartAIEngine(true);
                }
                return;
            }

            if (response.data.status === 'degraded') {
                alert(`AI Engine restarted but may not be fully healthy: ${response.data.output || 'Health check issue'}\n\nPlease verify manually.`);
                return;
            }

            setPendingRestart(false);
            alert('AI Engine restarted! Changes are now active.');
        } catch (error: any) {
            alert(`Failed to restart AI Engine: ${error.response?.data?.detail || error.message}`);
        } finally {
            setRestartingEngine(false);
        }
    };

    const updateToolsConfig = (newToolsConfig: any) => {
        // Extract root-level settings that should not be nested under tools
        const { farewell_hangup_delay_sec, ...toolsOnly } = newToolsConfig;
        
        // Update both tools config and root-level farewell_hangup_delay_sec
        const updatedConfig = { ...config, tools: toolsOnly };
        if (farewell_hangup_delay_sec !== undefined) {
            updatedConfig.farewell_hangup_delay_sec = farewell_hangup_delay_sec;
        }
        setConfig(updatedConfig);
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    return (
        <div className="space-y-6">
            <div className={`${pendingRestart ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    Tool configuration changes require an AI Engine restart to take effect.
                </div>
                <button
                    onClick={() => handleRestartAIEngine(false)}
                    disabled={restartingEngine || !pendingRestart}
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
                    {restartingEngine ? 'Restarting...' : 'Restart AI Engine'}
                </button>
            </div>
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Tools & Capabilities</h1>
                    <p className="text-muted-foreground mt-1">
                        Configure the tools and capabilities available to the AI agent.
                    </p>
                </div>
                <button
                    onClick={handleSave}
                    disabled={saving}
                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                >
                    <Save className="w-4 h-4 mr-2" />
                    {saving ? 'Saving...' : 'Save Changes'}
                </button>
            </div>

            <ConfigSection title="Global Tool Settings" description="Configure tools available across all contexts.">
                <ConfigCard>
                    <ToolForm
                        config={{ ...(config.tools || {}), farewell_hangup_delay_sec: config.farewell_hangup_delay_sec }}
                        onChange={updateToolsConfig}
                    />
                </ConfigCard>
            </ConfigSection>
        </div>
    );
};

export default ToolsPage;
