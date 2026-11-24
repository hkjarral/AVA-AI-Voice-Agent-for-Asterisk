/**
 * Modular Provider Subtype Registry
 *
 * Maps each capability (STT/LLM/TTS) to its available subtypes and their
 * required YAML fields.  The field `key` values match exactly what the
 * AI engine expects in `config/ai-agent.yaml`.
 */

import fishAudioModels from './fishAudioModels.json';
import { modelIds, providerDefault, providerVoices } from './providerCatalog';

export const FISH_AUDIO_MODELS = fishAudioModels as string[];

export interface SubtypeField {
  key: string;
  label: string;
  type: 'text' | 'number' | 'combobox' | 'select' | 'password' | 'string-list';
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
      { key: 'chat_model', label: 'Model', type: 'combobox', required: true, placeholder: 'mlx-community/Qwen3-8B-4bit', default: providerDefault('openai', 'chat_model'), suggestions: modelIds('openai', 'llm') },
      { key: 'reasoning_effort', label: 'Reasoning Effort', type: 'combobox', required: false, suggestions: ['none', 'low', 'medium', 'high'], tooltip: 'Optional. GPT-6 Luna tool calling uses none. Leave empty for compatible endpoints with different parameter support.' },
      { key: 'temperature', label: 'Temperature', type: 'number', required: false, default: 0.7 },
      { key: 'max_tokens', label: 'Max Tokens', type: 'number', required: false, default: 200, tooltip: 'Limits completion tokens, including hidden reasoning. Reasoning models can need a larger budget to produce visible text; test response latency and output before rollout.' },
      { key: 'response_timeout_sec', label: 'Response Timeout (sec)', type: 'number', required: false, default: 15, tooltip: 'Max wait time for LLM response. Increase for complex prompts or slow endpoints.' },
    ],
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    description: 'DeepSeek cloud models through the official OpenAI-compatible API',
    yamlType: 'openai',
    fields: [
      { key: 'chat_base_url', label: 'Chat API Base URL', type: 'text', required: true, placeholder: 'https://api.deepseek.com', default: 'https://api.deepseek.com' },
      { key: 'chat_model', label: 'Model', type: 'combobox', required: true, default: providerDefault('deepseek', 'chat_model'), suggestions: modelIds('deepseek', 'llm') },
      { key: 'temperature', label: 'Temperature', type: 'number', required: false, default: 0.3 },
      { key: 'max_tokens', label: 'Max Tokens', type: 'number', required: false, default: 200 },
      { key: 'response_timeout_sec', label: 'Response Timeout (sec)', type: 'number', required: false, default: 30, tooltip: 'Max wait time for a DeepSeek response.' },
    ],
  },
  {
    id: 'google',
    label: 'Google Gemini',
    description: 'Google Generative Language API (Gemini models)',
    yamlType: 'google',
    fields: [
      { key: 'llm_base_url', label: 'Generative Language API URL', type: 'text', required: true, default: 'https://generativelanguage.googleapis.com/v1', placeholder: 'https://generativelanguage.googleapis.com/v1' },
      { key: 'llm_model', label: 'Model', type: 'combobox', required: true, default: providerDefault('google', 'llm_model'), suggestions: modelIds('google', 'llm') },
      { key: 'temperature', label: 'Temperature', type: 'number', required: false, default: 0.7, tooltip: 'Ignored by Gemini 3.6+, Gemini 3.5 Flash (routes to 3.6), and Gemini 3.5 Flash-Lite. Saved values are preserved for models that support it.' },
      { key: 'max_output_tokens', label: 'Max Output Tokens', type: 'number', required: false, default: 200 },
      { key: 'timeout_sec', label: 'Timeout (sec)', type: 'number', required: false, default: 30 },
    ],
  },
  {
    id: 'ollama',
    label: 'Ollama',
    description: 'Self-hosted Ollama server with local models',
    yamlType: 'ollama',
    fields: [
      { key: 'base_url', label: 'Ollama URL', type: 'text', required: true, default: 'http://localhost:11434', placeholder: 'http://localhost:11434' },
      { key: 'model', label: 'Model', type: 'combobox', required: true, placeholder: 'llama3.2', suggestions: modelIds('ollama', 'llm') },
      { key: 'temperature', label: 'Temperature', type: 'number', required: false, default: 0.7 },
      { key: 'max_tokens', label: 'Max Tokens', type: 'number', required: false, default: 200 },
      { key: 'timeout_sec', label: 'Timeout (sec)', type: 'number', required: false, default: 60 },
      { key: 'tools_enabled', label: 'Tool Calling', type: 'combobox', required: false, default: 'true', suggestions: ['true', 'false'] },
    ],
  },
  {
    id: 'telnyx',
    label: 'Telnyx AI Inference',
    description: 'Telnyx-hosted LLM inference and supported external models',
    yamlType: 'telnyx',
    fields: [
      { key: 'chat_base_url', label: 'API Base URL', type: 'text', required: true, default: 'https://api.telnyx.com/v2/ai', placeholder: 'https://api.telnyx.com/v2/ai' },
      { key: 'chat_model', label: 'Model', type: 'combobox', required: true, default: providerDefault('telnyx', 'chat_model'), suggestions: modelIds('telnyx', 'llm') },
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
      { key: 'chat_model', label: 'Model', type: 'combobox', required: true, default: providerDefault('minimax', 'chat_model'), suggestions: modelIds('minimax', 'llm') },
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
      { key: 'auth_token', label: 'Auth Token', type: 'password', required: false, placeholder: 'Optional WebSocket auth token' },
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
    id: 'deepgram', label: 'Deepgram Nova', description: 'Streaming Nova recognition', yamlType: 'deepgram',
    fields: [
      { key: 'model', label: 'Model', type: 'combobox', default: providerDefault('deepgram', 'model'), suggestions: modelIds('deepgram', 'stt') },
      { key: 'stt_language', label: 'Language', type: 'text', default: 'en-US' },
    ],
  },
  {
    id: 'deepgram_flux', label: 'Deepgram Flux', description: 'Conversational recognition with model-owned turn detection', yamlType: 'deepgram',
    fields: [
      { key: 'model', label: 'Model', type: 'combobox', default: 'flux-general-en', suggestions: modelIds('deepgram', 'flux_stt') },
      { key: 'language_hints', label: 'Language Hints', type: 'string-list', tooltip: 'Comma-separated language codes for flux-general-multi. Leave empty for automatic detection.' },
      { key: 'eot_threshold', label: 'End-of-turn Threshold', type: 'number', default: 0.7 },
      { key: 'eot_timeout_ms', label: 'End-of-turn Timeout (ms)', type: 'number', default: 5000 },
    ],
  },
  {
    id: 'google', label: 'Google Cloud STT', description: 'Google Cloud Speech-to-Text v1 recognition', yamlType: 'google',
    fields: [
      { key: 'stt_base_url', label: 'STT API URL', type: 'text', default: 'https://speech.googleapis.com/v1' },
      { key: 'stt_language_code', label: 'Language', type: 'text', default: 'en-US' },
    ],
  },
  {
    id: 'openai',
    label: 'OpenAI Transcription',
    description: 'OpenAI file transcription API or compatible endpoint',
    yamlType: 'openai',
    fields: [
      { key: 'stt_base_url', label: 'STT API Base URL', type: 'text', required: false, default: 'https://api.openai.com/v1/audio/transcriptions' },
      { key: 'stt_model', label: 'Model', type: 'combobox', required: false, default: providerDefault('openai', 'stt_model'), suggestions: modelIds('openai', 'stt') },
      { key: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: '${OPENAI_API_KEY}' },
      { key: 'chunk_size_ms', label: 'Chunk Size (ms)', type: 'number', required: false, default: 100 },
    ],
  },
  {
    id: 'groq',
    label: 'Groq Whisper',
    description: 'Groq-hosted Whisper transcription (fast inference)',
    yamlType: 'groq',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: '${GROQ_API_KEY}' },
      { key: 'stt_model', label: 'Model', type: 'combobox', required: false, default: providerDefault('groq', 'stt_model'), suggestions: modelIds('groq', 'stt') },
    ],
  },
  {
    id: 'azure',
    label: 'Azure Speech-to-Text',
    description: 'Microsoft Azure Cognitive Services speech recognition',
    yamlType: 'azure',
    fields: [
      { key: 'api_key', label: 'Azure Speech Key', type: 'password', required: true, placeholder: '${AZURE_SPEECH_KEY}' },
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
      { key: 'auth_token', label: 'Auth Token', type: 'password', required: false },
      { key: 'stt_backend', label: 'STT Backend', type: 'combobox', required: false, default: 'vosk', suggestions: modelIds('local', 'stt_backends') },
      { key: 'chunk_ms', label: 'Chunk Size (ms)', type: 'number', required: false, default: 320 },
    ],
  },
];

