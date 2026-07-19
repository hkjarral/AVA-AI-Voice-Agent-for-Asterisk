// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { VicidialRemoteAgentsTab } from './VicidialRemoteAgentsTab';

vi.mock('axios');

const connection = {
    id: 'connection-1',
    name: 'VICIdial Lab',
    enabled: true,
    base_url: 'http://192.168.10.100',
    vicidial_host: '192.168.10.100',
    topology: 'lan_vpn',
    username_env: 'VICIDIAL_API_USER',
    password_env: 'VICIDIAL_API_PASS',
    timezone: 'America/Phoenix',
};

const mapping = {
    id: 'mapping-1',
    connection_id: connection.id,
    name: 'AVA Lab Remote Agent',
    enabled: true,
    direction: 'both',
    campaign_id: 'AVATEST',
    closer_campaigns: ['AVAIN'],
    user_start: '9001',
    number_of_lines: 2,
    conf_exten: '8371',
    static_agent_user: '',
    ai_agent: 'demo_deepgram',
    trusted_context: 'from-vicidial-ra',
    trusted_endpoint: 'vicidial-ra',
    dispositions: { sale: 'SALE' },
    statuses: { ai_hangup: 'AIHU', dnc: 'DNC', callback: 'CALLBK' },
    destinations: {
        sales: { type: 'ingroup', target: 'SALESLINE', description: 'Sales team' },
    },
    dnc_scope: 'campaign',
    callback_type: 'ANYONE',
    agent_available: true,
};

