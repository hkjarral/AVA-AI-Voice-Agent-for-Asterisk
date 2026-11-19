export interface PushoverRecipient {
    label: string;
    enabled: boolean;
    key_file: string;
}
export interface PushoverCredentials {
    token: string;
    recipients: Record<string, string>;
}
export interface PushoverDraft {
    phase?: string;
    description?: string;
    recipient_label?: string;
    token_file?: string;
    key_file?: string;
    recipients?: Record<string, PushoverRecipient>;
    title?: string;
    body?: string;
    priority?: number;
    sound?: string;
    timeout_ms?: number;
    destination_key?: string;
    suppress_after_tool?: string;
    [key: string]: unknown;
}
// Blank saved templates intentionally use the matching runtime fallback. Changing
// summary settings updates the hint/default without rewriting custom templates.
export function pushoverDefaults(phase = 'in_call', generateSummary = false) {
    const titles: Record<string, string> = {
        pre_call: '{agent}: incoming call',
        in_call: '{agent}: call update',
        post_call: '{agent}: call complete',
    };
    let body = 'Caller: {caller_number}\nCall: {call_id}';
    if (phase === 'post_call') body += '\nOutcome: {outcome}';
    if (phase !== 'pre_call' && generateSummary) body += '\nSummary: {summary}';
    return { title: titles[phase] || titles.in_call, body };
}
export function pushoverRecipients(value: PushoverDraft): Record<string, PushoverRecipient> {
    if (value.recipients !== undefined) return value.recipients || {};
    if (value.key_file || value.recipient_label)
        return {
            legacy: {
                label: value.recipient_label || '',
                enabled: true,
                key_file: value.key_file || '',
            },
        };
    return { recipient_1: { label: '', enabled: true, key_file: '' } };
}
const fields = [
    'kind',
    'phase',
    'enabled',
    'is_global',
    'description',
    'token_file',
    'title',
    'body',
    'priority',
    'sound',
    'timeout_ms',
    'generate_summary',
    'summary_provider',
    'summary_prompt',
    'summary_max_words',
    'summary_timeout_ms',
    'summary_failure_policy',
    'destination_key',
    'suppress_after_tool',
];
export function pushoverDocument(value: PushoverDraft) {
    return {
        ...Object.fromEntries(
            fields
                .filter(key => value[key] !== undefined && value[key] !== '')
                .map(key => [key, value[key]])
        ),
        recipients: pushoverRecipients(value),
    };
}
export function credentialPayload(value: PushoverCredentials, ids?: string[]) {
    const recipients = Object.fromEntries(
        Object.entries(value.recipients).filter(([id, key]) => key && (!ids || ids.includes(id)))
    );
    return {
        ...(value.token ? { token: value.token } : {}),
        ...(Object.keys(recipients).length ? { recipients } : {}),
    };
}
export function applyPushoverRefs(
    value: PushoverDraft,
    refs: {
        token_file?: string;
        key_file?: string;
        recipients?: Record<string, { key_file: string }>;
    }
): PushoverDraft {
    const recipients = { ...pushoverRecipients(value) };
    for (const [id, ref] of Object.entries(refs.recipients || {}))
        if (recipients[id]) recipients[id] = { ...recipients[id], ...ref };
    if (refs.key_file && Object.keys(recipients).length === 1) {
        const id = Object.keys(recipients)[0];
        recipients[id] = { ...recipients[id], key_file: refs.key_file };
    }
    return { ...value, ...(refs.token_file ? { token_file: refs.token_file } : {}), recipients };
}
