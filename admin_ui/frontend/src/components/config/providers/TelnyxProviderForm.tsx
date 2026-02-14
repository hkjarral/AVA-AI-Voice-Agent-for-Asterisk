import React from 'react';

interface TelnyxProviderFormProps {
    config: any;
    onChange: (newConfig: any) => void;
}

const TelnyxProviderForm: React.FC<TelnyxProviderFormProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: any) => {
        onChange({ ...config, [field]: value });
    };

    const maxTokensValue =
        config.max_tokens === undefined || config.max_tokens === null ? '' : String(config.max_tokens);

    return (
        <div className="space-y-6">
            <div className="space-y-4">
                <h4 className="font-semibold text-sm border-b pb-2">Authentication</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">Telnyx API Key (env or literal)</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.api_key || '${TELNYX_API_KEY}'}
                            onChange={(e) => handleChange('api_key', e.target.value)}
                            placeholder="${TELNYX_API_KEY}"
                        />
                        <p className="text-xs text-muted-foreground">
                            Recommended: leave as <code>${'{TELNYX_API_KEY}'}</code>. The AI Engine will inject this at runtime.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">External Model Key Ref (optional)</label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.api_key_ref || ''}
                            onChange={(e) => handleChange('api_key_ref', e.target.value)}
                            placeholder="integration_secret_identifier"
                        />
                        <p className="text-xs text-muted-foreground">
                            Required only for external models like <code>openai/*</code>. Create an Integration Secret in the Telnyx portal and
                            paste its identifier here (not the raw API key).
                        </p>
                    </div>
                </div>
            </div>

            <div className="space-y-4">
                <h4 className="font-semibold text-sm border-b pb-2">LLM (Chat Completions)</h4>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <label className="text-sm font-medium">
                            Chat API Base URL <span className="text-xs text-muted-foreground ml-2">(chat_base_url)</span>
                        </label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.chat_base_url || 'https://api.telnyx.com/v2/ai'}
                            onChange={(e) => handleChange('chat_base_url', e.target.value)}
                            placeholder="https://api.telnyx.com/v2/ai"
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">
                            Chat Model <span className="text-xs text-muted-foreground ml-2">(chat_model)</span>
                        </label>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.chat_model ?? ''}
                            onChange={(e) => handleChange('chat_model', e.target.value)}
                            placeholder="Qwen/Qwen3-235B-A22B"
                        />
                        <p className="text-xs text-muted-foreground">
                            Telnyx-hosted models like <code>meta-llama/*</code> work with only <code>TELNYX_API_KEY</code>. External models
                            like <code>openai/*</code> require <code>api_key_ref</code>.
                        </p>
                        {!config.chat_model && (
                            <p className="text-xs text-muted-foreground">
                                Not set in YAML. Recommended default: <code>Qwen/Qwen3-235B-A22B</code>.
                            </p>
                        )}
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">
                            Temperature <span className="text-xs text-muted-foreground ml-2">(temperature)</span>
                        </label>
                        <input
                            type="number"
                            step="0.05"
                            min="0"
                            max="2"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.temperature ?? 0.7}
                            onChange={(e) => handleChange('temperature', parseFloat(e.target.value || '0.7'))}
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">
                            Max Tokens (optional) <span className="text-xs text-muted-foreground ml-2">(max_tokens)</span>
                        </label>
                        <input
                            type="number"
                            min="1"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={maxTokensValue}
                            onChange={(e) => {
                                const v = e.target.value;
                                if (!v) {
                                    const next = { ...config };
                                    delete next.max_tokens;
                                    onChange(next);
                                    return;
                                }
                                handleChange('max_tokens', parseInt(v, 10));
                            }}
                            placeholder="150"
                        />
                    </div>
                    <div className="space-y-2">
                        <label className="text-sm font-medium">
                            Response Timeout (sec) <span className="text-xs text-muted-foreground ml-2">(response_timeout_sec)</span>
                        </label>
                        <input
                            type="number"
                            step="0.5"
                            min="0.5"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.response_timeout_sec ?? 30.0}
                            onChange={(e) => handleChange('response_timeout_sec', parseFloat(e.target.value || '30'))}
                        />
                    </div>
                </div>
            </div>
        </div>
    );
};

export default TelnyxProviderForm;
