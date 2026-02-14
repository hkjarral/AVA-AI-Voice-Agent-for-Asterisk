import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import yaml from 'js-yaml';
import { Save, AlertCircle, RefreshCw, Loader2 } from 'lucide-react';
import { YamlErrorBanner, YamlErrorInfo } from '../../components/ui/YamlErrorBanner';
import { ConfigSection } from '../../components/ui/ConfigSection';
import { ConfigCard } from '../../components/ui/ConfigCard';
import { FormInput, FormSelect, FormSwitch } from '../../components/ui/FormComponents';
import { sanitizeConfigForSave } from '../../utils/configSanitizers';

const TransportPage = () => {
    const { confirm } = useConfirmDialog();
    const [config, setConfig] = useState<any>({});
    const [loading, setLoading] = useState(true);
    const [yamlError, setYamlError] = useState<YamlErrorInfo | null>(null);
    const [saving, setSaving] = useState(false);
    const [pendingRestart, setPendingRestart] = useState(false);
    const [restartingEngine, setRestartingEngine] = useState(false);
    const [applyMethod, setApplyMethod] = useState<string>('restart');
    const [showExternalMediaExpert, setShowExternalMediaExpert] = useState(false);

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            if (res.data.yaml_error) {
                setYamlError(res.data.yaml_error);
                setConfig({});
            } else {
                const parsed = yaml.load(res.data.content) as any;
                setConfig(parsed || {});
                setYamlError(null);
            }
        } catch (err) {
            console.error('Failed to load config', err);
            setYamlError(null);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const sanitized = sanitizeConfigForSave(config);
            const response = await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) });
            const method = response.data?.recommended_apply_method || 'restart';
            setApplyMethod(method);
            setPendingRestart(true);
            
            // Show appropriate message based on recommended apply method
            if (method === 'hot_reload') {
                toast.success('Configuration saved. Changes can be applied via hot-reload.');
            } else {
                toast.success('Transport configuration saved. Restart AI Engine to apply changes.');
            }
        } catch (err) {
            console.error('Failed to save config', err);
            toast.error('Failed to save configuration');
        } finally {
            setSaving(false);
        }
    };

    const handleApplyAIEngine = async (force: boolean = false) => {
        setRestartingEngine(true);
        try {
            if (applyMethod === 'hot_reload') {
                const response = await axios.post('/api/system/containers/ai_engine/reload');

                if (response.data?.restart_required) {
                    setApplyMethod('restart');
                    setPendingRestart(true);
                    toast.warning('Hot reload applied partially', { description: response.data.message || 'Restart AI Engine to fully apply changes' });
                    return;
                }

                if (response.data?.status === 'success') {
                    setPendingRestart(false);
                    toast.success('AI Engine hot reloaded! Changes are now active.');
                    return;
                }

                toast.info(`Hot reload response: ${response.data?.message || 'unknown status'}`);
                return;
            }

            const response = await axios.post(`/api/system/containers/ai_engine/restart?force=${force}`);

            if (response.data.status === 'warning') {
                const confirmForce = await confirm({
                    title: 'Force Restart?',
                    description: `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`,
                    confirmText: 'Force Restart',
                    variant: 'destructive'
                });
                if (confirmForce) {
                    setRestartingEngine(false);
                    return handleApplyAIEngine(true);
                }
                return;
            }

            if (response.data.status === 'degraded') {
                toast.warning('AI Engine restarted but may not be fully healthy', { description: response.data.output || 'Please verify manually' });
                return;
            }

            if (response.data.status === 'success') {
                setPendingRestart(false);
                toast.success('AI Engine restarted! Changes are now active.');
                return;
            }
        } catch (error: any) {
            const actionLabel = applyMethod === 'hot_reload' ? 'hot reload' : 'restart';
            toast.error(`Failed to ${actionLabel} AI Engine`, { description: error.response?.data?.detail || error.message });
        } finally {
            setRestartingEngine(false);
        }
    };

    const updateConfig = (field: string, value: any) => {
        setConfig({ ...config, [field]: value });
    };

    const updateSectionConfig = (section: string, field: string, value: any) => {
        setConfig({
            ...config,
            [section]: {
                ...config[section],
                [field]: value
            }
        });
    };

    useEffect(() => {
        if (config?.external_media?.lock_remote_endpoint !== undefined) {
            setShowExternalMediaExpert(true);
        }
    }, [config?.external_media?.lock_remote_endpoint]);

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading configuration...</div>;

    if (yamlError) return (
        <div className="space-y-6">
            <YamlErrorBanner error={yamlError} />
        </div>
    );

    const transportType = config.audio_transport || 'audiosocket';
    const audiosocketConfig = config.audiosocket || {};
    const externalMediaConfig = config.external_media || {};

    // Determine banner message based on apply method
    const bannerMessage = applyMethod === 'hot_reload'
        ? 'Changes saved. Apply Changes to hot reload AI Engine without a restart.'
        : 'Changes to transport configurations require an AI Engine restart to take effect.';
    
    const buttonLabel = applyMethod === 'hot_reload' ? 'Apply Changes' : 'Restart AI Engine';

    return (
        <div className="space-y-6">
            <div className={`${pendingRestart ? 'bg-orange-500/15 border-orange-500/30' : 'bg-yellow-500/10 border-yellow-500/20'} border text-yellow-600 dark:text-yellow-500 p-4 rounded-md flex items-center justify-between`}>
                <div className="flex items-center">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    {bannerMessage}
                </div>
                <button
                    onClick={() => handleApplyAIEngine(false)}
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
                    {restartingEngine ? 'Applying...' : buttonLabel}
                </button>
            </div>

            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Audio Transport</h1>
                    <p className="text-muted-foreground mt-1">
                        Configure how audio is transported between Asterisk and the AI Agent.
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

            <ConfigSection title="Asterisk Configuration" description="Core Asterisk integration settings.">
                <ConfigCard>
                    <FormInput
                        label="Stasis Application Name"
                        value={config.asterisk?.app_name || 'asterisk-ai-voice-agent'}
                        onChange={(e) => updateSectionConfig('asterisk', 'app_name', e.target.value)}
                        tooltip="Name of the Stasis application in your dialplan. Must match the app name in your Asterisk configuration."
                    />
                </ConfigCard>
            </ConfigSection>

            <ConfigSection title="Transport Type" description="Select the audio transport method.">
                <ConfigCard>
                    <FormSelect
                        label="Transport Method"
                        value={transportType}
                        onChange={(e) => updateConfig('audio_transport', e.target.value)}
                        options={[
                            { value: 'audiosocket', label: 'AudioSocket (Default)' },
                            { value: 'externalmedia', label: 'External Media (RTP)' }
                        ]}
                        description="Choose 'AudioSocket' for standard deployments or 'External Media' for RTP-based integration."
                    />
                </ConfigCard>
            </ConfigSection>

            {transportType === 'audiosocket' && (
                <ConfigSection title="AudioSocket Settings" description="Configuration for the AudioSocket server.">
                    <ConfigCard>
                        <div className="space-y-6">
                            <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider">Network Configuration</h4>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <FormInput
                                    label="Bind Host"
                                    value={audiosocketConfig.host || '127.0.0.1'}
                                    onChange={(e) => updateSectionConfig('audiosocket', 'host', e.target.value)}
                                    tooltip="IP address the AudioSocket server listens on. Use 0.0.0.0 to listen on all interfaces."
                                />
                                <FormInput
                                    label="Advertise Host"
                                    value={audiosocketConfig.advertise_host || audiosocketConfig.host || '127.0.0.1'}
                                    onChange={(e) => updateSectionConfig('audiosocket', 'advertise_host', e.target.value)}
                                    tooltip="IP address Asterisk connects to. For NAT/VPN deployments, set this to your routable IP (VPN IP, public IP, or LAN IP). Leave as Bind Host for same-host deployments."
                                />
                                <FormInput
                                    label="Port"
                                    type="number"
                                    value={audiosocketConfig.port || 8090}
                                    onChange={(e) => updateSectionConfig('audiosocket', 'port', parseInt(e.target.value))}
                                    tooltip="TCP port for AudioSocket connections (default: 8090)."
                                />
                                <FormInput
                                    label="Format"
                                    value={audiosocketConfig.format || 'slin'}
                                    onChange={(e) => updateSectionConfig('audiosocket', 'format', e.target.value)}
                                    tooltip="Audio format (e.g., slin, ulaw)"
                                />
                            </div>
                        </div>
                    </ConfigCard>
                </ConfigSection>
            )}

            {transportType === 'externalmedia' && (
                <ConfigSection title="External Media (RTP) Settings" description="Configuration for RTP-based audio transport.">
                    <ConfigCard>
                        <div className="space-y-6">
                            <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider">Network Configuration</h4>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <FormInput
                                    label="RTP Bind Host"
                                    value={externalMediaConfig.rtp_host || '127.0.0.1'}
                                    onChange={(e) => updateSectionConfig('external_media', 'rtp_host', e.target.value)}
                                    tooltip="IP address the RTP server listens on. Use 0.0.0.0 to listen on all interfaces."
                                />
                                <FormInput
                                    label="Advertise Host"
                                    value={externalMediaConfig.advertise_host || externalMediaConfig.rtp_host || '127.0.0.1'}
                                    onChange={(e) => updateSectionConfig('external_media', 'advertise_host', e.target.value)}
                                    tooltip="IP address Asterisk sends RTP to. For NAT/VPN deployments, set this to your routable IP (VPN IP, public IP, or LAN IP). Leave as Bind Host for same-host deployments."
                                />
                                <FormInput
                                    label="RTP Port"
                                    type="number"
                                    value={externalMediaConfig.rtp_port || 18080}
                                    onChange={(e) => updateSectionConfig('external_media', 'rtp_port', parseInt(e.target.value))}
                                    tooltip="Base UDP port for RTP streams (default: 18080)."
                                />
                                <FormInput
                                    label="Port Range"
                                    value={externalMediaConfig.port_range || '18080:18099'}
                                    onChange={(e) => updateSectionConfig('external_media', 'port_range', e.target.value)}
                                    placeholder="18080:18099"
                                    tooltip="Range of UDP ports for concurrent calls (format: start:end, e.g., 18080:18099)."
                                />
                                <FormInput
                                    label="Allowed Remote Hosts"
                                    value={Array.isArray(externalMediaConfig.allowed_remote_hosts) 
                                        ? externalMediaConfig.allowed_remote_hosts.join(', ') 
                                        : (externalMediaConfig.allowed_remote_hosts || '')}
                                    onChange={(e) => {
                                        const value = e.target.value.trim();
                                        const hosts = value ? value.split(',').map(h => h.trim()).filter(h => h) : [];
                                        updateSectionConfig('external_media', 'allowed_remote_hosts', hosts.length > 0 ? hosts : null);
                                    }}
                                    placeholder="e.g., 192.168.1.100, 10.0.0.5"
                                    tooltip="IP addresses allowed to send RTP packets. Required when ASTERISK_HOST is a hostname. Comma-separated for multiple IPs."
                                />
                            </div>

                            <div className="border-t border-border my-4"></div>

                            <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider">Asterisk-side Configuration</h4>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <FormSelect
                                    label="Codec"
                                    value={externalMediaConfig.codec || 'ulaw'}
                                    onChange={(e) => updateSectionConfig('external_media', 'codec', e.target.value)}
                                    options={[
                                        { value: 'ulaw', label: 'μ-law (8kHz)' },
                                        { value: 'alaw', label: 'A-law (8kHz)' },
                                        { value: 'slin', label: 'SLIN (8kHz)' },
                                        { value: 'slin16', label: 'SLIN16 (16kHz)' }
                                    ]}
                                    description="Codec Asterisk sends/receives."
                                />
                                <FormSelect
                                    label="Direction"
                                    value={externalMediaConfig.direction || 'both'}
                                    onChange={(e) => updateSectionConfig('external_media', 'direction', e.target.value)}
                                    options={[
                                        { value: 'both', label: 'Both' },
                                        { value: 'sendonly', label: 'Send Only' },
                                        { value: 'recvonly', label: 'Receive Only' }
                                    ]}
                                />
                            </div>

                            <div className="border-t border-border my-4"></div>

                            <h4 className="text-sm font-medium text-muted-foreground uppercase tracking-wider">Engine-side Configuration</h4>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <FormSelect
                                    label="Internal Format"
                                    value={externalMediaConfig.format || 'slin16'}
                                    onChange={(e) => updateSectionConfig('external_media', 'format', e.target.value)}
                                    options={[
                                        { value: 'slin', label: 'SLIN (8kHz)' },
                                        { value: 'slin16', label: 'SLIN16 (16kHz)' },
                                        { value: 'ulaw', label: 'μ-law (8kHz)' }
                                    ]}
                                    description="Engine internal format. Pipelines typically expect 16kHz PCM16 (slin16)."
                                />
                                <FormInput
                                    label="Sample Rate (Hz)"
                                    type="number"
                                    value={externalMediaConfig.sample_rate || 16000}
                                    onChange={(e) => updateSectionConfig('external_media', 'sample_rate', parseInt(e.target.value))}
                                    tooltip="Auto-inferred from format if not set."
                                />
                            </div>

                            <div className="border border-amber-300/40 rounded-lg p-4 bg-amber-500/5">
                                <FormSwitch
                                    label="External Media Expert Settings"
                                    description="Expose RTP source endpoint hardening controls."
                                    checked={showExternalMediaExpert}
                                    onChange={(e) => setShowExternalMediaExpert(e.target.checked)}
                                    className="mb-0 border-0 p-0 bg-transparent"
                                />
                                <p className={`text-xs mt-2 ${showExternalMediaExpert ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'}`}>
                                    {showExternalMediaExpert
                                        ? 'Warning: incorrect settings can drop RTP packets or break media connectivity.'
                                        : 'Expert values are visible and read-only until enabled.'}
                                </p>
                                <div className="mt-3">
                                    <FormSwitch
                                        label="Lock Remote Endpoint"
                                        description="Drop RTP packets if source host/port changes mid-call."
                                        checked={externalMediaConfig.lock_remote_endpoint ?? true}
                                        onChange={(e) => updateSectionConfig('external_media', 'lock_remote_endpoint', e.target.checked)}
                                        disabled={!showExternalMediaExpert}
                                    />
                                    <p className="text-xs text-muted-foreground mt-2">
                                        Security hardening: keep enabled unless your network path legitimately rewrites RTP source mid-call.
                                    </p>
                                </div>
                            </div>
                        </div>
                    </ConfigCard>
                </ConfigSection>
            )}
        </div>
    );
};

export default TransportPage;
