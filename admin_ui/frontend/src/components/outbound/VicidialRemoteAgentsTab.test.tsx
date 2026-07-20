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
    last_verification: { ready: true },
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
    pbx_setup_mode: 'generated_registration',
    pbx_technology: 'PJSIP',
    pbx_trunk_name: 'VICIdial lab trunk',
    sip_username: '8371',
    sip_auth_username: '8371',
    sip_contact_user: '8371',
    sip_transport: 'udp',
    dispositions: { sale: 'SALE' },
    statuses: { ai_hangup: 'AIHU', dnc: 'DNC', callback: 'CALLBK' },
    destinations: {
        sales: { type: 'ingroup', target: 'SALESLINE', description: 'Sales team' },
    },
    dnc_scope: 'campaign',
    callback_type: 'ANYONE',
    agent_available: true,
    last_verification: {
        configuration_ready: true,
        pbx_ready: true,
        pbx_endpoint: { state: 'online', resource: 'vicidial-ra' },
        real_call: { verified: false, required_directions: ['inbound', 'outbound'] },
        real_calls: { outbound: { verified: true, verified_at: '2026-07-19T18:30:00+00:00' } },
    },
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
                            unconfirmed_errors: 1,
                            confirmed_failures: 0,
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
                                unconfirmed_errors: 1,
                                confirmed_failures: 0,
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
                                unconfirmed_error: false,
                                confirmed_failure: false,
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
                        artifact_inputs: {
                            setup_mode: 'generated_registration',
                            technology: 'PJSIP',
                            remote_agent_extension: '8371',
                            trunk_name: 'VICIdial lab trunk',
                            endpoint_id: 'vicidial-ra',
                            username: '8371',
                            auth_username: '8371',
                            contact_user: '8371',
                        },
                        vicidial_steps: ['Create every VICIdial user.'],
                        dialplan: '[from-vicidial-ra]',
                        freepbx_trunk: {
                            setup_mode: 'generated_registration',
                            name: 'VICIdial lab trunk',
                            secret: '<VICIDIAL_PHONE_CONF_SECRET>',
                        },
                        dialplan_install: {
                            path: '/etc/asterisk/extensions_custom.conf',
                            freepbx_apply: 'Use FreePBX Apply Config or run fwconsole reload',
                            asterisk_apply:
                                "For vanilla Asterisk, run asterisk -rx 'dialplan reload'",
                            note: 'Do not edit FreePBX-generated dialplan files',
                        },
                        network: { notes: ['Use a routed private LAN.'] },
                        verification_order: ['Verify APIs'],
                    },
                };
            }
            if (url === '/api/outbound/vicidial/asterisk/endpoints') {
                return {
                    data: {
                        ari_connected: true,
                        probe_available: true,
                        endpoints: [
                            { resource: 'support-vicidial', state: 'online', channel_count: 0 },
                        ],
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

        fireEvent.click(
            screen.getByRole('button', {
                name: 'Help for API username environment variable',
            })
        );
        expect(await screen.findByRole('tooltip')).toHaveTextContent(
            'Admin → Environment → System → Outbound Campaign'
        );
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
            'PBX setup mode',
            'PBX technology',
            'PBX trunk name',
            'Asterisk endpoint ID',
            'Endpoint discovery',
            'SIP transport',
            'SIP username override',
            'SIP auth username override',
            'SIP contact user override',
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

    it('keeps focus while editing disposition and destination names', async () => {
        render(<VicidialRemoteAgentsTab />);
        await screen.findByText('VICIdial Lab');

        fireEvent.click(screen.getByRole('button', { name: 'Edit mapping' }));

        const dispositionName = screen.getByLabelText('Disposition name');
        dispositionName.focus();
        fireEvent.change(dispositionName, { target: { value: 'qualified_sale' } });
        expect(dispositionName).toHaveFocus();
        expect(dispositionName).toHaveValue('qualified_sale');
        fireEvent.blur(dispositionName);
        expect(screen.getByLabelText('Disposition name')).toHaveValue('qualified_sale');

        const destinationName = screen.getByLabelText('Destination name');
        destinationName.focus();
        fireEvent.change(destinationName, { target: { value: 'priority_sales' } });
        expect(destinationName).toHaveFocus();
        expect(destinationName).toHaveValue('priority_sales');
        fireEvent.blur(destinationName);
        expect(screen.getByLabelText('Destination name')).toHaveValue('priority_sales');
    });

    it('discovers an existing endpoint without replacing manual entry', async () => {
        render(<VicidialRemoteAgentsTab />);
        await screen.findByText('VICIdial Lab');

        fireEvent.click(screen.getByRole('button', { name: 'Edit mapping' }));
        fireEvent.click(screen.getByRole('button', { name: 'Detect PJSIP endpoints' }));

        const detected = await screen.findByLabelText('Detected Asterisk endpoint');
        fireEvent.change(detected, { target: { value: 'support-vicidial' } });

        expect(screen.getByLabelText('Asterisk endpoint ID')).toHaveValue('support-vicidial');
        expect(axios.get).toHaveBeenCalledWith('/api/outbound/vicidial/asterisk/endpoints', {
            params: { technology: 'PJSIP' },
        });
    });

    it('keeps chan_sip on the manual existing-endpoint path', async () => {
        render(<VicidialRemoteAgentsTab />);
        await screen.findByText('VICIdial Lab');

        fireEvent.click(screen.getByRole('button', { name: 'Edit mapping' }));
        fireEvent.change(screen.getByLabelText('PBX technology'), {
            target: { value: 'SIP' },
        });

        expect(screen.getByLabelText('PBX setup mode')).toHaveValue('existing_endpoint');
        expect(screen.queryByLabelText('SIP username override')).not.toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Detect SIP endpoints' })).toBeInTheDocument();
    });

    it('shows independent setup progress and the missing call direction', async () => {
        render(<VicidialRemoteAgentsTab />);

        expect(await screen.findByText('1/1 verified')).toBeInTheDocument();
        expect(screen.getByText('1/1 valid')).toBeInTheDocument();
        expect(screen.getByText('1/1 reachable')).toBeInTheDocument();
        expect(screen.getByText('0/1 verified')).toBeInTheDocument();
        expect(
            screen.getByText('Configuration valid — inbound call test required')
        ).toBeInTheDocument();
    });

    it('provides contextual help throughout the generated setup guide', async () => {
        render(<VicidialRemoteAgentsTab />);
        await screen.findByText('VICIdial Lab');

        fireEvent.click(screen.getByRole('button', { name: 'Setup guide' }));
        await waitFor(() =>
            expect(screen.getByText('Create every VICIdial user.')).toBeInTheDocument()
        );

        expect(screen.queryByText('[from-vicidial-ra]')).not.toBeInTheDocument();
        expect(screen.getByText('remote agent extension')).toBeInTheDocument();
        expect(screen.getAllByText('8371').length).toBeGreaterThan(0);
        fireEvent.click(screen.getByRole('button', { name: 'Generate dialplan and trunk guide' }));

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
        expect(screen.getByText('/etc/asterisk/extensions_custom.conf')).toBeInTheDocument();
    });

    it('keeps the generated SIP secret browser-only and clears it on close', async () => {
        render(<VicidialRemoteAgentsTab />);
        await screen.findByText('VICIdial Lab');

        fireEvent.click(screen.getByRole('button', { name: 'Setup guide' }));
        const secret = await screen.findByLabelText('Browser-only SIP secret');
        fireEvent.change(secret, { target: { value: 'BrowserOnly_Test-2026!' } });
        fireEvent.click(screen.getByRole('button', { name: 'Generate dialplan and trunk guide' }));

        expect(secret).toHaveValue('BrowserOnly_Test-2026!');
        expect(axios.post).not.toHaveBeenCalled();
        expect(axios.put).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole('button', { name: 'Close' }));
        fireEvent.click(screen.getByRole('button', { name: 'Setup guide' }));
        expect(await screen.findByLabelText('Browser-only SIP secret')).toHaveValue('');
    });

    it('starts a first-time mapping with environment-specific PBX fields blank', async () => {
        render(<VicidialRemoteAgentsTab />);
        await screen.findByText('VICIdial Lab');

        fireEvent.click(screen.getByRole('button', { name: 'Add mapping' }));

        expect(screen.getByLabelText('Remote Agent extension')).toHaveValue('');
        expect(screen.getByLabelText('PBX trunk name')).toHaveValue('');
        expect(screen.getByLabelText('Asterisk endpoint ID')).toHaveValue('');
        expect(
            screen.getAllByLabelText('Disposition name').map(input => input.getAttribute('value'))
        ).toEqual(['sale', 'not_interested', 'dnc', 'callback']);
        expect(screen.getByLabelText('dnc VICIdial status')).toHaveValue('DNC');
        expect(screen.getByLabelText('callback VICIdial status')).toHaveValue('CALLBK');
    });

    it('shows scoped Remote Agent metrics and deep-links recent calls', async () => {
        render(<VicidialRemoteAgentsTab />);

        expect(await screen.findByText('Remote Agent activity')).toBeInTheDocument();
        expect(screen.getByText('Only calls delivered to AAVA are counted.')).toBeInTheDocument();
        expect(screen.getByText('Handled by AAVA')).toBeInTheDocument();
        expect(screen.getByText('Finalized in VICIdial')).toBeInTheDocument();
        expect(screen.getByText('Unconfirmed / errors')).toBeInTheDocument();
        expect(screen.getByText('Confirmed failures')).toBeInTheDocument();
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
