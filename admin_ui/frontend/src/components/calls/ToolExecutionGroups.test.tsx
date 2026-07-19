// @vitest-environment jsdom
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { PhaseToolGroup } from './ToolExecutionGroups';

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
