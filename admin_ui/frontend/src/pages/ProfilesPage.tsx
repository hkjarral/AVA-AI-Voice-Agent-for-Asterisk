import { useState, useEffect } from 'react';
import axios from 'axios';
import yaml from 'js-yaml';
import { sanitizeConfigForSave } from '../utils/configSanitizers';
import { Settings, Radio, Star, AlertCircle, RefreshCw, Loader2, Plus, Trash2 } from 'lucide-react';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import { Modal } from '../components/ui/Modal';
import { FormInput, FormSelect } from '../components/ui/FormComponents';

const ProfilesPage = () => {
	const [config, setConfig] = useState<any>({});
	const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
	const [editingProfile, setEditingProfile] = useState<string | null>(null);
	const [profileForm, setProfileForm] = useState<any>({});
	const [isNewProfile, setIsNewProfile] = useState(false);
	const [newProfileName, setNewProfileName] = useState('');
	const [pendingApply, setPendingApply] = useState(false);
	const [applying, setApplying] = useState(false);
	const [applyMethod, setApplyMethod] = useState<'hot_reload' | 'restart'>('restart');

    useEffect(() => {
        fetchConfig();
    }, []);

    const fetchConfig = async () => {
        try {
            const res = await axios.get('/api/config/yaml');
            const parsed = yaml.load(res.data.content) as any;
            setConfig(parsed || {});
            setError(null);
        } catch (err) {
            console.error('Failed to load config', err);
            const status = (err as any)?.response?.status;
            if (status === 401) {
                setError('Not authenticated. Please refresh and log in again.');
            } else {
                setError('Failed to load configuration. Check backend logs and try again.');
            }
        } finally {
            setLoading(false);
        }
    };

    const saveConfig = async (newConfig: any) => {
        try {
            const sanitized = sanitizeConfigForSave(newConfig);
            const response = await axios.post('/api/config/yaml', { content: yaml.dump(sanitized) });
            const method = (response.data?.recommended_apply_method || 'restart') as 'hot_reload' | 'restart';
            setApplyMethod(method);
            setPendingApply(true);
            setConfig(sanitized);
        } catch (err) {
            console.error('Failed to save config', err);
            alert('Failed to save configuration');
        }
    };

	    const applyChanges = async (force: boolean = false) => {
	        setApplying(true);
	        try {
	            if (applyMethod === 'hot_reload') {
	                const response = await axios.post('/api/system/containers/ai_engine/reload');
	                const status = response.data?.status ?? (response.status === 200 ? 'success' : undefined);
	                if (status === 'partial' || response.data?.restart_required) {
	                    setApplyMethod('restart');
	                    setPendingApply(true);
	                    alert('Hot reload applied partially; restart AI Engine to fully apply changes.');
	                    return;
	                }
	                if (status === 'success' || response.status === 200) {
	                    setPendingApply(false);
	                    alert('AI Engine hot reloaded! Changes are now active.');
	                    fetchConfig();
	                    return;
	                }
	            }

	            const response = await axios.post(`/api/system/containers/ai_engine/restart?force=${force}`);
	            const status = response.data?.status ?? (response.status === 200 ? 'success' : undefined);
	            if (status === 'warning') {
	                const confirmForce = window.confirm(
	                    `${response.data.message}\n\nDo you want to force restart anyway? This may disconnect active calls.`
	                );
	                if (confirmForce) {
	                    setApplying(false);
	                    return applyChanges(true);
	                }
	                return;
	            }
	            if (status === 'degraded') {
	                setPendingApply(false);
	                alert(`AI Engine restarted but may not be fully healthy: ${response.data.output || 'Health check issue'}\n\nPlease verify manually.`);
	                fetchConfig();
	                return;
	            }
	            if (status === 'success' || response.status === 200) {
	                setPendingApply(false);
	                alert('AI Engine restarted! Changes are now active.');
	                fetchConfig();
	                return;
	            }
	        } catch (err: any) {
	            const action = applyMethod === 'hot_reload' ? 'hot reload' : 'restart';
	            alert(`Failed to ${action} AI Engine: ${err.response?.data?.detail || err.message}`);
	        } finally {
	            setApplying(false);
	        }
	    };

	const handleEditProfile = (name: string) => {
		setEditingProfile(name);
		setProfileForm({ ...config.profiles?.[name] });
		setIsNewProfile(false);
		setNewProfileName('');
	};

	const handleAddProfile = () => {
		setEditingProfile('new_profile');
		setProfileForm({
			chunk_ms: 'auto',
			idle_cutoff_ms: 600,
			internal_rate_hz: 8000,
			provider_pref: {
				input_encoding: 'mulaw',
				input_sample_rate_hz: 8000,
				output_encoding: 'mulaw',
				output_sample_rate_hz: 8000
			},
			transport_out: {
				encoding: 'slin',
				sample_rate_hz: 8000
			}
		});
		setIsNewProfile(true);
		setNewProfileName('');
	};

	const handleSaveProfile = async () => {
		if (!editingProfile) return;

		const profileKey = isNewProfile ? newProfileName.trim() : editingProfile;
		if (!profileKey) {
			alert('Profile name is required');
			return;
		}
		if (profileKey === 'default') {
			alert("Profile name 'default' is reserved (profiles.default selects the default profile).");
			return;
		}
		if (isNewProfile && (config.profiles?.[profileKey] != null)) {
			alert(`Profile '${profileKey}' already exists.`);
			return;
		}

		const newConfig = { ...config };
		if (!newConfig.profiles) newConfig.profiles = {};
		
		newConfig.profiles[profileKey] = profileForm;
		await saveConfig(newConfig);
		setEditingProfile(null);
		setIsNewProfile(false);
		setNewProfileName('');
	};

    const updateProfileField = (field: string, value: any) => {
        setProfileForm({ ...profileForm, [field]: value });
    };

    const updateNestedField = (section: string, field: string, value: any) => {
        setProfileForm({
            ...profileForm,
            [section]: {
                ...profileForm[section],
                [field]: value
            }
        });
    };

    // Get contexts that use this profile
    const getContextsUsingProfile = (profileName: string) => {
        if (!config.contexts) return [];
        return Object.entries(config.contexts)
            .filter(([_, ctx]: [string, any]) => ctx.profile === profileName)
            .map(([name]) => name);
    };

    // Get profile description
    const getProfileDescription = (profileName: string) => {
        const descriptions: Record<string, string> = {
            'telephony_responsive': 'Standard 8kHz μ-law for telephony with adaptive timing',
            'telephony_ulaw_8k': '8kHz μ-law matching RTP codec directly',
            'wideband_pcm_16k': '16kHz wideband for better audio quality',
            'openai_realtime_24k': 'High-fidelity 24kHz for OpenAI Realtime API'
        };
        return descriptions[profileName] || 'Custom audio profile';
    };

    const handleDeleteProfile = async (profileName: string) => {
        const currentProfiles = config.profiles || {};
        const currentProfileKeys = Object.keys(currentProfiles).filter((k) => k !== 'default');
        const currentDefaultProfile = currentProfiles.default || 'telephony_responsive';

        if (currentProfileKeys.length <= 1) {
            alert('Cannot delete the last remaining audio profile.');
            return;
        }

        const contextsUsing = getContextsUsingProfile(profileName);
        const remainingProfiles = currentProfileKeys.filter((p) => p !== profileName);
        const fallbackDefault =
            (remainingProfiles.includes('telephony_responsive') ? 'telephony_responsive' : remainingProfiles[0]) || 'telephony_responsive';

        const isDefault = currentDefaultProfile === profileName || currentProfiles.default === profileName;

        const lines: string[] = [`Delete audio profile "${profileName}"?`];
        if (isDefault) {
            lines.push('', `This profile is currently set as the default (profiles.default).`);
            lines.push(`Default will be changed to "${fallbackDefault}".`);
        }
        if (contextsUsing.length > 0) {
            lines.push('', `This profile is used by ${contextsUsing.length} context(s): ${contextsUsing.join(', ')}`);
            lines.push('Those contexts will fall back to the default profile.');
        }
        lines.push('', 'This cannot be undone.');

        if (!window.confirm(lines.join('\n'))) return;

        const newConfig = { ...config };
        newConfig.profiles = { ...(newConfig.profiles || {}) };

        delete newConfig.profiles[profileName];

        // If any contexts reference this profile, remove the explicit override so they fall back to default.
        if (newConfig.contexts) {
            const nextContexts: Record<string, any> = { ...newConfig.contexts };
            Object.entries(nextContexts).forEach(([ctxName, ctx]) => {
                if (ctx && typeof ctx === 'object' && ctx.profile === profileName) {
                    const nextCtx = { ...ctx };
                    delete nextCtx.profile;
                    nextContexts[ctxName] = nextCtx;
                }
            });
            newConfig.contexts = nextContexts;
        }

        // If this was the default profile, switch profiles.default to a safe remaining value.
        if (newConfig.profiles.default === profileName || isDefault) {
            newConfig.profiles.default = fallbackDefault;
        }

        await saveConfig(newConfig);

        if (editingProfile === profileName) {
            setEditingProfile(null);
            setIsNewProfile(false);
            setNewProfileName('');
        }
    };

    if (loading) return <div className="p-8 text-center text-muted-foreground">Loading profiles...</div>;

    const profiles = config.profiles || {};
    const profileKeys = Object.keys(profiles).filter(k => k !== 'default');
    const defaultProfile = profiles.default || 'telephony_responsive';

	return (
		<div className="space-y-6">
			{pendingApply && (
				<div className="bg-orange-500/15 border border-orange-500/30 text-yellow-700 dark:text-yellow-400 p-4 rounded-md flex items-center justify-between">
					<div className="flex items-center">
						<AlertCircle className="w-5 h-5 mr-2" />
						{applyMethod === 'hot_reload' ? 'Changes saved. Apply to make them active.' : 'Changes saved. Restart required to make them active.'}
					</div>
					<button
						onClick={() => {
							const msg = applyMethod === 'hot_reload'
								? 'Apply profile changes via hot reload now? Active calls should continue, new calls use updated config.'
								: 'Restart AI Engine now? This may disconnect active calls.';
							if (window.confirm(msg)) {
								applyChanges(false);
							}
						}}
						disabled={applying || !pendingApply}
						className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-orange-500 text-white hover:bg-orange-600 font-medium disabled:opacity-50"
					>
						{applying ? (
							<Loader2 className="w-3 h-3 mr-1.5 animate-spin" />
						) : (
							<RefreshCw className="w-3 h-3 mr-1.5" />
						)}
						{applying ? 'Applying...' : applyMethod === 'hot_reload' ? 'Apply Changes' : 'Restart AI Engine'}
					</button>
				</div>
			)}
            {error && (
                <div className="bg-red-500/15 border border-red-500/30 text-red-700 dark:text-red-400 p-4 rounded-md flex items-center justify-between">
                    <div className="flex items-center">
                        <AlertCircle className="w-5 h-5 mr-2" />
                        {error}
                    </div>
                    <button
                        onClick={() => window.location.reload()}
                        className="flex items-center text-xs px-3 py-1.5 rounded transition-colors bg-red-500 text-white hover:bg-red-600 font-medium"
                    >
                        Reload
                    </button>
                </div>
            )}
			<div className="flex justify-between items-center">
				<div>
					<h1 className="text-3xl font-bold tracking-tight">Audio Profiles</h1>
					<p className="text-muted-foreground mt-1">
						Audio encoding and sampling configurations for different scenarios and providers.
					</p>
				</div>
				<button
					onClick={handleAddProfile}
					className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
				>
					<Plus className="w-4 h-4 mr-2" />
					Add Profile
				</button>
			</div>

	            <ConfigSection title="Audio Profiles" description="Click a profile card to edit its settings.">
	                <div className="grid grid-cols-1 gap-4">
	                    {profileKeys.map((profileName) => {
	                        const profile = profiles[profileName];
	                        const contextsUsing = getContextsUsingProfile(profileName);
	                        const isDefault = defaultProfile === profileName;
                            const canDelete = profileKeys.length > 1;
	                        
	                        return (
	                            <div 
	                                key={profileName}
	                                onClick={() => handleEditProfile(profileName)}
                            >
                            <ConfigCard 
                                className="group relative hover:border-primary/50 transition-colors cursor-pointer"
                            >
                                <div className="flex justify-between items-start">
                                    <div className="flex items-center gap-3 mb-4">
                                        <div className="p-2 bg-secondary rounded-md">
                                            <Radio className="w-5 h-5 text-primary" />
                                        </div>
                                        <div>
                                            <div className="flex items-center gap-2">
                                                <h4 className="font-semibold text-lg">{profileName}</h4>
                                                {isDefault && (
                                                    <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-semibold text-primary">
                                                        <Star className="w-3 h-3" />
                                                        Default
                                                    </span>
                                                )}
                                            </div>
                                            <p className="text-sm text-muted-foreground mt-1">
                                                {getProfileDescription(profileName)}
                                            </p>
                                        </div>
                                    </div>
	                                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
	                                        <button
	                                            onClick={(e) => {
	                                                e.stopPropagation();
	                                                handleEditProfile(profileName);
	                                            }}
	                                            className="p-2 hover:bg-accent rounded-md text-muted-foreground hover:text-foreground"
	                                        >
	                                            <Settings className="w-4 h-4" />
	                                        </button>
                                            <button
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleDeleteProfile(profileName);
                                                }}
                                                disabled={!canDelete}
                                                title={!canDelete ? 'Cannot delete the last remaining audio profile' : undefined}
                                                className={[
                                                    "p-2 rounded-md",
                                                    canDelete
                                                        ? "hover:bg-destructive/10 text-destructive"
                                                        : "text-muted-foreground/50 cursor-not-allowed"
                                                ].join(' ')}
                                            >
                                                <Trash2 className="w-4 h-4" />
                                            </button>
	                                    </div>
	                                </div>

                                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                                    <div className="bg-secondary/30 p-2 rounded-md">
                                        <span className="font-medium text-xs uppercase tracking-wider text-muted-foreground block">Internal Rate</span>
                                        <p className="text-foreground font-mono">{profile.internal_rate_hz || 8000} Hz</p>
                                    </div>
                                    <div className="bg-secondary/30 p-2 rounded-md">
                                        <span className="font-medium text-xs uppercase tracking-wider text-muted-foreground block">Chunk</span>
                                        <p className="text-foreground font-mono">{profile.chunk_ms || 'auto'} ms</p>
                                    </div>
                                    <div className="bg-secondary/30 p-2 rounded-md">
                                        <span className="font-medium text-xs uppercase tracking-wider text-muted-foreground block">Provider In</span>
                                        <p className="text-foreground font-mono">{profile.provider_pref?.input_encoding || 'mulaw'}</p>
                                    </div>
                                    <div className="bg-secondary/30 p-2 rounded-md">
                                        <span className="font-medium text-xs uppercase tracking-wider text-muted-foreground block">Transport Out</span>
                                        <p className="text-foreground font-mono">{profile.transport_out?.encoding || 'slin'}</p>
                                    </div>
                                </div>

                                {contextsUsing.length > 0 && (
                                    <div className="mt-3">
                                        <span className="font-medium text-xs uppercase tracking-wider text-muted-foreground block mb-2">Used By Contexts</span>
                                        <div className="flex flex-wrap gap-1.5">
                                            {contextsUsing.map((ctx) => (
                                                <span key={ctx} className="px-2 py-1 rounded-md text-xs bg-accent text-accent-foreground font-medium border border-accent-foreground/10">
                                                    {ctx}
                                                </span>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </ConfigCard>
                            </div>
                        );
                    })}
                </div>
            </ConfigSection>

				<Modal
					isOpen={!!editingProfile}
					onClose={() => {
						setEditingProfile(null);
					setIsNewProfile(false);
					setNewProfileName('');
				}}
				title={isNewProfile ? 'Add Profile' : `Edit Profile: ${editingProfile}`}
					size="lg"
					footer={
						<>
                            {!isNewProfile && (
                                <button
                                    onClick={() => {
                                        if (editingProfile) {
                                            handleDeleteProfile(editingProfile);
                                        }
                                    }}
                                    disabled={!editingProfile || profileKeys.length <= 1}
                                    title={profileKeys.length <= 1 ? 'Cannot delete the last remaining audio profile' : undefined}
                                    className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-destructive/30 text-destructive hover:bg-destructive/10 h-9 px-4 py-2 mr-auto"
                                >
                                    Delete
                                </button>
                            )}
							<button
								onClick={() => {
									setEditingProfile(null);
									setIsNewProfile(false);
								setNewProfileName('');
							}}
							className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground h-9 px-4 py-2"
						>
							Cancel
						</button>
                        <button
                            onClick={handleSaveProfile}
                            className="inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow hover:bg-primary/90 h-9 px-4 py-2"
                        >
                            Save Changes
                        </button>
					</>
				}
			>
				{isNewProfile && (
					<div className="pb-4 border-b border-border mb-4">
						<FormInput
							label="Profile Name"
							value={newProfileName}
							onChange={(e) => setNewProfileName(e.target.value)}
							placeholder="e.g., telephony_pcm_16k"
							tooltip="Key under profiles.<name>. Use lowercase letters, numbers, and underscores."
						/>
					</div>
				)}
				<div className="space-y-6">
					{/* Core Settings */}
					<div>
						<h4 className="font-semibold mb-3">Core Settings</h4>
						<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FormInput
                                label="Chunk Duration (ms)"
                                value={profileForm.chunk_ms || 'auto'}
                                onChange={(e) => updateProfileField('chunk_ms', e.target.value)}
                                tooltip="Audio packet size. Use 'auto' for adaptive."
                            />
                            <FormInput
                                label="Idle Cutoff (ms)"
                                type="number"
                                value={profileForm.idle_cutoff_ms || 0}
                                onChange={(e) => updateProfileField('idle_cutoff_ms', parseInt(e.target.value))}
                                tooltip="Silence before input considered finished."
                            />
                            <FormInput
                                label="Internal Sample Rate (Hz)"
                                type="number"
                                value={profileForm.internal_rate_hz || 8000}
                                onChange={(e) => updateProfileField('internal_rate_hz', parseInt(e.target.value))}
                                tooltip="Processing sample rate (8000, 16000, 24000)."
                            />
                        </div>
                    </div>

                    {/* Provider Preferences */}
                    <div>
                        <h4 className="font-semibold mb-3">Provider Preferences</h4>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FormSelect
                                label="Input Encoding"
                                value={profileForm.provider_pref?.input_encoding || 'mulaw'}
                                onChange={(e) => updateNestedField('provider_pref', 'input_encoding', e.target.value)}
                                options={[
                                    { value: 'mulaw', label: 'μ-law' },
                                    { value: 'pcm16', label: 'PCM16' },
                                    { value: 'linear16', label: 'Linear16' }
                                ]}
                            />
                            <FormInput
                                label="Input Sample Rate (Hz)"
                                type="number"
                                value={profileForm.provider_pref?.input_sample_rate_hz || 8000}
                                onChange={(e) => updateNestedField('provider_pref', 'input_sample_rate_hz', parseInt(e.target.value))}
                            />
                            <FormSelect
                                label="Output Encoding"
                                value={profileForm.provider_pref?.output_encoding || 'mulaw'}
                                onChange={(e) => updateNestedField('provider_pref', 'output_encoding', e.target.value)}
                                options={[
                                    { value: 'mulaw', label: 'μ-law' },
                                    { value: 'pcm16', label: 'PCM16' },
                                    { value: 'linear16', label: 'Linear16' }
                                ]}
                            />
                            <FormInput
                                label="Output Sample Rate (Hz)"
                                type="number"
                                value={profileForm.provider_pref?.output_sample_rate_hz || 8000}
                                onChange={(e) => updateNestedField('provider_pref', 'output_sample_rate_hz', parseInt(e.target.value))}
                            />
                        </div>
                    </div>

                    {/* Transport Output */}
                    <div>
                        <h4 className="font-semibold mb-3">Transport Output</h4>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <FormSelect
                                label="Encoding"
                                value={profileForm.transport_out?.encoding || 'slin'}
                                onChange={(e) => updateNestedField('transport_out', 'encoding', e.target.value)}
                                options={[
                                    { value: 'slin', label: 'SLIN (8kHz)' },
                                    { value: 'slin16', label: 'SLIN16 (16kHz)' },
                                    { value: 'ulaw', label: 'μ-law' }
                                ]}
                            />
                            <FormInput
                                label="Sample Rate (Hz)"
                                type="number"
                                value={profileForm.transport_out?.sample_rate_hz || 8000}
                                onChange={(e) => updateNestedField('transport_out', 'sample_rate_hz', parseInt(e.target.value))}
                            />
                        </div>
                    </div>
                </div>
            </Modal>
        </div>
    );
};

export default ProfilesPage;
