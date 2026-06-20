import React from 'react';
import HelpTooltip from '../../ui/HelpTooltip';

interface TelnyxConfig {
    api_key?: string;
    api_key_ref?: string;
    chat_base_url?: string;
    chat_model?: string;
    temperature?: number;
    max_tokens?: number | null;
    response_timeout_sec?: number;
    [key: string]: unknown;
}

interface TelnyxProviderFormProps {
    config: TelnyxConfig;
    onChange: (newConfig: TelnyxConfig) => void;
    /** Unused here; accepted for prop-shape parity with full-agent forms. */
    providerKey?: string;
}

const TelnyxProviderForm: React.FC<TelnyxProviderFormProps> = ({ config, onChange }) => {
    const handleChange = (field: string, value: unknown) => {
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">Telnyx API Key (env or literal)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Telnyx API Key</strong> — credential for Telnyx Inference. Prefer the env-var form
                                        {' '}<code>{'${TELNYX_API_KEY}'}</code> so the value is injected at runtime instead of stored in YAML.
                                        Create the key in the Telnyx portal under <em>API Keys</em>.
                                    </>
                                }
                                link="https://developers.telnyx.com/docs/inference/overview"
                                linkText="Telnyx Inference docs"
                            />
                        </div>
                        <input
                            type="password"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.api_key ?? '${TELNYX_API_KEY}'}
                            onChange={(e) => handleChange('api_key', e.target.value)}
                            placeholder="${TELNYX_API_KEY}"
                        />
                        <p className="text-xs text-muted-foreground">
                            Recommended: leave as <code>${'{TELNYX_API_KEY}'}</code>. The AI Engine will inject this at runtime.
                        </p>
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">External Model Key Ref (optional)</label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>External Model Key Ref</strong> — only required when <code>chat_model</code> is an
                                        external model like <code>openai/*</code>. Create an Integration Secret in the Telnyx portal and
                                        paste its identifier (not the raw upstream API key). Leave blank for Telnyx-hosted models
                                        like <code>Qwen/*</code> or <code>meta-llama/*</code>.
                                    </>
                                }
                                link="https://developers.telnyx.com/docs/inference/overview"
                                linkText="Telnyx Inference docs"
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Chat API Base URL <span className="text-xs text-muted-foreground ml-2">(chat_base_url)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Chat API Base URL</strong> — Telnyx Inference base URL. The engine appends
                                        {' '}<code>/chat/completions</code> (OpenAI-compatible). Leave at the default
                                        {' '}<code>https://api.telnyx.com/v2/ai</code> unless Telnyx instructs you otherwise.
                                    </>
                                }
                                link="https://developers.telnyx.com/docs/inference/overview"
                                linkText="Telnyx Inference docs"
                            />
                        </div>
                        <input
                            type="text"
                            className="w-full p-2 rounded border border-input bg-background"
                            value={config.chat_base_url ?? 'https://api.telnyx.com/v2/ai'}
                            onChange={(e) => handleChange('chat_base_url', e.target.value)}
                            placeholder="https://api.telnyx.com/v2/ai"
                        />
                    </div>
                    <div className="space-y-2">
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Chat Model <span className="text-xs text-muted-foreground ml-2">(chat_model)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Chat Model</strong> — which Telnyx-hosted model serves chat completions.
                                        <ul className="list-disc pl-4 mt-1 space-y-0.5">
                                            <li><code>Qwen/Qwen3-235B-A22B</code> — default, strong reasoning</li>
                                            <li><code>meta-llama/Meta-Llama-3.1-70B-Instruct</code> — Llama 3.1 70B</li>
                                            <li><code>mistralai/Mixtral-8x7B-Instruct-v0.1</code> — Mixtral MoE</li>
                                        </ul>
                                        Pricing varies by model (~$0.40 / 1M input tokens for Qwen). External models
                                        like <code>openai/*</code> additionally require <code>api_key_ref</code>.
                                    </>
                                }
                                link="https://developers.telnyx.com/docs/inference/overview"
                                linkText="Telnyx Inference docs"
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Temperature <span className="text-xs text-muted-foreground ml-2">(temperature)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Temperature</strong> — sampling randomness, 0.0–2.0. Lower (0.2–0.5) for
                                        deterministic transactional flows; higher (0.7–1.0) for natural conversational tone.
                                        Default <code>0.7</code> works well for most voice agents.
                                    </>
                                }
                                link="https://developers.telnyx.com/docs/inference/overview"
                                linkText="Telnyx Inference docs"
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Max Tokens (optional) <span className="text-xs text-muted-foreground ml-2">(max_tokens)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Max Tokens</strong> — caps the response length per turn. Leave blank to let the
                                        model decide (recommended for voice — short replies are natural). Set <code>150</code>–
                                        <code>250</code> to enforce concise spoken answers and lower cost.
                                    </>
                                }
                                link="https://developers.telnyx.com/docs/inference/overview"
                                linkText="Telnyx Inference docs"
                            />
                        </div>
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
                        <div className="flex items-center gap-1.5">
                            <label className="text-sm font-medium">
                                Response Timeout (sec) <span className="text-xs text-muted-foreground ml-2">(response_timeout_sec)</span>
                            </label>
                            <HelpTooltip
                                content={
                                    <>
                                        <strong>Response Timeout</strong> — how long to wait for a full chat completion before
                                        aborting the turn. Default <code>30</code>s is safe; lower (10–15s) if you want faster
                                        fallback on slow responses.
                                    </>
                                }
                                link="https://developers.telnyx.com/docs/inference/overview"
                                linkText="Telnyx Inference docs"
                            />
                        </div>
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
