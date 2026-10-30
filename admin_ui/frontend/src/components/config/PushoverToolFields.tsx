import { useState } from 'react';
import axios from 'axios';
import { HTTPTemplateEditor, type TemplateReview } from './HTTPTemplateGuidance';
import { FormInput, FormSelect, FormLabel, FormSwitch } from '../ui/FormComponents';

import {
    pushoverDocument,
    pushoverDefaults,
    pushoverRecipients,
    applyPushoverRefs,
    credentialPayload,
    type PushoverDraft,
    type PushoverCredentials,
    type PushoverRecipient,
} from './pushoverToolConfig';

export function PushoverToolFields({
    value,
    onChange,
    credentials,
    onCredentialsChange,
    templateReview,
}: {
    templateReview?: TemplateReview | null;
    value: PushoverDraft;
    onChange: (value: PushoverDraft) => void;
    credentials: PushoverCredentials;
    onCredentialsChange: (value: PushoverCredentials) => void;
}) {
    const [status, setStatus] = useState('');
    const [busy, setBusy] = useState(false);
    const change = (key: string, next: unknown) => {
        onChange({ ...value, [key]: next });
    };
    const recipients = pushoverRecipients(value);
    const defaults = pushoverDefaults(value.phase, Boolean(value.generate_summary));
    const updateRecipient = (id: string, patch: Partial<PushoverRecipient>) =>
        change('recipients', { ...recipients, [id]: { ...recipients[id], ...patch } });
    const addRecipient = () => {
        const id = Array.from({ length: 16 }, (_, index) => `recipient_${index + 1}`).find(
            key => !recipients[key]
        );
        if (id)
            change('recipients', {
                ...recipients,
                [id]: { label: '', enabled: true, key_file: '' },
            });
    };
    const removeRecipient = (id: string) => {
        change(
            'recipients',
            Object.fromEntries(Object.entries(recipients).filter(([key]) => key !== id))
        );
        onCredentialsChange({
            ...credentials,
            recipients: Object.fromEntries(
                Object.entries(credentials.recipients).filter(([key]) => key !== id)
            ),
        });
    };
    const action = async (kind: 'validate' | 'test', id?: string) => {
        setBusy(true);
        setStatus('');
        try {
            const response = await axios.post(`/api/pushover/${kind}`, {
                config: pushoverDocument(value),
                credentials: credentialPayload(credentials, id ? [id] : undefined),
                recipient_id: id,
            });
            onChange(applyPushoverRefs(value, response.data.credential_refs || {}));
            onCredentialsChange({
                token: '',
                recipients: Object.fromEntries(
                    Object.entries(credentials.recipients).filter(([key]) => id && key !== id)
                ),
            });
            const details = Object.entries(response.data.notification_deliveries || {})
                .map(([key, result]) => {
                    const row = result as {
                        label?: string;
                        delivery_status: string;
                        error_code?: string;
                    };
                    return `${row.label || key}: ${row.delivery_status}${row.error_code ? ` (${row.error_code})` : ''}`;
                })
                .join('; ');
            setStatus(
                `${kind === 'test' ? 'Test' : 'Validation'}: ${details || response.data.delivery_status}. Acceptance does not prove display or acknowledgement.`
            );
        } catch {
            setStatus(
                'Unable to complete this action. Check credential format and configuration. No automatic retry will be made.'
            );
        } finally {
            setBusy(false);
        }
    };
    return (
        <fieldset disabled={busy} className="min-w-0 space-y-3">
            {value.phase === 'in_call' && (
                <div>
                    <FormLabel
                        htmlFor="pushover-description"
                        tooltip="The AI sees this description. Explain when to invoke this named tool; the agent prompt can add instructions."
                    >
                        Tool description / when to use
                    </FormLabel>
                    <textarea
                        id="pushover-description"
                        className="w-full rounded border border-input bg-background p-3"
                        value={value.description || ''}
                        onChange={e => change('description', e.target.value)}
                        placeholder="Notify Sales before transferring a caller to Sales."
                    />
                </div>
            )}
            <FormInput
                label="Application token"
                type="password"
                autoComplete="new-password"
                value={credentials.token}
                onChange={e => onCredentialsChange({ ...credentials, token: e.target.value })}
                placeholder={
                    value.token_file ? 'Saved — blank keeps it' : 'Enter application token'
                }
                tooltip="One application token is shared by this tool's recipients. Stored as an owner-only secret file; never returned as plaintext."
            />
            {value.token_file && (
                <button type="button" className="text-xs" onClick={() => change('token_file', '')}>
                    Clear saved application token
                </button>
            )}
            <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h4 className="font-medium">Recipients / groups</h4>
                    <button
                        type="button"
                        disabled={Object.keys(recipients).length >= 16}
                        onClick={addRecipient}
                        className="rounded border px-3 py-2 text-sm"
                    >
                        Add recipient / group
                    </button>
                </div>
                <p className="text-xs text-muted-foreground">
                    Add up to 16 user or group keys. The summary is generated once and shared by
                    enabled recipients. A group delivers to its active members in Pushover.
                </p>
                {Object.entries(recipients).map(([id, entry]) => (
                    <div key={id} className="rounded border p-3 min-w-0">
                        <div className="grid gap-3 md:grid-cols-2">
                            <FormInput
                                label={`Recipient name (${id})`}
                                value={entry.label}
                                onChange={e => updateRecipient(id, { label: e.target.value })}
                                placeholder="e.g., Sales team"
                            />
                            <FormInput
                                label={`User/group key (${id})`}
                                type="password"
                                autoComplete="new-password"
                                value={credentials.recipients[id] || ''}
                                onChange={e =>
                                    onCredentialsChange({
                                        ...credentials,
                                        recipients: {
                                            ...credentials.recipients,
                                            [id]: e.target.value,
                                        },
                                    })
                                }
                                placeholder={
                                    entry.key_file
                                        ? 'Saved — blank keeps it'
                                        : 'Enter user/group key'
                                }
                            />
                        </div>
                        <FormSwitch
                            label={`Recipient enabled (${id})`}
                            checked={entry.enabled}
                            onChange={e => updateRecipient(id, { enabled: e.target.checked })}
                            description="Disabled recipients are retained but never sent notifications."
                        />
                        <div className="flex flex-wrap gap-3 text-xs">
                            {entry.key_file && (
                                <button
                                    type="button"
                                    onClick={() => updateRecipient(id, { key_file: '' })}
                                >
                                    Clear saved key ({id})
                                </button>
                            )}
                            <button
                                type="button"
                                disabled={!entry.enabled}
                                onClick={() => void action('validate', id)}
                            >
                                Validate {entry.label || id}
                            </button>
                            <button
                                type="button"
                                disabled={!entry.enabled}
                                onClick={() => void action('test', id)}
                            >
                                Send test to {entry.label || id}
                            </button>
                            <button type="button" onClick={() => removeRecipient(id)}>
                                Remove {entry.label || id}
                            </button>
                        </div>
                    </div>
                ))}
                {!Object.keys(recipients).length && (
                    <p className="text-sm text-muted-foreground">
                        No recipients. Add a user or group to enable delivery.
                    </p>
                )}
            </div>
            <div>
                <FormLabel htmlFor="pushover-title">Notification title</FormLabel>
                <HTTPTemplateEditor id="pushover-title" rows={1} value={value.title || ''}
                    onChange={e => change('title', e.target.value)} placeholder={defaults.title}
                    references={templateReview?.references.filter(reference => reference.field === 'title')} />
            </div>
            <div>
                <FormLabel
                    htmlFor="pushover-body"
                    tooltip="Plain-text templates insert trusted call values once. The AI cannot choose credentials, recipients or the endpoint."
                >
                    Message template
                </FormLabel>
                <HTTPTemplateEditor
                    references={templateReview?.references.filter(reference => reference.field === 'body')}
                    id="pushover-body"
                    className="w-full min-h-32 rounded border border-input bg-background p-3 font-mono text-sm"
                    value={value.body || ''}
                    onChange={e => change('body', e.target.value)}
                    placeholder={defaults.body}
                />
                <p className="text-xs text-muted-foreground">
                    Leave blank to use the phase default; a summary is included only when enabled.
                    Optional custom variables:{' '}
                    {
                        '{agent}, {name}, {company}, {crm_status}, {caller_number}, {callback_number}, {called_number}, {destination}, {call_id}, {outcome}, {language}, {meeting}, {summary}, {summary_status}'
                    }
                    . Preview uses synthetic data only.
                </p>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
                <FormSelect
                    label="Priority"
                    value={String(value.priority ?? 0)}
                    options={[
                        { value: '-2', label: 'Lowest' },
                        { value: '-1', label: 'Low' },
                        { value: '0', label: 'Normal' },
                        { value: '1', label: 'High' },
                    ]}
                    onChange={e => change('priority', Number(e.target.value))}
                />
                <FormInput
                    label="Sound"
                    value={value.sound || ''}
                    onChange={e => change('sound', e.target.value)}
                    placeholder="Default"
                />
                <FormInput
                    label="Delivery timeout (ms)"
                    type="number"
                    min={100}
                    max={10000}
                    value={value.timeout_ms || 5000}
                    onChange={e => change('timeout_ms', Number(e.target.value))}
                />
            </div>
            <details>
                <summary className="cursor-pointer text-sm">
                    Advanced: transfer completion suppression
                </summary>
                <p className="text-xs text-muted-foreground my-3">
                    Optional. An in-call tool can identify its intended transfer destination. A
                    post-call tool may suppress only after that named tool was accepted for the same
                    credentials and the matching next transfer actually succeeded. Failed/cancelled
                    transfers retain completion.
                </p>
                {value.phase === 'in_call' && (
                    <FormInput
                        label="Intended transfer destination key"
                        value={value.destination_key || ''}
                        onChange={e => change('destination_key', e.target.value)}
                    />
                )}
                {value.phase === 'post_call' && (
                    <FormInput
                        label="Suppress after accepted in-call tool"
                        value={value.suppress_after_tool || ''}
                        onChange={e => change('suppress_after_tool', e.target.value)}
                        placeholder="e.g., notify_sales"
                    />
                )}
            </details>
            {status && (
                <p role="status" className="text-sm">
                    {status}
                </p>
            )}
            <p className="text-xs text-muted-foreground">
                Update tool, then Save &amp; Apply on the Tools page. Agent access uses the existing
                tool selectors. Pre/post-call tools run automatically when selected; in-call tools
                are invoked by the AI. Best-effort delivery has no restart replay.
            </p>
        </fieldset>
    );
}
