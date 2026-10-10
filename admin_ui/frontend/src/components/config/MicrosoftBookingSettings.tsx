import { useState } from 'react';
import { FormInput, FormSwitch } from '../ui/FormComponents';

export const MICROSOFT_INVITATION_SUBJECT = '{{meeting_purpose}}';
export const MICROSOFT_INVITATION_BODY = 'Hello {{caller_name}},\n\nYour appointment is scheduled for {{appointment_date}}, {{start_time}}–{{end_time}} ({{timezone_label}}).\n\nPurpose: {{meeting_purpose}}\n{{confirmed_notes}}\nLocation: {{location}}\n\n{{rescheduling_instructions}}\n{{business_name}}\n{{contact_details}}';

const callerPreview = {
    caller_name: 'Example Caller', meeting_purpose: 'Consultation', confirmed_notes: 'Discuss service options.',
    appointment_date: 'Monday, 2026-10-05', start_time: '13:00', end_time: '13:30', timezone_label: 'America/Phoenix (UTC-0700)',
};
const operatorFields = ['business_name', 'location', 'contact_details', 'rescheduling_instructions'] as const;
const placeholderNames = [...Object.keys(callerPreview), ...operatorFields];

export function previewMicrosoftTemplate(template: string, values: Record<string, string>) {
    return template.replace(/\{\{([^{}]*)\}\}/g, (_, key: string) =>
        placeholderNames.includes(key.trim()) ? values[key.trim()] || '' : `[Unsupported placeholder: ${key.trim()}]`);
}

interface Props {
    config: Record<string, any>;
    onChange: (patch: Record<string, any>) => void;
}

export function MicrosoftBookingSettings({ config, onChange }: Props) {
    const [emptyNumericFields, setEmptyNumericFields] = useState<Record<string, boolean>>({});
    const days: number[] = config.working_days ?? [0, 1, 2, 3, 4];
    const values = { ...callerPreview, ...Object.fromEntries(operatorFields.map(key => [key, config[key] || ''])) };
    return <div className="space-y-4 mt-4">
        <FormSwitch label="Enforce working hours and booking horizon" checked={config.enforce_booking_limits === true}
            onChange={event => onChange({ enforce_booking_limits: event.target.checked })}
            tooltip="Opt in to reject bookings outside these hours or beyond the horizon. Existing installations keep hours/horizon enforcement off until enabled; availability suggestions still use these hours." />
        <p className="text-xs text-muted-foreground">Working hours guide suggestions. Enable enforcement to apply hours and the booking horizon to exact availability, creation and rescheduling. Maximum duration always applies.</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {[
                ['working_hours_start', 'Working hours start (0–23)', 9, 0, 23],
                ['working_hours_end', 'Working hours end (1–24)', 17, 1, 24],
                ['max_event_duration_minutes', 'Maximum booking duration (minutes)', 240, 0, undefined],
                ['booking_horizon_days', 'Booking horizon (days)', 365, 0, undefined],
            ].map(([key, label, fallback, min, max]) => <FormInput key={String(key)} label={String(label)} type="number"
                min={min as number} max={max as number | undefined} step={1}
                value={emptyNumericFields[String(key)] ? '' : String(config[String(key)] ?? fallback)}
                onChange={event => {
                    const empty = event.target.value === '';
                    setEmptyNumericFields(previous => ({ ...previous, [String(key)]: empty }));
                    if (!empty) onChange({ [String(key)]: Number(event.target.value) });
                }}
                onBlur={event => {
                    if (event.target.value === '') {
                        setEmptyNumericFields(previous => ({ ...previous, [String(key)]: false }));
                        onChange({ [String(key)]: undefined });
                    }
                }}
                tooltip="Hours use the configured calendar timezone. Zero disables the duration/horizon limit." />)}
        </div>
        <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Working days</legend>
            <div className="flex flex-wrap gap-3">
                {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((day, index) =>
                    <label key={day} className="text-sm flex items-center gap-1">
                        <input type="checkbox" aria-label={`Microsoft working day ${day}`} checked={days.includes(index)}
                            onChange={event => onChange({ working_days: event.target.checked ? [...days, index].sort() : days.filter(value => value !== index) })} />
                        {day}
                    </label>)}
            </div>
        </fieldset>
        <FormSwitch label="Allow caller invitations" checked={config.invitations_enabled === true}
            onChange={event => onChange({ invitations_enabled: event.target.checked })}
            description="When enabled, the agent must confirm the booking, every attendee email and permission to send invitations. Without attendees, bookings remain appointments. Teams links and automatic staff attendees are not enabled." />
        <p className="text-xs text-muted-foreground">Cancellation and rescheduling apply only to bookings created during the current call. Requests about earlier bookings require staff. Availability suggestions are a subset; the agent checks the requested interval first.</p>
        {config.invitations_enabled === true && <div className="space-y-3">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {operatorFields.map(key => <FormInput key={key} label={{ business_name: 'Business name', location: 'Appointment location', contact_details: 'Contact details', rescheduling_instructions: 'Cancellation / rescheduling instructions' }[key]}
                    value={config[key] || ''} onChange={event => onChange({ [key]: event.target.value })} />)}
            </div>
            <FormInput label="Invitation subject template" value={config.invitation_subject_template ?? MICROSOFT_INVITATION_SUBJECT}
                onChange={event => onChange({ invitation_subject_template: event.target.value })} />
            <label className="block text-sm font-medium" htmlFor="microsoft-invitation-body">Invitation body template (plain text)</label>
            <textarea id="microsoft-invitation-body" rows={8} className="w-full border rounded p-2 bg-background text-sm"
                value={config.invitation_body_template ?? MICROSOFT_INVITATION_BODY}
                onChange={event => onChange({ invitation_body_template: event.target.value })} />
            <p className="text-xs text-muted-foreground">Placeholders: {placeholderNames.map(key => `{{${key}}}`).join(', ')}. Caller name, purpose and notes must be confirmed. Only supported placeholders are rendered; no code or HTML is executed.</p>
            <div aria-label="Invitation preview" className="rounded border p-3 text-sm">
                <p className="font-medium">Preview with synthetic Phoenix booking details</p>
                <p>{previewMicrosoftTemplate(config.invitation_subject_template ?? MICROSOFT_INVITATION_SUBJECT, values)}</p>
                <pre className="whitespace-pre-wrap font-sans mt-2">{previewMicrosoftTemplate(config.invitation_body_template ?? MICROSOFT_INVITATION_BODY, values)}</pre>
            </div>
            <p className="text-xs text-muted-foreground">Microsoft generates invitation messages when attendees are included. Booking success does not verify recipient delivery or acceptance.</p>
        </div>}
    </div>;
}
