// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

import CallHistoryPage from './CallHistoryPage';

vi.mock('axios');
vi.mock('../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn().mockResolvedValue(false) }),
}));

const callDetail = {
    id: 'record-1',
    call_id: 'asterisk-1',
    caller_number: '13164619284',
    caller_name: 'Alice',
    called_number: null,
    start_time: '2026-07-19T21:16:10+00:00',
    end_time: '2026-07-19T21:17:29+00:00',
    duration_seconds: 79,
    provider_name: 'deepgram',
    pipeline_name: null,
    pipeline_components: {},
    context_name: 'demo_deepgram',
    routing_method: 'ai_agent',
    voice: null,
    voice_source: null,
    outcome: 'completed',
    error_message: null,
    avg_turn_latency_ms: 600,
    max_turn_latency_ms: 750,
    total_turns: 2,
    barge_in_count: 0,
    caller_audio_format: 'ulaw',
    codec_alignment_ok: true,
    conversation_history: [],
    transfer_destination: null,
    tool_calls: [],
    pre_call_tool_calls: [],
    post_call_tool_calls: [],
    external_platform: 'vicidial',
    external_call_id: 'V7191416030000000039',
    external_direction: 'outbound',
    external_disposition: 'AIHU',
    external_metadata: {
        mapping_name: 'AVA Lab Remote Agent',
        finalized: true,
        session: { agent_user: '9001' },
        events: [],
    },
    call_metadata: { customer_tier: 'gold' },
    call_metadata_updates: [
        {
            field: 'customer_tier',
            source: 'agent_correction',
            updated_at: '2026-07-19T21:16:45+00:00',
        },
    ],
};

const LocationProbe = () => {
    const location = useLocation();
    return <div data-testid="location-search">{location.search}</div>;
};

const FullLocationProbe = () => {
    const location = useLocation();
    return <div data-testid="full-location">{`${location.pathname}${location.search}${location.hash}`}</div>;
};

