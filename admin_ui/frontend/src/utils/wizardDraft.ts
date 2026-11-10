const KEY = 'ava-onboarding-draft-v1';
// Deliberate allowlist: API keys, ARI passwords and provider tokens never enter browser storage.
const FIELDS = ['provider', 'deployment_topology', 'audio_transport', 'media_advertise_host',
    'asterisk_host', 'asterisk_username', 'asterisk_port', 'asterisk_scheme', 'asterisk_app',
    'asterisk_server_ip', 'asterisk_ssl_verify', 'hybrid_llm_provider', 'local_stt_backend',
    'local_stt_model', 'local_tts_backend', 'local_tts_model', 'local_llm_model'];

export function saveWizardDraft(step: number, config: object) {
    try {
        const entries = Object.entries(config).filter(([key]) => FIELDS.includes(key));
        sessionStorage.setItem(KEY, JSON.stringify({ step, config: Object.fromEntries(entries) }));
    } catch { /* Setup remains usable when browser storage is disabled. */ }
}

export function readWizardDraft(): { step: number; config: Record<string, unknown> } | null {
    try {
        const draft = JSON.parse(sessionStorage.getItem(KEY) || 'null');
        if (!draft || !Number.isInteger(draft.step) || draft.step < 1 || draft.step > 4) return null;
        // Resume credentials at step 3 because secrets are intentionally not retained.
        return { step: Math.min(draft.step, 3), config: Object.fromEntries(Object.entries(draft.config || {}).filter(([key]) => FIELDS.includes(key))) };
    } catch { return null; }
}

export function clearWizardDraft() {
    try { sessionStorage.removeItem(KEY); } catch { /* No draft to clear. */ }
}
