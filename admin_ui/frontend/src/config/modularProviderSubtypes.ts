/**
 * Modular Provider Subtype Registry
 *
 * Maps each capability (STT/LLM/TTS) to its available subtypes and their
 * required YAML fields.  The field `key` values match exactly what the
 * AI engine expects in `config/ai-agent.yaml`.
 */

export interface SubtypeField {
  key: string;
  label: string;
  type: 'text' | 'number' | 'combobox' | 'password';
  required?: boolean;
  placeholder?: string;
  default?: string | number;
  suggestions?: string[];
  tooltip?: string;
}

export interface ProviderSubtype {
  id: string;
  label: string;
  description: string;
  /** Value written to `type:` in YAML */
  yamlType: string;
  fields: SubtypeField[];
}

export type Capability = 'stt' | 'llm' | 'tts';

// ---------------------------------------------------------------------------
// LLM subtypes
// ---------------------------------------------------------------------------
const LLM_SUBTYPES: ProviderSubtype[] = [
  {
    id: 'openai',
    label: 'OpenAI-Compatible',
    description: 'Any OpenAI-compatible chat/completions API (MLX, vLLM, LiteLLM, Groq, OpenRouter, etc.)',
    yamlType: 'openai',
    fields: [
      { key: 'chat_base_url', label: 'Chat API Base URL', type: 'text', required: true, placeholder: 'http://10.44.0.5:8080/v1', default: 'https://api.openai.com/v1' },
      { key: 'chat_model', label: 'Model', type: 'combobox', required: true, placeholder: 'mlx-community/Qwen3-8B-4bit', suggestions: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'] },
      { key: 'api_key', label: 'API Key', type: 'text', required: false, placeholder: 'not-needed (for self-hosted)', default: 'not-needed', tooltip: 'Use "not-needed" for self-hosted endpoints or ${ENV_VAR} for env references' },
      { key: 'temperature', label: 'Temperature', type: 'number', required: false, default: 0.7 },
      { key: 'max_tokens', label: 'Max Tokens', type: 'number', required: false, default: 200 },
      { key: 'response_timeout_sec', label: 'Response Timeout (sec)', type: 'number', required: false, default: 15, tooltip: 'Max wait time for LLM response. Increase for complex prompts or slow endpoints.' },
    ],
  },
  {
    id: 'ollama',
    label: 'Ollama',
    description: 'Self-hosted Ollama server with local models',
    yamlType: 'ollama',
    fields: [
      { key: 'base_url', label: 'Ollama URL', type: 'text', required: true, default: 'http://localhost:11434', placeholder: 'http://localhost:11434' },
      { key: 'model', label: 'Model', type: 'combobox', required: true, placeholder: 'llama3.2', suggestions: ['llama3.2', 'qwen3', 'qwen3:8b', 'mistral', 'phi3', 'gemma2'] },
      { key: 'temperature', label: 'Temperature', type: 'number', required: false, default: 0.7 },
      { key: 'max_tokens', label: 'Max Tokens', type: 'number', required: false, default: 200 },
      { key: 'timeout_sec', label: 'Timeout (sec)', type: 'number', required: false, default: 60 },
      { key: 'tools_enabled', label: 'Tool Calling', type: 'combobox', required: false, default: 'true', suggestions: ['true', 'false'] },
    ],
  },
  {
    id: 'telnyx',
    label: 'Telnyx AI Inference',
    description: 'Telnyx cloud LLM inference with Qwen, Llama, and external models',
    yamlType: 'telnyx',
    fields: [
      { key: 'chat_base_url', label: 'API Base URL', type: 'text', required: true, default: 'https://api.telnyx.com/v2/ai', placeholder: 'https://api.telnyx.com/v2/ai' },
      { key: 'chat_model', label: 'Model', type: 'combobox', required: true, placeholder: 'Qwen/Qwen3-235B-A22B', suggestions: ['Qwen/Qwen3-235B-A22B', 'meta-llama/Meta-Llama-3.1-70B-Instruct', 'meta-llama/Meta-Llama-3.1-8B-Instruct'] },
      { key: 'api_key', label: 'API Key', type: 'text', required: true, placeholder: '${TELNYX_API_KEY}' },
      { key: 'temperature', label: 'Temperature', type: 'number', required: false, default: 0.7 },
      { key: 'max_tokens', label: 'Max Tokens', type: 'number', required: false, default: 200 },
      { key: 'response_timeout_sec', label: 'Response Timeout (sec)', type: 'number', required: false, default: 30 },
    ],
  },
  {
    id: 'minimax',
    label: 'MiniMax',
    description: 'MiniMax cloud LLM API',
    yamlType: 'minimax',
    fields: [
      { key: 'chat_base_url', label: 'API Base URL', type: 'text', required: true, default: 'https://api.minimax.io/v1', placeholder: 'https://api.minimax.io/v1' },
      { key: 'chat_model', label: 'Model', type: 'combobox', required: true, suggestions: ['MiniMax-M2.7', 'MiniMax-M2.7-highspeed', 'MiniMax-M2.5'] },
      { key: 'api_key', label: 'API Key', type: 'text', required: true, placeholder: '${MINIMAX_API_KEY}' },
      { key: 'temperature', label: 'Temperature', type: 'number', required: false, default: 0.7 },
      { key: 'response_timeout_sec', label: 'Response Timeout (sec)', type: 'number', required: false, default: 30 },
    ],
  },
  {
    id: 'local',
    label: 'Local AI Server (WebSocket)',
    description: 'LLM running inside the local-ai-server container',
    yamlType: 'local',
    fields: [
      { key: 'ws_url', label: 'WebSocket URL', type: 'text', required: true, default: 'ws://127.0.0.1:8765', placeholder: 'ws://127.0.0.1:8765' },
      { key: 'auth_token', label: 'Auth Token', type: 'text', required: false, placeholder: 'Optional WebSocket auth token' },
      { key: 'max_tokens', label: 'Max Tokens', type: 'number', required: false, default: 64 },
      { key: 'temperature', label: 'Temperature', type: 'number', required: false, default: 0.4 },
    ],
  },
];

// ---------------------------------------------------------------------------
// STT subtypes
// ---------------------------------------------------------------------------
const STT_SUBTYPES: ProviderSubtype[] = [
  {
    id: 'openai',
    label: 'OpenAI Whisper',
    description: 'OpenAI Whisper transcription API or compatible endpoint',
    yamlType: 'openai',
    fields: [
      { key: 'stt_base_url', label: 'STT API Base URL', type: 'text', required: false, default: 'https://api.openai.com/v1/audio/transcriptions' },
      { key: 'stt_model', label: 'Model', type: 'combobox', required: false, default: 'whisper-1', suggestions: ['whisper-1', 'gpt-4o-mini-transcribe', 'gpt-4o-transcribe'] },
      { key: 'api_key', label: 'API Key', type: 'text', required: true, placeholder: '${OPENAI_API_KEY}' },
      { key: 'chunk_size_ms', label: 'Chunk Size (ms)', type: 'number', required: false, default: 100 },
    ],
  },
  {
    id: 'groq',
    label: 'Groq Whisper',
    description: 'Groq-hosted Whisper transcription (fast inference)',
    yamlType: 'groq',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'text', required: true, placeholder: '${GROQ_API_KEY}' },
      { key: 'stt_model', label: 'Model', type: 'combobox', required: false, default: 'whisper-large-v3-turbo', suggestions: ['whisper-large-v3-turbo', 'whisper-large-v3'] },
    ],
  },
  {
    id: 'azure',
    label: 'Azure Speech-to-Text',
    description: 'Microsoft Azure Cognitive Services speech recognition',
    yamlType: 'azure',
    fields: [
      { key: 'api_key', label: 'Azure Speech Key', type: 'text', required: true, placeholder: '${AZURE_SPEECH_KEY}' },
      { key: 'region', label: 'Region', type: 'combobox', required: true, default: 'eastus', suggestions: ['eastus', 'westus2', 'westeurope', 'southeastasia'] },
      { key: 'language', label: 'Language', type: 'combobox', required: false, default: 'en-US', suggestions: ['en-US', 'en-GB', 'es-ES', 'fr-FR', 'de-DE'] },
      { key: 'variant', label: 'Variant', type: 'combobox', required: false, default: 'realtime', suggestions: ['realtime', 'fast'] },
    ],
  },
  {
    id: 'local',
    label: 'Local AI Server',
    description: 'STT running inside the local-ai-server container (Vosk, Sherpa, Kroko, Faster-Whisper)',
    yamlType: 'local',
    fields: [
      { key: 'ws_url', label: 'WebSocket URL', type: 'text', required: true, default: 'ws://127.0.0.1:8765', placeholder: 'ws://127.0.0.1:8765' },
      { key: 'auth_token', label: 'Auth Token', type: 'text', required: false },
      { key: 'stt_backend', label: 'STT Backend', type: 'combobox', required: false, default: 'vosk', suggestions: ['vosk', 'sherpa', 'kroko', 'faster_whisper', 'whisper_cpp'] },
      { key: 'chunk_ms', label: 'Chunk Size (ms)', type: 'number', required: false, default: 320 },
    ],
  },
];