// ---------------------------------------------------------------------------
// TTS subtypes
// ---------------------------------------------------------------------------
const OUTPUT_RESAMPLER_FIELD: SubtypeField = {
  key: 'output_resampler',
  label: 'Output Downsampling',
  type: 'combobox',
  required: false,
  default: 'inherit',
  suggestions: ['inherit', 'linear', 'bandlimited'],
  tooltip: 'inherit uses the Agent Audio Profile; linear preserves current behavior; bandlimited removes out-of-band energy before 16/24 kHz audio is reduced to 8 kHz telephony.',
};
const TTS_SUBTYPES: ProviderSubtype[] = [
  {
    id: 'fishaudio',
    label: 'Fish Audio',
    description: 'Fish Audio streaming PCM text-to-speech for telephony',
    yamlType: 'fishaudio',
    fields: [
      { key: 'base_url', label: 'API Base URL', type: 'text', required: true, default: 'https://api.fish.audio/v1', placeholder: 'https://api.fish.audio/v1', tooltip: 'HTTPS is required except for an explicit loopback mock.' },
      { key: 'transport', label: 'Transport', type: 'select', required: false, default: 'http', suggestions: ['http', 'websocket'], tooltip: 'HTTP sends one request per fragment. WebSocket streams the whole response turn over one realtime session.' },
      { key: 'ws_base_url', label: 'Realtime WebSocket URL', type: 'text', required: false, placeholder: 'wss://api.fish.audio/v1', tooltip: 'Optional override for websocket transport. Secure WSS is required except for an explicit loopback mock.' },
      { key: 'model', label: 'Model', type: 'select', required: true, default: providerDefault('fishaudio', 'model'), suggestions: FISH_AUDIO_MODELS },
      { key: 'reference_id', label: 'Voice Reference ID', type: 'text', required: true, placeholder: 'Voice model ID from the Fish Audio library' },
      { key: 'audio_format', label: 'Audio Format', type: 'combobox', required: false, default: 'pcm', suggestions: ['pcm', 'wav'], tooltip: 'PCM streams progressively and is recommended for calls. WAV is buffered before playback.' },
      { key: 'sample_rate', label: 'Provider Sample Rate (Hz)', type: 'combobox', required: false, placeholder: 'Leave blank to follow the call', suggestions: ['8000', '16000', '24000', '32000', '44100'] },
      { key: 'latency', label: 'Latency Mode', type: 'combobox', required: false, default: 'low', suggestions: ['low', 'balanced', 'normal'] },
      { key: 'chunk_length', label: 'Synthesis Chunk Length', type: 'number', required: false, default: 200, tooltip: 'Fish Audio accepts values from 100 to 300.' },
      { key: 'temperature', label: 'Temperature', type: 'number', required: false, default: 0.7 },
      { key: 'top_p', label: 'Top P', type: 'number', required: false, default: 0.7 },
      { key: 'connect_timeout_sec', label: 'Connect Timeout (sec)', type: 'number', required: false, default: 10 },
      { key: 'read_timeout_sec', label: 'Inter-chunk Timeout (sec)', type: 'number', required: false, default: 30, tooltip: 'Fails a stalled stream while allowing long synthesis to continue when chunks keep arriving.' },
      OUTPUT_RESAMPLER_FIELD,
    ],
  },
  {
    id: 'openai',
    label: 'OpenAI TTS',
    description: 'OpenAI text-to-speech API',
    yamlType: 'openai',
    fields: [
      { key: 'tts_transport', label: 'TTS API Workflow', type: 'select', default: providerDefault('openai', 'tts_transport'), suggestions: ['auto', 'speech', 'realtime'], tooltip: 'auto selects Realtime for gpt-realtime models and Speech for existing speech models. Speech models retire January 6, 2027; changing workflows needs a real-call test.' },
      { key: 'tts_realtime_base_url', label: 'Realtime WebSocket URL', type: 'text', default: 'wss://api.openai.com/v1/realtime' },
      { key: 'tts_realtime_timeout_sec', label: 'Realtime Synthesis Timeout (sec)', type: 'number', default: Number(providerDefault('openai', 'tts_realtime_timeout_sec')), tooltip: 'Total Realtime synthesis budget per text fragment, from 1 to 120 seconds. Separate from the legacy Speech API response timeout.' },
      { key: 'tts_instructions', label: 'Speech Instructions', type: 'text', tooltip: 'Optional style instructions. Realtime and compatible speech models only.' },
      { key: 'tts_base_url', label: 'TTS API Base URL', type: 'text', required: false, default: 'https://api.openai.com/v1/audio/speech' },
      { key: 'tts_model', label: 'Model', type: 'combobox', required: false, default: providerDefault('openai', 'tts_model'), suggestions: modelIds('openai', 'tts') },
      { key: 'voice', label: 'Voice', type: 'combobox', required: false, default: 'alloy', suggestions: providerVoices('openai', 'tts').map(voice => voice.id) },
      { key: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: '${OPENAI_API_KEY}' },
      OUTPUT_RESAMPLER_FIELD,
    ],
  },
  {
    id: 'groq',
    label: 'Groq TTS',
    description: 'Groq-hosted text-to-speech (Orpheus voices)',
    yamlType: 'groq',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: '${GROQ_API_KEY}' },
      { key: 'tts_model', label: 'Model', type: 'combobox', required: false, default: providerDefault('groq', 'tts_model'), suggestions: modelIds('groq', 'tts') },
      { key: 'voice', label: 'Voice', type: 'combobox', required: false, default: providerDefault('groq', 'voice'), suggestions: providerVoices('groq', 'tts').map(voice => voice.id) },
      OUTPUT_RESAMPLER_FIELD,
    ],
  },
  {
    id: 'elevenlabs',
    label: 'ElevenLabs',
    description: 'ElevenLabs premium text-to-speech',
    yamlType: 'elevenlabs',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: '${ELEVENLABS_API_KEY}' },
      { key: 'voice_id', label: 'Voice ID', type: 'text', required: false, default: '21m00Tcm4TlvDq8ikWAM', placeholder: 'Rachel voice ID' },
      { key: 'model_id', label: 'Model', type: 'combobox', required: false, default: providerDefault('elevenlabs', 'model_id'), suggestions: modelIds('elevenlabs', 'tts') },
      { key: 'stability', label: 'Stability', type: 'number', default: 0.5, tooltip: 'Voice consistency from 0 to 1. Eleven v3 accepts only 0, 0.5, or 1 and does not use speaker boost.' },
      { key: 'similarity_boost', label: 'Similarity', type: 'number', default: 0.75, tooltip: 'Voice similarity from 0 to 1. Supported by Eleven v4 and the v2 family; not sent for Eleven v3.' },
      { key: 'output_format', label: 'Output Format', type: 'combobox', required: false, default: 'ulaw_8000', suggestions: ['ulaw_8000', 'pcm_16000', 'pcm_24000'] },
      OUTPUT_RESAMPLER_FIELD,
    ],
  },
  {
    id: 'deepgram',
    label: 'Deepgram Aura / Flux',
    description: 'Deepgram text-to-speech with native telephony output support',
    yamlType: 'deepgram',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: '${DEEPGRAM_API_KEY}' },
      { key: 'tts_version', label: 'TTS API Version', type: 'select', default: providerDefault('deepgram', 'tts_version'), suggestions: ['v1', 'v2'], tooltip: 'v1 uses Aura voices; opt-in v2 uses Flux voices and raw audio. Change the voice model to match the version.' },
      { key: 'tts_model', label: 'Voice Model', type: 'combobox', required: false, default: providerDefault('deepgram', 'tts_model'), suggestions: providerVoices('deepgram', 'tts').map(voice => voice.id) },
      { key: 'tts_speed', label: 'Speaking Rate', type: 'number', tooltip: 'Aura: 0.7–1.5. Flux: 0.5–1.5 in steps of 0.05. Empty uses the provider default.' },
      { key: 'tts_expressivity', label: 'Flux Expressivity', type: 'number', tooltip: 'Flux v2 only: integer -2 to 2. Empty uses the provider default.' },
      { key: 'output_encoding', label: 'Output Encoding', type: 'combobox', required: false, default: 'mulaw', suggestions: ['mulaw', 'linear16'] },
      { key: 'output_sample_rate_hz', label: 'Output Sample Rate (Hz)', type: 'number', required: false, default: 8000 },
      OUTPUT_RESAMPLER_FIELD,
    ],
  },
  {
    id: 'google',
    label: 'Google Cloud TTS',
    description: 'Google Cloud text-to-speech',
    yamlType: 'google',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: '${GOOGLE_API_KEY}' },
      { key: 'tts_voice_name', label: 'Voice', type: 'text', required: false, default: providerDefault('google', 'tts_voice_name') },
      { key: 'tts_audio_encoding', label: 'Output Encoding', type: 'combobox', required: false, default: 'MULAW', suggestions: ['MULAW', 'LINEAR16'] },
      { key: 'tts_sample_rate_hz', label: 'Output Sample Rate (Hz)', type: 'number', required: false, default: 8000 },
      OUTPUT_RESAMPLER_FIELD,
    ],
  },
  {
    id: 'cambai',
    label: 'CAMB AI',
    description: 'CAMB AI MARS text-to-speech',
    yamlType: 'cambai',
    fields: [
      { key: 'api_key', label: 'API Key', type: 'password', required: true, placeholder: '${CAMB_API_KEY}' },
      { key: 'voice_id', label: 'Voice ID', type: 'number', required: false, default: 147320 },
      { key: 'speech_model', label: 'Speech Model', type: 'combobox', required: false, default: providerDefault('cambai', 'speech_model'), suggestions: modelIds('cambai', 'tts') },
      { key: 'language', label: 'Language', type: 'text', required: false, default: 'en-us' },
      { key: 'output_format', label: 'Output Format', type: 'combobox', required: false, default: 'pcm_s16le', suggestions: ['pcm_s16le', 'wav'] },
      OUTPUT_RESAMPLER_FIELD,
    ],
  },
  {
    id: 'azure',
    label: 'Azure Text-to-Speech',
    description: 'Microsoft Azure Cognitive Services speech synthesis',
    yamlType: 'azure',
    fields: [
      { key: 'api_key', label: 'Azure Speech Key', type: 'password', required: true, placeholder: '${AZURE_SPEECH_KEY}' },
      { key: 'region', label: 'Region', type: 'combobox', required: true, default: 'eastus', suggestions: ['eastus', 'westus2', 'westeurope', 'southeastasia'] },
      { key: 'voice_name', label: 'Voice', type: 'combobox', required: false, default: providerDefault('azure', 'voice_name'), suggestions: providerVoices('azure', 'tts').map(voice => voice.id) },
      { key: 'output_format', label: 'Output Format', type: 'combobox', required: false, default: 'raw-8khz-16bit-mono-pcm', suggestions: ['raw-8khz-16bit-mono-pcm', 'raw-16khz-16bit-mono-pcm'] },
      OUTPUT_RESAMPLER_FIELD,
    ],
  },
  {
    id: 'local',
    label: 'Local AI Server',
    description: 'TTS running inside the local-ai-server container (Piper, Kokoro)',
    yamlType: 'local',
    fields: [
      { key: 'ws_url', label: 'WebSocket URL', type: 'text', required: true, default: 'ws://127.0.0.1:8765', placeholder: 'ws://127.0.0.1:8765' },
      { key: 'auth_token', label: 'Auth Token', type: 'password', required: false },
      { key: 'tts_backend', label: 'TTS Backend', type: 'combobox', required: false, default: 'piper', suggestions: modelIds('local', 'tts_backends') },
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
  if (cap === 'stt' && yamlType === 'deepgram' && String(config?.model || '').startsWith('flux-')) {
    return MODULAR_SUBTYPES.stt.find(s => s.id === 'deepgram_flux');
  }
  if (cap === 'llm' && yamlType === 'openai') {
    const baseUrl = String(config?.chat_base_url || config?.base_url || '').toLowerCase();
    if (baseUrl.includes('api.deepseek.com')) {
      return MODULAR_SUBTYPES.llm.find(s => s.id === 'deepseek');
    }
  }
  return findSubtype(cap as Capability, yamlType);
};
