import { Calendar, FileText, Mail, PhoneForwarded, PhoneOff, Voicemail, Settings } from 'lucide-react';

export const toolSections = [
    { key: 'transfer', title: 'Transfer & live agents', description: 'Destinations, announcements and handoff', group: 'Call handling', icon: PhoneForwarded },
    { key: 'hangup_call', title: 'Hangup', description: 'Farewell and provider failure behavior', group: 'Call handling', icon: PhoneOff },
    { key: 'leave_voicemail', title: 'Voicemail', description: 'Mailboxes and default destination', group: 'Call handling', icon: Voicemail },
    { key: 'send_email_summary', title: 'Email summary', description: 'Summary delivery and recipients', group: 'Business', icon: Mail },
    { key: 'request_transcript', title: 'Transcript requests', description: 'Caller delivery and email confirmation', group: 'Business', icon: FileText },
    { key: 'google_calendar', title: 'Google Calendar', description: 'Calendars and availability', group: 'Business', icon: Calendar },
    { key: 'microsoft_calendar', title: 'Microsoft Calendar', description: 'Account, availability and booking rules', group: 'Business', icon: Calendar },
    { key: 'calendly_calendar', title: 'Calendly adapter', description: 'Optional paid-provider adapter for existing Calendly users', group: 'Optional providers', icon: Calendar },
] as const;

export type ToolSection = typeof toolSections[number]['key'] | 'defaults';
export const toolDefaults = { key: 'defaults', title: 'Tool defaults', description: 'Shared identity and action defaults', icon: Settings } as const;