describe('CallHistoryPage deep links', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.mocked(axios.get).mockImplementation(async url => {
            if (url === '/api/calls') {
                return { data: { calls: [callDetail], total: 51, total_pages: 2 } };
            }
            if (url === '/api/calls/stats') return { data: null };
            if (url === '/api/calls/redaction-policy') {
                return {
                    data: {
                        configured_mode: 'show_routing',
                        configured_value_valid: true,
                        pending_restart: true,
                        modes: {
                            strict: 'Redact credentials, caller data, free text, and routing details.',
                            show_routing: 'Show destinations and extensions while redacting credentials and caller data.',
                            off: 'Persist in-call tool diagnostics verbatim.',
                        },
                    },
                };
            }
            if (url === '/api/calls/filters') {
                return { data: { providers: [], pipelines: [], contexts: [], outcomes: [] } };
            }
            if (url === '/api/agents') return { data: [] };
            if (url === '/api/calls/record-1') return { data: callDetail };
            if (url === '/api/calls/record-1/recording') {
                return {
                    data: {
                        has_recording: false,
                        filename: null,
                        file_path: null,
                        file_size_bytes: 0,
                        duration_hint: null,
                    },
                };
            }
            throw new Error(`Unexpected GET ${url}`);
        });
    });

    it('removes the deep-link id when closing so the modal stays closed', async () => {
        render(
            <MemoryRouter initialEntries={['/history?range=7d&id=record-1']}>
                <Routes>
                    <Route
                        path="/history"
                        element={
                            <>
                                <CallHistoryPage />
                                <LocationProbe />
                            </>
                        }
                    />
                </Routes>
            </MemoryRouter>
        );

        expect(await screen.findByRole('dialog', { name: 'Call Details' })).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Close call details' }));

        await waitFor(() =>
            expect(screen.queryByRole('dialog', { name: 'Call Details' })).not.toBeInTheDocument()
        );
        expect(screen.getByTestId('location-search')).toHaveTextContent('?range=7d');
        expect(screen.getByTestId('location-search')).not.toHaveTextContent('id=record-1');
        expect(
            vi.mocked(axios.get).mock.calls.filter(([url]) => url === '/api/calls/record-1')
        ).toHaveLength(1);
    });

    it('moves and traps focus, closes on Escape, and restores the previous focus', async () => {
        render(
            <MemoryRouter initialEntries={['/history?range=7d&id=record-1']}>
                <button type="button">Return target</button>
                <Routes>
                    <Route path="/history" element={<CallHistoryPage />} />
                </Routes>
            </MemoryRouter>
        );

        const returnTarget = screen.getByRole('button', { name: 'Return target' });
        returnTarget.focus();

        const dialog = await screen.findByRole('dialog', { name: 'Call Details' });
        await waitFor(() => expect(dialog).toHaveFocus());

        const dialogButtons = Array.from(dialog.querySelectorAll<HTMLButtonElement>('button'));
        const firstButton = dialogButtons[0];
        const lastButton = dialogButtons[dialogButtons.length - 1];

        lastButton.focus();
        fireEvent.keyDown(document, { key: 'Tab' });
        expect(firstButton).toHaveFocus();

        firstButton.focus();
        fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
        expect(lastButton).toHaveFocus();

        fireEvent.keyDown(document, { key: 'Escape' });
        await waitFor(() => expect(dialog).not.toBeInTheDocument());
        expect(returnTarget).toHaveFocus();
    });

    it('surfaces the configured policy and links to its Environment setting', async () => {
        render(
            <MemoryRouter initialEntries={['/history']}>
                <CallHistoryPage />
                <FullLocationProbe />
            </MemoryRouter>
        );

        expect(await screen.findByText('Tool history privacy: Show routing')).toBeInTheDocument();
        expect(screen.getByText('AI Engine restart pending')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Configure redaction' }));

        expect(screen.getByTestId('full-location')).toHaveTextContent(
            '/env?section=call-history#system'
        );
    });

    it('shows final metadata provenance and applies an exact-match filter', async () => {
        render(
            <MemoryRouter initialEntries={['/history?id=record-1']}>
                <CallHistoryPage />
            </MemoryRouter>
        );

        expect(await screen.findByText('Call Metadata')).toBeInTheDocument();
        expect(screen.getByText('customer_tier')).toBeInTheDocument();
        expect(screen.getByText('gold')).toBeInTheDocument();
        expect(screen.getByText('Updated during call')).toBeInTheDocument();

        fireEvent.click(screen.getByTitle('Filters'));
        fireEvent.change(screen.getByLabelText('Metadata Field'), {
            target: { value: 'customer_tier' },
        });
        fireEvent.change(screen.getByLabelText('Metadata Value (exact)'), {
            target: { value: 'gold' },
        });

        await waitFor(() => {
            const callsRequest = vi.mocked(axios.get).mock.calls
                .filter(([url]) => url === '/api/calls')
                .slice(-1)[0];
            expect(callsRequest?.[1]).toMatchObject({
                params: {
                    call_metadata_key: 'customer_tier',
                    call_metadata_value: 'gold',
                },
            });
        });
    });

    it('resets to the first page when a metadata filter changes', async () => {
        render(
            <MemoryRouter initialEntries={['/history']}>
                <CallHistoryPage />
            </MemoryRouter>
        );

        const pageLabel = await screen.findByText('Page 1 of 2');
        const pagination = pageLabel.parentElement;
        const buttons = pagination?.querySelectorAll('button');
        expect(buttons).toHaveLength(2);
        fireEvent.click(buttons![1]);
        expect(await screen.findByText('Page 2 of 2')).toBeInTheDocument();

        fireEvent.click(screen.getByTitle('Filters'));
        fireEvent.change(screen.getByLabelText('Metadata Field'), {
            target: { value: 'customer_tier' },
        });

        expect(await screen.findByText('Page 1 of 2')).toBeInTheDocument();
        await waitFor(() => {
            const callsRequest = vi.mocked(axios.get).mock.calls
                .filter(([url]) => url === '/api/calls')
                .slice(-1)[0];
            expect(callsRequest?.[1]).toMatchObject({ params: { page: 1 } });
        });
    });
});
