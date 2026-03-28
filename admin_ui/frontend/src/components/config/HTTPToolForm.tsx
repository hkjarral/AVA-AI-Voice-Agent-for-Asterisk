import { useState } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { useConfirmDialog } from '../../hooks/useConfirmDialog';
import { Plus, Trash2, Settings, Webhook, Search, Play, Loader2, CheckCircle2, XCircle, ChevronDown, ChevronRight, Lock } from 'lucide-react';
import { useAuth } from '../../auth/AuthContext';
import { FormInput, FormSwitch, FormSelect, FormLabel } from '../ui/FormComponents';
import { Modal } from '../ui/Modal';

interface HTTPToolFormProps {
    config: any;
    onChange: (newConfig: any) => void;
    phase: 'pre_call' | 'in_call' | 'post_call';
    contexts?: Record<string, any>;  // P1: For in-use check on delete
}

interface ToolParameter {
    name: string;
    type: string;
    description: string;
    required: boolean;
}

interface HTTPToolConfig {
    kind: string;
    phase: string;
    enabled: boolean;
    is_global: boolean;
    timeout_ms: number;
    url: string;
    method: string;
    headers: Record<string, string>;
    query_params?: Record<string, string>;
    body_template?: string;
    payload_template?: string;
    output_variables?: Record<string, string>;
    hold_audio_file?: string;
    hold_audio_threshold_ms?: number;
    generate_summary?: boolean;
    summary_max_words?: number;
    // In-call specific fields
    description?: string;
    parameters?: ToolParameter[];
    return_raw_json?: boolean;
    error_message?: string;
}

const DEFAULT_WEBHOOK_PAYLOAD = `{
  "schema_version": 1,
  "event_type": "call_completed",
  "call_id": "{call_id}",
  "caller_number": "{caller_number}",
  "caller_name": "{caller_name}",
  "call_duration": {call_duration},
  "call_outcome": "{call_outcome}",
  "transcript": {transcript_json},
  "context": "{context_name}",
  "provider": "{provider}",
  "timestamp": "{call_end_time}"
}`;

interface TestResult {
    success: boolean;
    status_code?: number;
    response_time_ms: number;
    headers: Record<string, string>;
    body?: any;
    body_raw?: string;
    error?: string;
    resolved_url: string;
    resolved_body?: string;
    suggested_mappings: Array<{ path: string; value: string; type: string }>;
}

const DEFAULT_TEST_VALUES: Record<string, string> = {
    caller_number: '+15551234567',
    called_number: '+18005551234',
    caller_name: 'Test Caller',
    caller_id: '+15551234567',
    call_id: '1234567890.123',
    context_name: 'test-context',
    campaign_id: 'test-campaign',
    lead_id: 'test-lead-123',
};