// ---------------------------------------------------------------------------
// TTS subtypes
// ---------------------------------------------------------------------------
const TTS_SUBTYPES: ProviderSubtype[] = [
  {
    id: 'openai',
    label: 'OpenAI TTS',
    description: 'OpenAI text-to-speech API',
    yamlType: 'openai',
    fields: [
      { key: 'tts_base_url', label: 'TTS API Base URL', type: 'text', required: false, default: 'https://api.openai.com/v1/audio/speech' },
      { key: 'tts_model', label: 'Model', type: 'combobox', required: false, default: 'tts-1', suggestions: ['tts-1', 'tts-1-hd', 'gpt-4o-mini-tts'] },
      { key: 'voice', label: 'Voice', type: 'combobox', required: false, default: 'alloy', suggestions: ['alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'nova', 'onyx', 'sage', 'shimmer'] },
      { key: 'api_key', label: 'API Key', type: 'text', required: true, placeholder: '${OPENAI_API_KEY}' },
    ],
  },
  {
    id: 'groq',
    label: 'Groq TTS',
    description: 'Groq-hosted text-to-speech (Orpheus voices)',
    yamlType: 'groq',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'text', required: true, placeholder: '${GROQ_API_KEY}' },
      { key: 'tts_model', label: 'Model', type: 'combobox', required: false, default: 'canopylabs/orpheus-v1-english', suggestions: ['canopylabs/orpheus-v1-english', 'canopylabs/orpheus-arabic-saudi'] },
      { key: 'voice', label: 'Voice', type: 'combobox', required: false, default: 'hannah', suggestions: ['autumn', 'diana', 'hannah', 'austin', 'daniel', 'troy'] },
    ],
  },
  {
    id: 'elevenlabs',
    label: 'ElevenLabs',
    description: 'ElevenLabs premium text-to-speech',
    yamlType: 'elevenlabs',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'text', required: true, placeholder: '${ELEVENLABS_API_KEY}' },
      { key: 'voice_id', label: 'Voice ID', type: 'text', required: false, default: '21m00Tcm4TlvDq8ikWAM', placeholder: 'Rachel voice ID' },
      { key: 'model_id', label: 'Model', type: 'combobox', required: false, default: 'eleven_turbo_v2_5', suggestions: ['eleven_turbo_v2_5', 'eleven_multilingual_v2', 'eleven_monolingual_v1'] },
      { key: 'output_format', label: 'Output Format', type: 'combobox', required: false, default: 'ulaw_8000', suggestions: ['ulaw_8000', 'pcm_16000', 'pcm_24000', 'mp3_44100'] },
    ],
  },
  {
    id: 'azure',
    label: 'Azure Text-to-Speech',
    description: 'Microsoft Azure Cognitive Services speech synthesis',
    yamlType: 'azure',
    fields: [
      { key: 'api_key', label: 'Azure Speech Key', type: 'text', required: true, placeholder: '${AZURE_SPEECH_KEY}' },
      { key: 'region', label: 'Region', type: 'combobox', required: true, default: 'eastus', suggestions: ['eastus', 'westus2', 'westeurope', 'southeastasia'] },
      { key: 'voice_name', label: 'Voice', type: 'combobox', required: false, default: 'en-US-JennyNeural', suggestions: ['en-US-JennyNeural', 'en-US-GuyNeural', 'en-GB-SoniaNeural'] },
      { key: 'output_format', label: 'Output Format', type: 'combobox', required: false, default: 'raw-8khz-16bit-mono-pcm', suggestions: ['raw-8khz-16bit-mono-pcm', 'raw-16khz-16bit-mono-pcm'] },
    ],
  },
  {
    id: 'local',
    label: 'Local AI Server',
    description: 'TTS running inside the local-ai-server container (Piper, Kokoro)',
    yamlType: 'local',
    fields: [
      { key: 'ws_url', label: 'WebSocket URL', type: 'text', required: true, default: 'ws://127.0.0.1:8765', placeholder: 'ws://127.0.0.1:8765' },
      { key: 'auth_token', label: 'Auth Token', type: 'text', required: false },
      { key: 'tts_backend', label: 'TTS Backend', type: 'combobox', required: false, default: 'piper', suggestions: ['piper', 'kokoro', 'silero', 'melotts'] },
    ],
  },
];

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------
export const MODULAR_SUBTYPES: Record<Capability, ProviderSubtype[]> = {
  llm: LLM_SUBTYPES,
  stt: STT_SUBTYPES,
  tts: TTS_SUBTYPES,
};

/**
 * Find a subtype definition by capability and yamlType (the `type:` value in YAML).
 */
export const findSubtype = (capability: Capability, yamlType: string): ProviderSubtype | undefined => {
  return MODULAR_SUBTYPES[capability]?.find(s => s.yamlType === yamlType);
};

/**
 * Infer subtype from an existing provider config.
 */
export const inferSubtype = (config: any): ProviderSubtype | undefined => {
  const caps = config?.capabilities || [];
  const cap = caps.length === 1 ? caps[0] : null;
  if (!cap || !MODULAR_SUBTYPES[cap as Capability]) return undefined;
  const yamlType = (config?.type || '').toLowerCase();
  if (!yamlType) return undefined;
  return findSubtype(cap as Capability, yamlType);
};
