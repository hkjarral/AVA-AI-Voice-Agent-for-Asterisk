import React, { useState } from 'react';
import axios from 'axios';
import { Loader2, CheckCircle2, XCircle, Server, Cpu, Wrench } from 'lucide-react';

interface OllamaModel {
  name: string;
  size: number;
  modified_at: string;
  tools_capable: boolean;
}

interface OllamaProviderFormProps {
  config: any;
  onChange: (updates: Record<string, any>) => void;
}

const OllamaProviderForm: React.FC<OllamaProviderFormProps> = ({ config, onChange }) => {
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string; models?: OllamaModel[] } | null>(null);
  const [availableModels, setAvailableModels] = useState<OllamaModel[]>([]);

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    
    try {
      const res = await axios.post('/api/ollama/test', {
        base_url: config.base_url || 'http://localhost:11434'
      });
      
      setTestResult({
        success: res.data.success,
        message: res.data.message,
        models: res.data.models
      });
      
      if (res.data.success && res.data.models) {
        setAvailableModels(res.data.models);
      }
    } catch (err: any) {
      setTestResult({
        success: false,
        message: err.response?.data?.message || 'Connection test failed'
      });
    } finally {
      setTesting(false);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes === 0) return 'Unknown';
    const gb = bytes / (1024 * 1024 * 1024);
    if (gb >= 1) return `${gb.toFixed(1)} GB`;
    const mb = bytes / (1024 * 1024);
    return `${mb.toFixed(0)} MB`;
  };

  return (
    <div className="space-y-6">
      {/* Info Banner */}
      <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-4">
        <div className="flex items-start gap-3">
          <Server className="w-5 h-5 text-blue-500 mt-0.5" />
          <div>
            <h4 className="font-medium text-blue-500">Self-Hosted LLM via Ollama</h4>
            <p className="text-sm text-muted-foreground mt-1">
              Run your own local LLM on a Mac Mini, gaming PC, or any machine with Ollama installed.
              No API key required - fully private and self-hosted.
            </p>
          </div>
        </div>
      </div>

      {/* Base URL */}
      <div>
        <label className="block text-sm font-medium mb-2">
          Ollama Server URL <span className="text-red-500">*</span>
        </label>
        <input
          type="text"
          value={config.base_url || 'http://localhost:11434'}
          onChange={(e) => onChange({ base_url: e.target.value })}
          placeholder="http://192.168.1.100:11434"
          className="w-full px-3 py-2 bg-background border rounded-md focus:ring-2 focus:ring-primary"
        />
        <p className="text-xs text-muted-foreground mt-1">
          <strong>Important:</strong> For Docker, use your host machine's IP address (not localhost).
          Run Ollama with: <code className="bg-muted px-1 rounded">OLLAMA_HOST=0.0.0.0 ollama serve</code>
        </p>
      </div>

      {/* Test Connection Button */}
      <div className="flex items-center gap-4">
        <button
          onClick={handleTestConnection}
          disabled={testing}
          className="inline-flex items-center px-4 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 disabled:opacity-50"
        >
          {testing ? (
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
          ) : (
            <Server className="w-4 h-4 mr-2" />
          )}
          {testing ? 'Testing...' : 'Test Connection'}
        </button>
        
        {testResult && (
          <div className={`flex items-center gap-2 ${testResult.success ? 'text-green-500' : 'text-red-500'}`}>
            {testResult.success ? (
              <CheckCircle2 className="w-4 h-4" />
            ) : (
              <XCircle className="w-4 h-4" />
            )}
            <span className="text-sm">{testResult.message}</span>
          </div>
        )}
      </div>

      {/* Model Selection */}
      <div>
        <label className="block text-sm font-medium mb-2">
          Model <span className="text-red-500">*</span>
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            value={config.model || 'llama3.2'}
            onChange={(e) => onChange({ model: e.target.value })}
            placeholder="llama3.2"
            className="flex-1 px-3 py-2 bg-background border rounded-md focus:ring-2 focus:ring-primary"
          />
          {availableModels.length > 0 && (
            <select
              onChange={(e) => {
                if (e.target.value) {
                  onChange({ model: e.target.value });
                }
              }}
              className="px-3 py-2 bg-background border rounded-md focus:ring-2 focus:ring-primary"
              value=""
            >
              <option value="">Select from available...</option>
              {availableModels.map((model) => (
                <option key={model.name} value={model.name}>
                  {model.name} ({formatSize(model.size)}) {model.tools_capable ? '🔧' : ''}
                </option>
              ))}
            </select>
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-1">
          Enter model name or test connection to see available models.
          Models with 🔧 support tool calling (hangup, transfer, etc.)
        </p>
      </div>

      {/* Available Models List (if fetched) */}
      {availableModels.length > 0 && (
        <div>
          <label className="block text-sm font-medium mb-2">Available Models</label>
          <div className="grid grid-cols-2 gap-2 max-h-48 overflow-y-auto">
            {availableModels.map((model) => (
              <button
                key={model.name}
                onClick={() => onChange({ model: model.name })}
                className={`flex items-center justify-between p-2 text-left text-sm border rounded-md hover:bg-muted/50 transition-colors ${
                  config.model === model.name ? 'border-primary bg-primary/10' : 'border-border'
                }`}
              >
                <div className="flex items-center gap-2">
                  <Cpu className="w-4 h-4 text-muted-foreground" />
                  <span className="font-mono">{model.name}</span>
                </div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span>{formatSize(model.size)}</span>
                  {model.tools_capable && (
                    <span title="Supports tool calling">
                      <Wrench className="w-3 h-3 text-green-500" />
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Temperature */}
      <div>
        <label className="block text-sm font-medium mb-2">Temperature</label>
        <input
          type="number"
          step="0.1"
          min="0"
          max="2"
          value={config.temperature ?? 0.7}
          onChange={(e) => onChange({ temperature: parseFloat(e.target.value) })}
          className="w-32 px-3 py-2 bg-background border rounded-md focus:ring-2 focus:ring-primary"
        />
        <p className="text-xs text-muted-foreground mt-1">
          Controls randomness. Lower = more focused, higher = more creative.
        </p>
      </div>

      {/* Max Tokens */}
      <div>
        <label className="block text-sm font-medium mb-2">Max Tokens</label>
        <input
          type="number"
          min="50"
          max="2000"
          value={config.max_tokens ?? 200}
          onChange={(e) => onChange({ max_tokens: parseInt(e.target.value) })}
          className="w-32 px-3 py-2 bg-background border rounded-md focus:ring-2 focus:ring-primary"
        />
        <p className="text-xs text-muted-foreground mt-1">
          Maximum response length. Keep low (100-200) for voice applications.
        </p>
      </div>

      {/* Timeout */}
      <div>
        <label className="block text-sm font-medium mb-2">Timeout (seconds)</label>
        <input
          type="number"
          min="10"
          max="300"
          value={config.timeout_sec ?? 60}
          onChange={(e) => onChange({ timeout_sec: parseInt(e.target.value) })}
          className="w-32 px-3 py-2 bg-background border rounded-md focus:ring-2 focus:ring-primary"
        />
        <p className="text-xs text-muted-foreground mt-1">
          Local models may be slower. Increase for larger models.
        </p>
      </div>

      <div className="text-xs text-muted-foreground">
        Tools are allowlisted per <strong>Context</strong>. Your selected model must support tool calling for actions to work.
      </div>

      {/* Tool Capable Models Info */}
      <div className="bg-muted/50 rounded-lg p-4">
        <h4 className="font-medium text-sm mb-2 flex items-center gap-2">
          <Wrench className="w-4 h-4" />
          Models with Tool Calling Support
        </h4>
        <div className="flex flex-wrap gap-2">
          {['llama3.2', 'llama3.1', 'mistral', 'mistral-nemo', 'qwen2.5', 'command-r'].map((model) => (
            <span key={model} className="px-2 py-1 bg-background rounded text-xs font-mono">
              {model}
            </span>
          ))}
        </div>
        <p className="text-xs text-muted-foreground mt-2">
          These models can use tools like hangup_call, transfer, send_email, etc.
          Other models will work for conversation but cannot execute actions.
        </p>
      </div>
    </div>
  );
};

export default OllamaProviderForm;