describe('VicidialRemoteAgentsTab tooltips', () => {
    beforeEach(() => {
        vi.mocked(axios.get).mockImplementation(async url => {
            if (url === '/api/outbound/vicidial/connections') return { data: [connection] };
            if (url === '/api/outbound/vicidial/mappings') return { data: [mapping] };
            if (url === '/api/outbound/vicidial/activity') {
                return {
                    data: {
                        summary: {
                            handled: 3,
                            finalized: 2,
                            needs_attention: 1,
                            average_duration_seconds: 42,
                            last_call_at: '2026-07-19T18:30:00+00:00',
                        },
                        dispositions: [
                            { status: 'AIHU', count: 1 },
                            { status: 'DNC', count: 1 },
                        ],
                        by_mapping: [
                            {
                                mapping_id: mapping.id,
                                mapping_name: mapping.name,
                                handled: 3,
                                finalized: 2,
                                needs_attention: 1,
                                last_call_at: '2026-07-19T18:30:00+00:00',
                            },
                        ],
                        recent_calls: [
                            {
                                id: 'record-1',
                                started_at: '2026-07-19T18:30:00+00:00',
                                direction: 'outbound',
                                masked_number: '•••9284',
                                remote_agent: '9001',
                                ai_agent: 'demo_deepgram',
                                duration_seconds: 42,
                                outcome: 'completed',
                                disposition: 'AIHU',
                                disposition_confirmed: true,
                                finalized: true,
                                needs_attention: false,
                                mapping_id: mapping.id,
                            },
                        ],
                        scope_note: 'Only calls delivered to AAVA are counted.',
                    },
                };
            }
            if (url === '/api/outbound/meta') {
                return { data: { agents: [{ slug: 'demo_deepgram', display_name: 'Deepgram' }] } };
            }
            if (url === '/api/outbound/vicidial/mappings/mapping-1/guidance') {
                return {
                    data: {
                        vicidial_steps: ['Create every VICIdial user.'],
                        dialplan: '[from-vicidial-ra]',
                        freepbx_trunk: { name: 'vicidial-ra', secret: 'Use conf_secret' },
                        network: { notes: ['Use a routed private LAN.'] },
                        verification_order: ['Verify APIs'],
                    },
                };
            }
            throw new Error(`Unexpected GET ${url}`);
        });
    });

    it('provides help for every connection field', async () => {
        render(<VicidialRemoteAgentsTab />);
        await screen.findByText('VICIdial Lab');

        fireEvent.click(screen.getByRole('button', { name: 'Edit connection' }));

        for (const label of [
            'Connection enabled',
            'Connection name',
            'VICIdial base URL',
            'VICIdial SIP host',
            'SIP port',
            'Network topology',
            'VICIdial timezone',
            'RTP start port',
            'RTP end port',
            'API username environment variable',
            'API password environment variable',
            'Source label',
            'Timeout (ms)',
            'Verify TLS certificates',
        ]) {
            expect(screen.getByRole('button', { name: `Help for ${label}` })).toBeInTheDocument();
        }
    });

    it('provides help for mapping, lifecycle, and transfer options', async () => {
        render(<VicidialRemoteAgentsTab />);
        await screen.findByText('VICIdial Lab');

        fireEvent.click(screen.getByRole('button', { name: 'Edit mapping' }));

        for (const label of [
            'Mapping enabled',
            'VICIdial connection',
            'Mapping name',
            'Call direction',
            'AAVA Agent',
            'VICIdial campaign ID',
            'Closer campaigns',
            'Starting Remote Agent user',
            'Number of lines',
            'Remote Agent extension',
            'One-line fallback user',
            'Trusted AAVA dialplan context',
            'Trusted endpoint (optional)',
            'DNC scope',
            'Callback ownership',
        ]) {
            expect(screen.getByRole('button', { name: `Help for ${label}` })).toBeInTheDocument();
        }

        expect(
            screen.getByRole('button', { name: 'Help for allowed dispositions' })
        ).toBeInTheDocument();
        const lifecycleHelp = screen.getByRole('button', { name: 'Help for lifecycle statuses' });
        expect(lifecycleHelp).toBeInTheDocument();
        expect(
            screen.getByRole('button', { name: 'Help for cold transfer destinations' })
        ).toBeInTheDocument();
        expect(
            screen.getByRole('button', { name: 'Help for destination target' })
        ).toBeInTheDocument();

        fireEvent.click(lifecycleHelp);
        expect(await screen.findByRole('tooltip')).toHaveTextContent(
            'Statuses used automatically for hangup, transfer, failure, DNC, and callback outcomes.'
        );
    });

    it('provides contextual help throughout the generated setup guide', async () => {
        render(<VicidialRemoteAgentsTab />);
        await screen.findByText('VICIdial Lab');

        fireEvent.click(screen.getByRole('button', { name: 'Setup guide' }));
        await waitFor(() =>
            expect(screen.getByText('Create every VICIdial user.')).toBeInTheDocument()
        );

        expect(
            screen.getByRole('button', { name: 'Help for VICIdial setup steps' })
        ).toBeInTheDocument();
        expect(
            screen.getByRole('button', { name: 'Help for AAVA trusted dialplan' })
        ).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Help for FreePBX trunk' })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Help for trunk secret' })).toBeInTheDocument();
        expect(
            screen.getByRole('button', { name: 'Help for network and NAT' })
        ).toBeInTheDocument();
        expect(
            screen.getByRole('button', { name: 'Help for verification order' })
        ).toBeInTheDocument();
    });

    it('shows scoped Remote Agent metrics and deep-links recent calls', async () => {
        render(<VicidialRemoteAgentsTab />);

        expect(await screen.findByText('Remote Agent activity')).toBeInTheDocument();
        expect(screen.getByText('Only calls delivered to AAVA are counted.')).toBeInTheDocument();
        expect(screen.getByText('Handled by AAVA')).toBeInTheDocument();
        expect(screen.getByText('Finalized in VICIdial')).toBeInTheDocument();
        expect(screen.getByText('Needs attention')).toBeInTheDocument();
        expect(screen.getByText('•••9284')).toBeInTheDocument();
        expect(screen.getByText('AIHU')).toBeInTheDocument();
        expect(screen.getByText(/AIHU · 1/)).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Details' })).toHaveAttribute(
            'href',
            '/history?id=record-1'
        );

        fireEvent.change(screen.getByLabelText('Activity range'), { target: { value: '30d' } });
        await waitFor(() =>
            expect(axios.get).toHaveBeenCalledWith('/api/outbound/vicidial/activity', {
                params: { range: '30d', mapping_id: undefined, limit: 10 },
            })
        );
    });
});
