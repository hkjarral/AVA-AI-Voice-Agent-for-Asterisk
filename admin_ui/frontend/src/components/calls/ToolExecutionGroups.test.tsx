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
});

describe('InCallToolGroup', () => {
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
