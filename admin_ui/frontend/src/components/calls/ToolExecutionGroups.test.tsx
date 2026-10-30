// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { InCallToolGroup, PhaseToolGroup } from './ToolExecutionGroups';

describe('PhaseToolGroup', () => {
    it('renders HTTP diagnostics and extracted output variables', () => {
        render(
            <PhaseToolGroup
                phase="pre_call"
                entries={[{
                    name: 'generic_http_lookup',
                    kind: 'GenericHTTPLookupTool',
                    phase: 'pre_call',
                    status: 'ok',
                    duration_ms: 815.26,
                    http_status: 200,
                    response_summary: '{"carrier":"Verizon Wireless"}',
                    output_variables: { carrier: 'Verizon Wireless' },
                }]}
            />,
        );

        expect(screen.getByText('Pre-call (1)')).toBeInTheDocument();
        expect(screen.getByText('generic_http_lookup')).toBeInTheDocument();
        expect(screen.getByText('HTTP 200')).toBeInTheDocument();
        expect(screen.getByText('Output variables')).toBeInTheDocument();
        expect(screen.getByText('carrier')).toBeInTheDocument();
        expect(screen.getByText('Verizon Wireless')).toBeInTheDocument();
        expect(screen.getByText('{"carrier":"Verizon Wireless"}')).toBeInTheDocument();
    });

    it('renders non-2xx post-call details as an error', () => {
        render(
            <PhaseToolGroup
                phase="post_call"
                entries={[{
                    name: 'aava_sms_summary',
                    phase: 'post_call',
                    status: 'error',
                    http_status: 502,
                    error_message: 'HTTP 502',
                }]}
            />,
        );

        expect(screen.getByText('Post-call (1)')).toBeInTheDocument();
        expect(screen.getAllByText('HTTP 502')).toHaveLength(2);
        expect(screen.getByText('error')).toBeInTheDocument();
    });

    it('renders post-call summary provider diagnostics', () => {
        render(
            <PhaseToolGroup
                phase="post_call"
                entries={[{
                    name: 'crm_webhook',
                    phase: 'post_call',
                    status: 'ok',
                    http_status: 204,
                    summary_provider: 'deepseek_llm',
                    summary_model: 'deepseek-chat',
                    summary_status: 'timeout',
                    summary_duration_ms: 15001,
                    summary_error_code: 'timeout',
                }]}
            />,
        );

        expect(screen.getByText('AI summary')).toBeInTheDocument();
        expect(screen.getByText('Provider: deepseek_llm')).toBeInTheDocument();
        expect(screen.getByText('Model: deepseek-chat')).toBeInTheDocument();
        expect(screen.getByText('Status: timeout')).toBeInTheDocument();
        expect(screen.getByText('Error: timeout')).toBeInTheDocument();
    });
});