const HTTPToolForm = ({ config, onChange, phase, contexts }: HTTPToolFormProps) => {
    const { confirm } = useConfirmDialog();
    const { token } = useAuth();
    const [editingTool, setEditingTool] = useState<string | null>(null);
    const [toolForm, setToolForm] = useState<any>({});
    const [headerKey, setHeaderKey] = useState('');
    const [headerValue, setHeaderValue] = useState('');
    const [outputVarKey, setOutputVarKey] = useState('');
    const [outputVarPath, setOutputVarPath] = useState('');
    const [queryParamKey, setQueryParamKey] = useState('');
    const [queryParamValue, setQueryParamValue] = useState('');
    
    // Test functionality state
    const [testValues, setTestValues] = useState<Record<string, string>>(DEFAULT_TEST_VALUES);
    const [testing, setTesting] = useState(false);
    const [testResult, setTestResult] = useState<TestResult | null>(null);
    const [showTestPanel, setShowTestPanel] = useState(false);
    const [showTestValues, setShowTestValues] = useState(false);
    const [showAllMappings, setShowAllMappings] = useState(false);
    const variableTokenClass = "font-mono text-emerald-700";

    const matchesPhase = (value: any) => {
        if (!value || typeof value !== 'object' || !value.kind) return false;
        if (phase === 'in_call') {
            return value.phase === 'in_call' || (!value.phase && value.kind === 'in_call_http_lookup');
        }
        return value.phase === phase;
    };

    const getHTTPTools = () => {
        const tools: Record<string, HTTPToolConfig> = {};
        Object.entries(config || {}).forEach(([key, value]: [string, any]) => {
            if (matchesPhase(value)) {
                tools[key] = value as HTTPToolConfig;
            }
        });
        return tools;
    };

    const httpTools = getHTTPTools();

    const handleAddTool = () => {
        const kindMap: Record<string, string> = {
            'pre_call': 'generic_http_lookup',
            'in_call': 'in_call_http_lookup',
            'post_call': 'generic_webhook',
        };
        const kind = kindMap[phase];
        setEditingTool('new_tool');
        setToolForm({
            key: '',
            kind,
            phase,
            enabled: true,
            is_global: phase === 'post_call',
            timeout_ms: phase === 'pre_call' ? 2000 : 5000,
            url: '',
            method: phase === 'pre_call' ? 'GET' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            query_params: {},
            output_variables: {},
            payload_template: phase === 'post_call' ? DEFAULT_WEBHOOK_PAYLOAD : undefined,
            // In-call specific fields
            description: phase === 'in_call' ? '' : undefined,
            parameters: phase === 'in_call' ? [] : undefined,
            return_raw_json: phase === 'in_call' ? false : undefined,
            error_message: phase === 'in_call' ? "I'm sorry, I couldn't retrieve that information right now." : undefined,
        });
    };

    const handleEditTool = (key: string, data: HTTPToolConfig) => {
        setEditingTool(key);
        setToolForm({ key, ...data });
    };

    const handleSaveTool = () => {
        if (!toolForm.key) {
            toast.error('Please enter a Tool Name');
            return;
        }
        if (!toolForm.url) {
            toast.error('Please enter a URL');
            return;
        }

        const { key, ...data } = toolForm;
        const updated = { ...config };

        if (editingTool !== 'new_tool' && editingTool !== key) {
            delete updated[editingTool!];
        }

        updated[key] = data;
        onChange(updated);
        setEditingTool(null);
    };

    const handleDeleteTool = async (key: string) => {
        const toolData = config[key] as HTTPToolConfig;
        let confirmed = false;
        
        // P2 Fix: Check if tool is global - affects ALL contexts
        if (toolData?.is_global) {
            const contextCountText = contexts ? `${Object.keys(contexts).length} context(s)` : 'all contexts';
            confirmed = await confirm({
                title: '⚠️ Global Tool Warning',
                description: `HTTP tool "${key}" is marked as GLOBAL and automatically applies to ${contextCountText}.\n\nDeleting this tool will affect every context.`,
                confirmText: 'Delete',
                variant: 'destructive'
            });
        } else if (contexts) {
            // P1: Check if tool is used by any context (for all phases)
            // Map phase to the context config key
            const phaseToContextKey: Record<string, string> = {
                'pre_call': 'pre_call_tools',
                'in_call': 'in_call_http_tools',
                'post_call': 'post_call_tools'
            };
            const contextKey = phaseToContextKey[phase];
            
            const usingContexts = Object.entries(contexts)
                .filter(([_, ctx]) => {
                    const tools = (ctx as any)[contextKey] || [];
                    return tools.includes(key);
                })
                .map(([ctxName]) => ctxName);
            
            if (usingContexts.length > 0) {
                confirmed = await confirm({
                    title: 'Delete HTTP Tool?',
                    description: `HTTP tool "${key}" is used by ${usingContexts.length} context(s): ${usingContexts.join(', ')}.\n\nDeleting will remove it from those contexts.`,
                    confirmText: 'Delete',
                    variant: 'destructive'
                });
            } else {
                confirmed = await confirm({
                    title: 'Delete HTTP Tool?',
                    description: `Delete "${key}"?`,
                    confirmText: 'Delete',
                    variant: 'destructive'
                });
            }
        } else {
            confirmed = await confirm({
                title: 'Delete HTTP Tool?',
                description: `Delete "${key}"?`,
                confirmText: 'Delete',
                variant: 'destructive'
            });
        }
        
        if (!confirmed) return;

        const updated = { ...config };
        delete updated[key];
        onChange(updated);
    };

    const addHeader = () => {
        if (!headerKey) return;
        setToolForm({
            ...toolForm,
            headers: { ...toolForm.headers, [headerKey]: headerValue }
        });
        setHeaderKey('');
        setHeaderValue('');
    };

    const removeHeader = (key: string) => {
        const headers = { ...toolForm.headers };
        delete headers[key];
        setToolForm({ ...toolForm, headers });
    };

    const addOutputVariable = () => {
        if (!outputVarKey) return;
        setToolForm({
            ...toolForm,
            output_variables: { ...toolForm.output_variables, [outputVarKey]: outputVarPath }
        });
        setOutputVarKey('');
        setOutputVarPath('');
    };

    const removeOutputVariable = (key: string) => {
        const vars = { ...toolForm.output_variables };
        delete vars[key];
        setToolForm({ ...toolForm, output_variables: vars });
    };

    const addQueryParam = () => {
        if (!queryParamKey) return;
        setToolForm({
            ...toolForm,
            query_params: { ...toolForm.query_params, [queryParamKey]: queryParamValue }
        });
        setQueryParamKey('');
        setQueryParamValue('');
    };

    const removeQueryParam = (key: string) => {
        const params = { ...toolForm.query_params };
        delete params[key];
        setToolForm({ ...toolForm, query_params: params });
    };

    const handleTestTool = async () => {
        if (!toolForm.url) {
            toast.error('Please enter a URL first');
            return;
        }
        
        setTesting(true);
        setTestResult(null);
        setShowTestPanel(true);
        
        try {
            const response = await axios.post('/api/tools/test-http', {
                url: toolForm.url,
                method: toolForm.method || 'GET',
                headers: toolForm.headers || {},
                query_params: toolForm.query_params || {},
                body_template: toolForm.body_template || null,
                timeout_ms: toolForm.timeout_ms || 5000,
                test_values: testValues,
            }, {
                headers: { Authorization: `Bearer ${token}` },
            });
            
            setTestResult(response.data);
        } catch (err: any) {
            setTestResult({
                success: false,
                response_time_ms: 0,
                headers: {},
                resolved_url: toolForm.url,
                error: err.response?.data?.detail || err.message || 'Test failed',
                suggested_mappings: [],
            });
        } finally {
            setTesting(false);
        }
    };

    const handleAddMapping = (path: string) => {
        const varName = path.replace(/\[\d+\]/g, '').replace(/\./g, '_').toLowerCase();
        // Use functional update to avoid stale closure issues
        setToolForm((prev: any) => ({
            ...prev,
            output_variables: { ...(prev.output_variables || {}), [varName]: path }
        }));
    };

    const getPhaseConfig = () => {
        switch (phase) {
            case 'pre_call':
                return {
                    icon: <Search className="w-4 h-4" />,
                    title: 'Pre-Call HTTP Lookups',
                    desc: 'Fetch data from external APIs (CRM, database) before the AI speaks. Output variables are injected into the system prompt.',
                    addLabel: 'Add Lookup',
                    emptyLabel: 'pre-call lookups',
                };
            case 'in_call':
                return {
                    icon: <Search className="w-4 h-4" />,
                    title: 'In-Call HTTP Tools',
                    desc: 'HTTP lookup tools the AI can invoke during conversation to fetch data (e.g., check availability, lookup order status).',
                    addLabel: 'Add Tool',
                    emptyLabel: 'in-call HTTP tools',
                };
            case 'post_call':
            default:
                return {
                    icon: <Webhook className="w-4 h-4" />,
                    title: 'Post-Call Webhooks',
                    desc: 'Send call data to external systems (n8n, Make, CRM) after the call ends. Fire-and-forget.',
                    addLabel: 'Add Webhook',
                    emptyLabel: 'post-call webhooks',
                };
        }
    };

    const phaseConfig = getPhaseConfig();

    return (
        <div className="space-y-4">
            <div className="flex justify-between items-center">
                <div>
                    <h4 className="text-md font-medium flex items-center gap-2">
                        {phaseConfig.icon} {phaseConfig.title}
                    </h4>
                    <p className="text-xs text-muted-foreground mt-1">{phaseConfig.desc}</p>
                </div>
                <button
                    onClick={handleAddTool}
                    className="text-xs flex items-center bg-primary text-primary-foreground px-3 py-1.5 rounded hover:bg-primary/90 transition-colors"
                >
                    <Plus className="w-3 h-3 mr-1" /> {phaseConfig.addLabel}
                </button>
            </div>

            {Object.keys(httpTools).length === 0 ? (
                <div className="text-sm text-muted-foreground p-4 border border-dashed border-border rounded-lg text-center">
                    No {phaseConfig.emptyLabel} configured.
                </div>
            ) : (
                <div className="space-y-2">
                    {Object.entries(httpTools).map(([key, tool]) => (
                        <div key={key} className="flex items-center justify-between p-3 bg-accent/30 rounded border border-border/50">
                            <div className="flex items-center gap-3">
                                <div className={`w-2 h-2 rounded-full ${tool.enabled ? 'bg-green-500' : 'bg-gray-400'}`} />
                                <div>
                                    <div className="font-medium text-sm flex items-center gap-2">
                                        {key}
                                        {tool.is_global && (
                                            <span className="text-xs bg-blue-500/20 text-blue-600 px-1.5 py-0.5 rounded flex items-center gap-1">
                                                <Lock className="w-3 h-3" /> Global
                                            </span>
                                        )}
                                    </div>
                                    <div className="text-xs text-muted-foreground">
                                        {tool.method} • {tool.url ? (tool.url.length > 50 ? tool.url.substring(0, 50) + '...' : tool.url) : 'No URL'}
                                    </div>
                                </div>
                            </div>
                            <div className="flex items-center gap-1">
                                <button 
                                    onClick={() => handleEditTool(key, tool)} 
                                    className="p-1.5 hover:bg-background rounded text-muted-foreground hover:text-foreground"
                                >
                                    <Settings className="w-4 h-4" />
                                </button>
                                <button 
                                    onClick={() => handleDeleteTool(key)} 
                                    className="p-1.5 hover:bg-destructive/10 rounded text-destructive"
                                >
                                    <Trash2 className="w-4 h-4" />
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            <Modal
                isOpen={!!editingTool}
                onClose={() => setEditingTool(null)}
                title={editingTool === 'new_tool' 
                    ? `Add ${phase === 'pre_call' ? 'HTTP Lookup' : phase === 'in_call' ? 'In-Call HTTP Tool' : 'Webhook'}` 
                    : `Edit ${toolForm.key}`}
                footer={
                    <>
                        <button onClick={() => setEditingTool(null)} className="px-4 py-2 border rounded hover:bg-accent">Cancel</button>
                        <button 
                            onClick={handleTestTool} 
                            disabled={testing || !toolForm.url}
                            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
                        >
                            {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                            {testing ? 'Testing...' : 'Test'}
                        </button>
                        <button onClick={handleSaveTool} className="px-4 py-2 bg-primary text-primary-foreground rounded hover:bg-primary/90">Save</button>
                    </>
                }
            >
                <div className="space-y-4 max-h-[60vh] overflow-y-auto pr-2">
                    <FormInput
                        label="Tool Name"
                        value={toolForm.key || ''}
                        onChange={(e) => setToolForm({ ...toolForm, key: e.target.value.replace(/\s/g, '_').toLowerCase() })}
                        placeholder="e.g., crm_lookup or call_webhook"
                        disabled={editingTool !== 'new_tool'}
                    />

                    <div className="grid grid-cols-2 gap-4">
                        <FormSwitch
                            label="Enabled"
                            checked={toolForm.enabled ?? true}
                            onChange={(e) => setToolForm({ ...toolForm, enabled: e.target.checked })}
                        />
                        <FormSwitch
                            label="Global (all contexts)"
                            checked={toolForm.is_global ?? false}
                            onChange={(e) => setToolForm({ ...toolForm, is_global: e.target.checked })}
                        />
                    </div>

                    <FormInput
                        label="URL"
                        value={toolForm.url || ''}
                        onChange={(e) => setToolForm({ ...toolForm, url: e.target.value })}
                        placeholder="https://api.example.com/webhook"
                        tooltip="Use {caller_number}, {call_id}, etc. for variable substitution. Use ${ENV_VAR} for secrets."
                    />

                    <div className="grid grid-cols-2 gap-4">
                        <FormSelect
                            label="Method"
                            options={[
                                { value: 'GET', label: 'GET' },
                                { value: 'POST', label: 'POST' },
                                { value: 'PUT', label: 'PUT' },
                                { value: 'PATCH', label: 'PATCH' },
                            ]}
                            value={toolForm.method || 'POST'}
                            onChange={(e) => setToolForm({ ...toolForm, method: e.target.value })}
                        />
                        <FormInput
                            label="Timeout (ms)"
                            type="number"
                            value={toolForm.timeout_ms || 5000}
                            onChange={(e) => setToolForm({ ...toolForm, timeout_ms: parseInt(e.target.value) })}
                        />
                    </div>

                    {/* Headers */}
                    <div className="space-y-2">
                        <FormLabel>Headers</FormLabel>
                        <div className="space-y-1">
                            {Object.entries(toolForm.headers || {}).map(([k, v]) => (
                                <div key={k} className="flex items-center gap-2 text-xs bg-accent/50 px-2 py-1 rounded">
                                    <span className="font-mono">{k}: {String(v).substring(0, 30)}{String(v).length > 30 ? '...' : ''}</span>
                                    <button onClick={() => removeHeader(k)} className="ml-auto text-destructive hover:text-destructive/80">
                                        <Trash2 className="w-3 h-3" />
                                    </button>
                                </div>
                            ))}
                        </div>
                        <div className="flex gap-2">
                            <input
                                className="flex-1 px-2 py-1 text-sm border rounded"
                                placeholder="Header name"
                                value={headerKey}
                                onChange={(e) => setHeaderKey(e.target.value)}
                            />
                            <input
                                className="flex-1 px-2 py-1 text-sm border rounded"
                                placeholder="Value (use ${VAR} for secrets)"
                                value={headerValue}
                                onChange={(e) => setHeaderValue(e.target.value)}
                            />
                            <button onClick={addHeader} className="px-2 py-1 bg-secondary rounded text-xs hover:bg-secondary/80">
                                <Plus className="w-3 h-3" />
                            </button>
                        </div>
                    </div>

                    {/* Pre-call specific: Query Params, Body Template, Output Variables */}
                    {phase === 'pre_call' && (
                        <>
                            {/* Query Parameters */}
                            <div className="space-y-2">
                                <FormLabel tooltip="URL query parameters. Use {caller_number}, {call_id}, etc. for variable substitution.">
                                    Query Parameters
                                </FormLabel>
                                <div className="space-y-1">
                                    {Object.entries(toolForm.query_params || {}).map(([k, v]) => (
                                        <div key={k} className="flex items-center gap-2 text-xs bg-accent/50 px-2 py-1 rounded">
                                            <span className="font-mono">{k}={String(v)}</span>
                                            <button onClick={() => removeQueryParam(k)} className="ml-auto text-destructive hover:text-destructive/80">
                                                <Trash2 className="w-3 h-3" />
                                            </button>
                                        </div>
                                    ))}
                                </div>
                                <div className="flex gap-2">
                                    <input
                                        className="flex-1 px-2 py-1 text-sm border rounded"
                                        placeholder="Parameter name (e.g., phone)"
                                        value={queryParamKey}
                                        onChange={(e) => setQueryParamKey(e.target.value)}
                                    />
                                    <input
                                        className="flex-1 px-2 py-1 text-sm border rounded"
                                        placeholder="Value (e.g., {caller_number})"
                                        value={queryParamValue}
                                        onChange={(e) => setQueryParamValue(e.target.value)}
                                    />
                                    <button onClick={addQueryParam} className="px-2 py-1 bg-secondary rounded text-xs hover:bg-secondary/80">
                                        <Plus className="w-3 h-3" />
                                    </button>
                                </div>
                            </div>

                            {/* Body Template (for POST requests) */}
                            {(toolForm.method === 'POST' || toolForm.method === 'PUT' || toolForm.method === 'PATCH') && (
                                <div className="space-y-2">
                                    <FormLabel tooltip="JSON body template for POST/PUT/PATCH requests. Use {caller_number}, {call_id}, etc. for variable substitution.">
                                        Body Template
                                    </FormLabel>
                                    <textarea
                                        className="w-full p-3 rounded-md border border-input bg-transparent text-sm font-mono min-h-[120px] focus:outline-none focus:ring-1 focus:ring-ring"
                                        value={toolForm.body_template || ''}
                                        onChange={(e) => setToolForm({ ...toolForm, body_template: e.target.value })}
                                        placeholder='{"phone": "{caller_number}", "context": "{context_name}"}'
                                    />
                                </div>
                            )}

                            {/* Output Variables */}
                            <div className="space-y-2">
                                <FormLabel tooltip="Map JSON response paths to variables for prompt injection. Use dot notation like 'contact.name' or 'contacts[0].email'">
                                    Output Variables
                                </FormLabel>
                                <div className="space-y-1">
                                    {Object.entries(toolForm.output_variables || {}).map(([k, v]) => (
                                        <div key={k} className="flex items-center gap-2 text-xs bg-accent/50 px-2 py-1 rounded">
                                            <span className="font-mono">
                                                <span className={variableTokenClass}>{k}</span>{" "}
                                                <span className="text-muted-foreground">←</span>{" "}
                                                <span>{String(v)}</span>
                                            </span>
                                            <button onClick={() => removeOutputVariable(k)} className="ml-auto text-destructive hover:text-destructive/80">
                                                <Trash2 className="w-3 h-3" />
                                            </button>
                                        </div>
                                    ))}
                                </div>
                                    <div className="flex gap-2">
                                        <input
                                            className={`flex-1 px-2 py-1 text-sm border rounded ${variableTokenClass}`}
                                            placeholder="Variable name (e.g., customer_name)"
                                            value={outputVarKey}
                                            onChange={(e) => setOutputVarKey(e.target.value)}
                                        />
                                        <input
                                            className="flex-1 px-2 py-1 text-sm border rounded"
                                            placeholder="JSON path (e.g., contact.name)"
                                        value={outputVarPath}
                                        onChange={(e) => setOutputVarPath(e.target.value)}
                                    />
                                    <button onClick={addOutputVariable} className="px-2 py-1 bg-secondary rounded text-xs hover:bg-secondary/80">
                                        <Plus className="w-3 h-3" />
                                    </button>
                                </div>
                            </div>

                            <div className="grid grid-cols-2 gap-4">
                                <FormInput
                                    label="Hold Audio File (optional)"
                                    value={toolForm.hold_audio_file || ''}
                                    onChange={(e) => setToolForm({ ...toolForm, hold_audio_file: e.target.value })}
                                    placeholder="custom/please-wait"
                                    tooltip="Asterisk sound file to play while waiting for lookup"
                                />
                                <FormInput
                                    label="Hold Audio Threshold (ms)"
                                    type="number"
                                    value={toolForm.hold_audio_threshold_ms || 500}
                                    onChange={(e) => setToolForm({ ...toolForm, hold_audio_threshold_ms: parseInt(e.target.value) })}
                                    tooltip="Play hold audio if lookup takes longer than this threshold"
                                />
                            </div>

                            {/* Test Values Configuration */}
                            <div className="border border-border rounded-lg p-3 bg-card/30">
                                <button
                                    type="button"
                                    onClick={() => setShowTestValues(!showTestValues)}
                                    className="flex items-center gap-2 text-sm font-medium w-full"
                                >
                                    {showTestValues ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                                    Test Values (for variable substitution)
                                </button>
                                {showTestValues && (
                                    <div className="mt-3 grid grid-cols-2 gap-3">
                                        {Object.entries(testValues).map(([key, value]) => (
                                            <div key={key} className="flex flex-col gap-1">
                                                <label className="text-xs text-muted-foreground font-mono">{`{${key}}`}</label>
                                                <input
                                                    className="w-full px-2 py-1 text-xs border rounded bg-background"
                                                    value={value}
                                                    onChange={(e) => setTestValues({ ...testValues, [key]: e.target.value })}
                                                />
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Test Results Panel */}
                            {showTestPanel && (
                                <div className="border border-border rounded-lg overflow-hidden">
                                    <div className="bg-accent/50 px-3 py-2 flex items-center justify-between">
                                        <span className="text-sm font-medium">Test Results</span>
                                        {testResult && (
                                            <div className="flex items-center gap-2">
                                                {testResult.success ? (
                                                    <span className="flex items-center gap-1 text-xs text-green-600">
                                                        <CheckCircle2 className="w-3 h-3" />
                                                        {testResult.status_code} OK
                                                    </span>
                                                ) : (
                                                    <span className="flex items-center gap-1 text-xs text-red-600">
                                                        <XCircle className="w-3 h-3" />
                                                        {testResult.error || 'Failed'}
                                                    </span>
                                                )}
                                                <span className="text-xs text-muted-foreground">
                                                    {testResult.response_time_ms.toFixed(0)}ms
                                                </span>
                                            </div>
                                        )}
                                    </div>
                                    
                                    {testing && (
                                        <div className="p-4 text-center text-muted-foreground">
                                            <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" />
                                            Testing endpoint...
                                        </div>
                                    )}
                                    
                                    {testResult && !testing && (
                                        <div className="p-3 space-y-3 max-h-[300px] overflow-y-auto">
                                            {/* Resolved URL */}
                                            <div>
                                                <div className="text-xs text-muted-foreground mb-1">Resolved URL:</div>
                                                <code className="text-xs bg-accent/50 px-2 py-1 rounded block break-all">
                                                    {testResult.resolved_url}
                                                </code>
                                            </div>
                                            
                                            {/* Response Body Preview */}
                                            {testResult.body && (
                                                <div>
                                                    <div className="text-xs text-muted-foreground mb-1">Response:</div>
                                                    <pre className="text-xs bg-accent/30 p-2 rounded overflow-x-auto max-h-[150px]">
                                                        {typeof testResult.body === 'object' 
                                                            ? JSON.stringify(testResult.body, null, 2)
                                                            : testResult.body}
                                                    </pre>
                                                </div>
                                            )}
                                            
                                            {/* Click-to-Map Suggestions */}
                                            {testResult.suggested_mappings && testResult.suggested_mappings.length > 0 && (
                                                <div>
                                                    <div className="text-xs text-muted-foreground mb-1">
                                                        Click to add output variable mapping:
                                                    </div>
                                                    <div className="space-y-1">
                                                        {(showAllMappings ? testResult.suggested_mappings : testResult.suggested_mappings.slice(0, 20)).map((mapping, idx) => (
                                                            <button
                                                                key={idx}
                                                                type="button"
                                                                onClick={() => handleAddMapping(mapping.path)}
                                                                className="flex items-center justify-between w-full text-left px-2 py-1.5 text-xs bg-accent/30 hover:bg-accent rounded group"
                                                            >
                                                                <div className="flex flex-col gap-1 min-w-0 md:grid md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-start md:gap-2">
                                                                    <code className="min-w-0 font-mono text-blue-600 break-all">{mapping.path}</code>
                                                                    <span className="hidden md:inline text-muted-foreground">→</span>
                                                                    <span className="min-w-0 whitespace-pre-wrap break-words">
                                                                        <span className="md:hidden text-muted-foreground mr-2">→</span>
                                                                        {String(mapping.value ?? 'null')}
                                                                    </span>
                                                                </div>
                                                                <Plus className="w-3 h-3 opacity-0 group-hover:opacity-100 text-green-600" />
                                                            </button>
                                                        ))}
                                                    </div>
                                                    {testResult.suggested_mappings.length > 20 && (
                                                        <button
                                                            type="button"
                                                            onClick={() => setShowAllMappings(!showAllMappings)}
                                                            className="text-xs text-blue-600 hover:text-blue-800 mt-2 underline"
                                                        >
                                                            {showAllMappings 
                                                                ? 'Show less' 
                                                                : `+${testResult.suggested_mappings.length - 20} more fields...`}
                                                        </button>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}
                        </>
                    )}

                    {/* In-call specific: Description, Parameters, Query Params, Body, Output Variables */}
                    {phase === 'in_call' && (
                        <>
                            {/* Tool Description for AI */}
                            <div className="space-y-2">
                                <FormLabel tooltip="Description shown to the AI so it knows when to use this tool">
                                    Tool Description
                                </FormLabel>
                                <textarea
                                    className="w-full p-3 rounded-md border border-input bg-transparent text-sm min-h-[80px] focus:outline-none focus:ring-1 focus:ring-ring"
                                    value={toolForm.description || ''}
                                    onChange={(e) => setToolForm({ ...toolForm, description: e.target.value })}
                                    placeholder="Describe what this tool does and when the AI should use it. E.g., 'Check if an appointment slot is available for the given date and time.'"
                                />
                            </div>

                            {/* AI-Provided Parameters */}
                            <div className="space-y-2">
                                <FormLabel tooltip="Parameters the AI will provide when calling this tool. These become variables in your URL, headers, and body template.">
                                    AI Parameters
                                </FormLabel>
                                <div className="space-y-2">
                                    {(toolForm.parameters || []).map((param: ToolParameter, idx: number) => (
                                        <div key={idx} className="flex items-start gap-2 p-2 bg-accent/30 rounded border">
                                            <div className="flex-1 grid grid-cols-4 gap-2">
                                                <input
                                                    className={`px-2 py-1 text-xs border rounded ${variableTokenClass}`}
                                                    placeholder="Name"
                                                    value={param.name}
                                                    onChange={(e) => {
                                                        const params = [...(toolForm.parameters || [])];
                                                        params[idx] = { ...params[idx], name: e.target.value };
                                                        setToolForm({ ...toolForm, parameters: params });
                                                    }}
                                                />
                                                <select
                                                    className="px-2 py-1 text-xs border rounded bg-background"
                                                    value={param.type}
                                                    onChange={(e) => {
                                                        const params = [...(toolForm.parameters || [])];
                                                        params[idx] = { ...params[idx], type: e.target.value };
                                                        setToolForm({ ...toolForm, parameters: params });
                                                    }}
                                                >
                                                    <option value="string">string</option>
                                                    <option value="number">number</option>
                                                    <option value="boolean">boolean</option>
                                                </select>
                                                <input
                                                    className="px-2 py-1 text-xs border rounded col-span-2"
                                                    placeholder="Description for AI"
                                                    value={param.description}
                                                    onChange={(e) => {
                                                        const params = [...(toolForm.parameters || [])];
                                                        params[idx] = { ...params[idx], description: e.target.value };
                                                        setToolForm({ ...toolForm, parameters: params });
                                                    }}
                                                />
                                            </div>
                                            <label className="flex items-center gap-1 text-xs">
                                                <input
                                                    type="checkbox"
                                                    checked={param.required}
                                                    onChange={(e) => {
                                                        const params = [...(toolForm.parameters || [])];
                                                        params[idx] = { ...params[idx], required: e.target.checked };
                                                        setToolForm({ ...toolForm, parameters: params });
                                                    }}
                                                />
                                                Required
                                            </label>
                                            <button
                                                onClick={() => {
                                                    const params = (toolForm.parameters || []).filter((_: any, i: number) => i !== idx);
                                                    setToolForm({ ...toolForm, parameters: params });
                                                }}
                                                className="p-1 text-destructive hover:text-destructive/80"
                                            >
                                                <Trash2 className="w-3 h-3" />
                                            </button>
                                        </div>
                                    ))}
                                </div>
                                <button
                                    type="button"
                                    onClick={() => {
                                        const params = [...(toolForm.parameters || []), { name: '', type: 'string', description: '', required: false }];
                                        setToolForm({ ...toolForm, parameters: params });
                                    }}
                                    className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
                                >
                                    <Plus className="w-3 h-3" /> Add Parameter
                                </button>
                            </div>

                            {/* Query Parameters */}
                            <div className="space-y-2">
                                <FormLabel tooltip="URL query parameters. Use {param_name} for AI-provided params, {caller_number}, {call_id}, etc. for context vars.">
                                    Query Parameters
                                </FormLabel>
                                <div className="space-y-1">
                                    {Object.entries(toolForm.query_params || {}).map(([k, v]) => (
                                        <div key={k} className="flex items-center gap-2 text-xs bg-accent/50 px-2 py-1 rounded">
                                            <span className="font-mono">{k}={String(v)}</span>
                                            <button onClick={() => removeQueryParam(k)} className="ml-auto text-destructive hover:text-destructive/80">
                                                <Trash2 className="w-3 h-3" />
                                            </button>
                                        </div>
                                    ))}
                                </div>
                                <div className="flex gap-2">
                                    <input
                                        className="flex-1 px-2 py-1 text-sm border rounded"
                                        placeholder="Parameter name"
                                        value={queryParamKey}
                                        onChange={(e) => setQueryParamKey(e.target.value)}
                                    />
                                    <input
                                        className="flex-1 px-2 py-1 text-sm border rounded"
                                        placeholder="Value (e.g., {date})"
                                        value={queryParamValue}
                                        onChange={(e) => setQueryParamValue(e.target.value)}
                                    />
                                    <button onClick={addQueryParam} className="px-2 py-1 bg-secondary rounded text-xs hover:bg-secondary/80">
                                        <Plus className="w-3 h-3" />
                                    </button>
                                </div>
                            </div>

                            {/* Body Template */}
                            {(toolForm.method === 'POST' || toolForm.method === 'PUT' || toolForm.method === 'PATCH') && (
                                <div className="space-y-2">
                                    <FormLabel tooltip="JSON body template. Use {param_name} for AI params, {caller_number}, {call_id}, etc. for context vars.">
                                        Body Template
                                    </FormLabel>
                                    <textarea
                                        className="w-full p-3 rounded-md border border-input bg-transparent text-sm font-mono min-h-[120px] focus:outline-none focus:ring-1 focus:ring-ring"
                                        value={toolForm.body_template || ''}
                                        onChange={(e) => setToolForm({ ...toolForm, body_template: e.target.value })}
                                        placeholder='{"caller": "{caller_number}", "date": "{date}", "time": "{time}"}'
                                    />
                                </div>
                            )}

                            {/* Response Handling */}
                            <div className="border border-border rounded-lg p-3 bg-card/30 space-y-3">
                                <FormSwitch
                                    label="Return Raw JSON to AI"
                                    description="If enabled, the full JSON response is passed to AI. Otherwise, only selected output variables are returned."
                                    checked={toolForm.return_raw_json ?? false}
                                    onChange={(e) => setToolForm({ ...toolForm, return_raw_json: e.target.checked })}
                                />
                            </div>

                            {/* Output Variables (when not returning raw JSON) */}
                            {!toolForm.return_raw_json && (
                                <div className="space-y-2">
                                    <FormLabel tooltip="Map JSON response paths to variables returned to the AI. Use dot notation like 'available' or 'slot.time'">
                                        Output Variables
                                    </FormLabel>
                                    <div className="space-y-1">
                                        {Object.entries(toolForm.output_variables || {}).map(([k, v]) => (
                                            <div key={k} className="flex items-center gap-2 text-xs bg-accent/50 px-2 py-1 rounded">
                                                <span className="font-mono">
                                                    <span className={variableTokenClass}>{k}</span>{" "}
                                                    <span className="text-muted-foreground">←</span>{" "}
                                                    <span>{String(v)}</span>
                                                </span>
                                                <button onClick={() => removeOutputVariable(k)} className="ml-auto text-destructive hover:text-destructive/80">
                                                    <Trash2 className="w-3 h-3" />
                                                </button>
                                            </div>
                                        ))}
                                    </div>
                                    <div className="flex gap-2">
                                        <input
                                            className={`flex-1 px-2 py-1 text-sm border rounded ${variableTokenClass}`}
                                            placeholder="Variable name (e.g., available)"
                                            value={outputVarKey}
                                            onChange={(e) => setOutputVarKey(e.target.value)}
                                        />
                                        <input
                                            className="flex-1 px-2 py-1 text-sm border rounded"
                                            placeholder="JSON path (e.g., data.available)"
                                            value={outputVarPath}
                                            onChange={(e) => setOutputVarPath(e.target.value)}
                                        />
                                        <button onClick={addOutputVariable} className="px-2 py-1 bg-secondary rounded text-xs hover:bg-secondary/80">
                                            <Plus className="w-3 h-3" />
                                        </button>
                                    </div>
                                </div>
                            )}

                            {/* Error Message */}
                            <FormInput
                                label="Error Message"
                                value={toolForm.error_message || ''}
                                onChange={(e) => setToolForm({ ...toolForm, error_message: e.target.value })}
                                placeholder="I'm sorry, I couldn't retrieve that information right now."
                                tooltip="Message the AI will receive if the HTTP request fails"
                            />

                            {/* Hold Audio Settings */}
                            <div className="grid grid-cols-2 gap-4">
                                <FormInput
                                    label="Hold Audio File (optional)"
                                    value={toolForm.hold_audio_file || ''}
                                    onChange={(e) => setToolForm({ ...toolForm, hold_audio_file: e.target.value })}
                                    placeholder="custom/please-wait"
                                    tooltip="Asterisk sound file to play while waiting for response"
                                />
                                <FormInput
                                    label="Hold Audio Threshold (ms)"
                                    type="number"
                                    value={toolForm.hold_audio_threshold_ms || 500}
                                    onChange={(e) => setToolForm({ ...toolForm, hold_audio_threshold_ms: parseInt(e.target.value) })}
                                    tooltip="Play hold audio if request takes longer than this threshold"
                                />
                            </div>

                            {/* Test Values Configuration */}
                            <div className="border border-border rounded-lg p-3 bg-card/30">
                                <button
                                    type="button"
                                    onClick={() => setShowTestValues(!showTestValues)}
                                    className="flex items-center gap-2 text-sm font-medium w-full"
                                >
                                    {showTestValues ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                                    Test Values (for variable substitution)
                                </button>
                                {showTestValues && (
                                    <div className="mt-3 grid grid-cols-2 gap-3">
                                        {Object.entries(testValues).map(([key, value]) => (
                                            <div key={key} className="flex flex-col gap-1">
                                                <label className={`text-xs ${variableTokenClass}`}>{`{${key}}`}</label>
                                                <input
                                                    className="w-full px-2 py-1 text-xs border rounded bg-background"
                                                    value={value}
                                                    onChange={(e) => setTestValues({ ...testValues, [key]: e.target.value })}
                                                />
                                            </div>
                                        ))}
                                        {/* Add AI parameter test values */}
                                        {(toolForm.parameters || []).map((param: ToolParameter) => (
                                            <div key={`param_${param.name}`} className="flex flex-col gap-1">
                                                <label className="text-xs">
                                                    <span className={variableTokenClass}>{`{${param.name}}`}</span>{" "}
                                                    <span className="text-blue-500">(AI param)</span>
                                                </label>
                                                <input
                                                    className="w-full px-2 py-1 text-xs border rounded bg-background"
                                                    value={testValues[param.name] || ''}
                                                    onChange={(e) => setTestValues({ ...testValues, [param.name]: e.target.value })}
                                                    placeholder={`Test value for ${param.name}`}
                                                />
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>

                            {/* Test Results Panel */}
                            {showTestPanel && (
                                <div className="border border-border rounded-lg overflow-hidden">
                                    <div className="bg-accent/50 px-3 py-2 flex items-center justify-between">
                                        <span className="text-sm font-medium">Test Results</span>
                                        {testResult && (
                                            <div className="flex items-center gap-2">
                                                {testResult.success ? (
                                                    <span className="flex items-center gap-1 text-xs text-green-600">
                                                        <CheckCircle2 className="w-3 h-3" />
                                                        {testResult.status_code} OK
                                                    </span>
                                                ) : (
                                                    <span className="flex items-center gap-1 text-xs text-red-600">
                                                        <XCircle className="w-3 h-3" />
                                                        {testResult.error || 'Failed'}
                                                    </span>
                                                )}
                                                <span className="text-xs text-muted-foreground">
                                                    {testResult.response_time_ms.toFixed(0)}ms
                                                </span>
                                            </div>
                                        )}
                                    </div>
                                    
                                    {testing && (
                                        <div className="p-4 text-center text-muted-foreground">
                                            <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" />
                                            Testing endpoint...
                                        </div>
                                    )}
                                    
                                    {testResult && !testing && (
                                        <div className="p-3 space-y-3 max-h-[300px] overflow-y-auto">
                                            {/* Resolved URL */}
                                            <div>
                                                <div className="text-xs text-muted-foreground mb-1">Resolved URL:</div>
                                                <code className="text-xs bg-accent/50 px-2 py-1 rounded block break-all">
                                                    {testResult.resolved_url}
                                                </code>
                                            </div>
                                            
                                            {/* Response Body Preview */}
                                            {testResult.body && (
                                                <div>
                                                    <div className="text-xs text-muted-foreground mb-1">Response:</div>
                                                    <pre className="text-xs bg-accent/30 p-2 rounded overflow-x-auto max-h-[150px]">
                                                        {typeof testResult.body === 'object' 
                                                            ? JSON.stringify(testResult.body, null, 2)
                                                            : testResult.body}
                                                    </pre>
                                                </div>
                                            )}
                                            
                                            {/* Click-to-Map Suggestions */}
                                            {testResult.suggested_mappings && testResult.suggested_mappings.length > 0 && (
                                                <div>
                                                    <div className="text-xs text-muted-foreground mb-1">
                                                        Click to add output variable mapping:
                                                    </div>
                                                    <div className="space-y-1">
                                                        {(showAllMappings ? testResult.suggested_mappings : testResult.suggested_mappings.slice(0, 20)).map((mapping, idx) => (
                                                            <button
                                                                key={idx}
                                                                type="button"
                                                                onClick={() => handleAddMapping(mapping.path)}
                                                                className="flex items-center justify-between w-full text-left px-2 py-1.5 text-xs bg-accent/30 hover:bg-accent rounded group"
                                                            >
                                                                <div className="flex flex-col gap-1 min-w-0 md:grid md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] md:items-start md:gap-2">
                                                                    <code className="min-w-0 font-mono text-blue-600 break-all">{mapping.path}</code>
                                                                    <span className="hidden md:inline text-muted-foreground">→</span>
                                                                    <span className="min-w-0 whitespace-pre-wrap break-words">
                                                                        <span className="md:hidden text-muted-foreground mr-2">→</span>
                                                                        {String(mapping.value ?? 'null')}
                                                                    </span>
                                                                </div>
                                                                <Plus className="w-3 h-3 opacity-0 group-hover:opacity-100 text-green-600" />
                                                            </button>
                                                        ))}
                                                    </div>
                                                    {testResult.suggested_mappings.length > 20 && (
                                                        <button
                                                            type="button"
                                                            onClick={() => setShowAllMappings(!showAllMappings)}
                                                            className="text-xs text-blue-600 hover:text-blue-800 mt-2 underline"
                                                        >
                                                            {showAllMappings 
                                                                ? 'Show less' 
                                                                : `+${testResult.suggested_mappings.length - 20} more fields...`}
                                                        </button>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            )}
                        </>
                    )}

                    {/* Post-call specific: Payload Template + Summary */}
                    {phase === 'post_call' && (
                        <>
                            <div className="border border-border rounded-lg p-3 bg-card/30">
                                <FormSwitch
                                    label="Generate AI Summary"
                                    description="Use OpenAI to generate a concise summary instead of sending full transcript. Requires OPENAI_API_KEY."
                                    checked={toolForm.generate_summary ?? false}
                                    onChange={(e) => setToolForm({ ...toolForm, generate_summary: e.target.checked })}
                                />
                                {toolForm.generate_summary && (
                                    <div className="mt-3">
                                        <FormInput
                                            label="Max Summary Words"
                                            type="number"
                                            value={toolForm.summary_max_words || 100}
                                            onChange={(e) => setToolForm({ ...toolForm, summary_max_words: parseInt(e.target.value) })}
                                            tooltip="Maximum words for the generated summary"
                                        />
                                    </div>
                                )}
                            </div>
                            <div className="space-y-2">
                                <FormLabel tooltip="JSON payload with variable substitution. Available: {call_id}, {caller_number}, {call_duration}, {transcript_json}, {summary}, etc.">
                                    Payload Template
                                </FormLabel>
                                <textarea
                                    className="w-full p-3 rounded-md border border-input bg-transparent text-sm font-mono min-h-[200px] focus:outline-none focus:ring-1 focus:ring-ring"
                                    value={toolForm.payload_template || ''}
                                    onChange={(e) => setToolForm({ ...toolForm, payload_template: e.target.value })}
                                    placeholder={DEFAULT_WEBHOOK_PAYLOAD}
                                />
                            </div>
                        </>
                    )}
                </div>
            </Modal>
        </div>
    );
};

export default HTTPToolForm;