describe('InCallToolGroup', () => {
    it('shows the Calendly attempt reference next to the recorded outcome', () => {
        render(<InCallToolGroup entries={[{
            name: 'calendly_calendar', action: 'create_booking', status: 'pending',
            reservation_status: 'uncertain', attempt_reference: 'request-ref-123', duration_ms: 20,
        }]} />);
        expect(screen.getByText('request-ref-123')).toBeInTheDocument();
        expect(screen.getByText(/Recorded outcome: uncertain/)).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Review Calendly requests' })).toHaveAttribute('href', '/tools?section=calendly_calendar');
    });
    it('renders the enriched contract and treats normalized status as authoritative', () => {
        render(
            <InCallToolGroup
                entries={[{
                    type: 'tool_result',
                    call_id: 'call-1',
                    tool_call_id: 'tool-1',
                    name: 'google_calendar',
                    action: 'create_event',
                    status: 'failure',
                    target_id: 'event-42',
                    params: { summary: 'Consultation' },
                    result: 'success',
                    message: 'Provider rejected the operation',
                    timestamp: '2026-07-29T04:00:00+00:00',
                    duration_ms: 12.4,
                }]}
            />,
        );

        expect(screen.getByText('In-call (1)')).toBeInTheDocument();
        expect(screen.getByText('create_event')).toBeInTheDocument();
        expect(screen.getByText('event-42')).toBeInTheDocument();
        expect(screen.getByText('tool-1')).toBeInTheDocument();
        expect(screen.queryByText('call-1')).not.toBeInTheDocument();
        expect(screen.getByText('error')).toBeInTheDocument();
    });

    it('falls back to the call id when no tool call id is available', () => {
        render(
            <InCallToolGroup
                entries={[{
                    type: 'tool_result',
                    call_id: 'call-fallback-1',
                    name: 'hangup_call',
                    params: {},
                    result: 'success',
                    timestamp: '2026-07-29T04:00:00+00:00',
                    duration_ms: 4,
                }]}
            />,
        );

        expect(screen.getByText('call-fallback-1')).toBeInTheDocument();
    });

    it('shows the recorded policy and irrecoverable sanitized paths', () => {
        render(
            <InCallToolGroup
                entries={[{
                    type: 'tool_result',
                    call_id: 'call-redacted',
                    name: 'blind_transfer',
                    params: { destination: 'REDACTED' },
                    target_id: 'REDACTED',
                    result: 'success',
                    redaction_mode: 'strict',
                    redacted_fields: ['params.destination', 'target_id'],
                    timestamp: '2026-07-29T04:00:00+00:00',
                    duration_ms: 8,
                }]}
            />,
        );

        expect(screen.getByText('Strict redaction')).toBeInTheDocument();
        expect(screen.getByText(/params\.destination, target_id/)).toBeInTheDocument();
        expect(screen.getByText(/Original values cannot be recovered/)).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Configure future calls' })).toHaveAttribute(
            'href',
            '/env?section=call-history#system'
        );
    });

    it('shows a mixed policy for strict and metadata-free legacy entries', () => {
        render(
            <InCallToolGroup
                entries={[
                    {
                        type: 'tool_result',
                        call_id: 'call-mixed',
                        tool_call_id: 'tool-strict',
                        name: 'blind_transfer',
                        params: { destination: '***REDACTED***' },
                        result: 'success',
                        redaction_mode: 'strict',
                        timestamp: '2026-07-29T04:00:00+00:00',
                        duration_ms: 8,
                    },
                    {
                        type: 'tool_result',
                        call_id: 'call-mixed',
                        tool_call_id: 'tool-legacy',
                        name: 'hangup_call',
                        params: {},
                        result: 'success',
                        timestamp: '2026-07-29T04:00:01+00:00',
                        duration_ms: 3,
                    },
                ]}
            />,
        );

        expect(screen.getByText('Mixed redaction policies')).toBeInTheDocument();
        expect(screen.queryByText('Strict redaction')).not.toBeInTheDocument();
    });
});

it('shows notification delivery independently from summary generation', () => {
    render(<InCallToolGroup entries={[{name:'pushover_briefing',params:{update_key:'handoff'},result:'success',timestamp:'2026-10-23T12:00:00Z',duration_ms:50,summary_status:'timeout',summary_provider:'mock_llm',delivery_status:'accepted',notification_service:'pushover',notification_deliveries:{staff:{delivery_status:'accepted'}}}]} />);
    expect(screen.getByText('Status: timeout')).toBeInTheDocument();
    expect(screen.getByText('Delivery: accepted')).toBeInTheDocument();
    expect(screen.getByText('staff: accepted')).toBeInTheDocument();
    expect(screen.getByText(/not displayed or read/)).toBeInTheDocument();
});

it('does not attach Pushover acknowledgement semantics to calendar delivery', () => {
    render(<InCallToolGroup entries={[{name:'microsoft_calendar',params:{},result:'success',timestamp:'2026-10-23T12:00:00Z',duration_ms:5,delivery_status:'unknown'}]} />);
    expect(screen.queryByText(/queued by Pushover/)).not.toBeInTheDocument();
});

it('distinguishes transfer initiation from its terminal failure', () => {
    render(<InCallToolGroup entries={[
        {name:'attended_transfer',action:'attended_transfer_initiated',params:{},result:'success',timestamp:'2026-10-30T12:00:00Z',duration_ms:2},
        {name:'attended_transfer',action:'attended_transfer_failed',params:{},result:'failed',timestamp:'2026-10-30T12:00:01Z',duration_ms:0},
    ]} />);
    expect(screen.getByText('Transfer initiated')).toBeInTheDocument();
    expect(screen.getByText('Transfer failed')).toBeInTheDocument();
});
