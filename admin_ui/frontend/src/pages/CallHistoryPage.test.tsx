// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';

import CallHistoryPage from './CallHistoryPage';

const mocks = vi.hoisted(() => ({ confirm: vi.fn() }));

vi.mock('axios');
vi.mock('../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: mocks.confirm }),
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
        mocks.confirm.mockResolvedValue(false);
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

    it('opens the dedicated call troubleshooting route without pinning an exact time range', async () => {
        render(
            <MemoryRouter initialEntries={['/history?id=record-1']}>
                <Routes>
                    <Route path="/history" element={<CallHistoryPage />} />
                    <Route path="/logs" element={<FullLocationProbe />} />
                </Routes>
            </MemoryRouter>
        );

        await screen.findByRole('dialog', { name: 'Call Details' });
        fireEvent.click(screen.getAllByRole('button', { name: 'Troubleshoot' })[0]);

        const location = await screen.findByTestId('full-location');
        expect(location).toHaveTextContent('/logs?');
        expect(location).toHaveTextContent('mode=troubleshoot');
        expect(location).toHaveTextContent('call_id=asterisk-1');
        expect(location).not.toHaveTextContent('since=');
        expect(location).not.toHaveTextContent('until=');
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

        expect(await screen.findByRole('button', { name: 'Previous page' })).toBeDisabled();
        const nextPage = screen.getByRole('button', { name: 'Next page' });
        expect(nextPage).toBeEnabled();
        fireEvent.click(nextPage);
        expect(await screen.findByText('Page 2 of 2')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled();

        fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
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

    it('hides selected outcomes in the list, the stats and the exports', async () => {
        const defaultGet = vi.mocked(axios.get).getMockImplementation()!;
        vi.mocked(axios.get).mockImplementation(async (url, config) => {
            if (url === '/api/calls/filters') {
                return { data: { providers: [], pipelines: [], contexts: [], outcomes: ['completed', 'abandoned'] } };
            }
            if (typeof url === 'string' && url.startsWith('/api/calls/export/')) return { data: new Blob() };
            return defaultGet(url, config);
        });
        window.URL.createObjectURL = vi.fn(() => 'blob:test');
        window.URL.revokeObjectURL = vi.fn();
        const downloadClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

        render(
            <MemoryRouter initialEntries={['/history']}>
                <CallHistoryPage />
            </MemoryRouter>
        );

        fireEvent.click(await screen.findByTitle('Filters'));
        fireEvent.click(screen.getByRole('button', { name: 'Hide' }));
        fireEvent.click(await screen.findByLabelText('abandoned'));

        const lastParams = (url: string) => vi.mocked(axios.get).mock.calls
            .filter(([u]) => u === url)
            .slice(-1)[0]?.[1]?.params;
        await waitFor(() => {
            expect(lastParams('/api/calls')).toMatchObject({ exclude_outcome: 'abandoned', page: 1 });
            expect(lastParams('/api/calls/stats')).toEqual({ exclude_outcome: 'abandoned' });
        });
        expect(lastParams('/api/calls')).not.toHaveProperty('outcome');

        fireEvent.click(screen.getByRole('button', { name: /CSV/ }));
        await waitFor(() => {
            expect(lastParams('/api/calls/export/csv')).toEqual({ exclude_outcome: 'abandoned' });
        });
        downloadClick.mockRestore();

        fireEvent.click(screen.getByText('Clear all'));
        await waitFor(() => {
            expect(lastParams('/api/calls/stats')).toEqual({});
        });
    });

    it('keeps the latest calls and pagination when an older request resolves last', async () => {
        const get = vi.mocked(axios.get).getMockImplementation()!;
        const pending = new Map<string, (value: unknown) => void>();
        vi.mocked(axios.get).mockImplementation(async (url, config) => {
            if (url === '/api/calls') {
                const caller = (config?.params as Record<string, string> | undefined)?.caller_name;
                if (caller) return new Promise(resolve => pending.set(caller, resolve));
            }
            return get(url, config);
        });

        render(<MemoryRouter initialEntries={['/history']}><CallHistoryPage /></MemoryRouter>);
        expect(await screen.findByText('Alice')).toBeInTheDocument();
        fireEvent.click(screen.getByTitle('Filters'));
        const callerName = screen.getByPlaceholderText('Name');
        fireEvent.change(callerName, { target: { value: 'Alice' } });
        await waitFor(() => expect(pending.has('Alice')).toBe(true));
        fireEvent.change(callerName, { target: { value: 'Bob' } });
        await waitFor(() => expect(pending.has('Bob')).toBe(true));

        await act(async () => { pending.get('Bob')!({ data: {
            calls: [{ ...callDetail, id: 'record-bob', caller_name: 'Bob' }], total: 1, total_pages: 1,
        } }); });
        expect(await screen.findByText('Bob')).toBeInTheDocument();
        await act(async () => { pending.get('Alice')!({ data: { calls: [callDetail], total: 51, total_pages: 3 } }); });

        expect(screen.getByText('Bob')).toBeInTheDocument();
        expect(screen.queryByText('Alice')).not.toBeInTheDocument();
        expect(screen.getByText('Page 1 of 1')).toBeInTheDocument();
        expect(screen.getByText('Showing 1 to 1 of 1 calls')).toBeInTheDocument();
    });

    it('ignores an older list failure while the current request is still loading', async () => {
        const get = vi.mocked(axios.get).getMockImplementation()!;
        const pending = new Map<string, { resolve: (value: unknown) => void; reject: (reason: unknown) => void }>();
        vi.mocked(axios.get).mockImplementation(async (url, config) => {
            if (url === '/api/calls') {
                const caller = (config?.params as Record<string, string> | undefined)?.caller_name;
                if (caller) return new Promise((resolve, reject) => pending.set(caller, { resolve, reject }));
            }
            return get(url, config);
        });

        render(<MemoryRouter initialEntries={['/history']}><CallHistoryPage /></MemoryRouter>);
        expect(await screen.findByText('Alice')).toBeInTheDocument();
        fireEvent.click(screen.getByTitle('Filters'));
        const callerName = screen.getByPlaceholderText('Name');
        fireEvent.change(callerName, { target: { value: 'Alice' } });
        await waitFor(() => expect(pending.has('Alice')).toBe(true));
        fireEvent.change(callerName, { target: { value: 'Bob' } });
        await waitFor(() => expect(pending.has('Bob')).toBe(true));

        await act(async () => { pending.get('Alice')!.reject({ response: { data: { detail: 'Obsolete Alice failure' } } }); });
        expect(screen.queryByText('Obsolete Alice failure')).not.toBeInTheDocument();
        expect(screen.queryByRole('table')).not.toBeInTheDocument();

        await act(async () => { pending.get('Bob')!.resolve({ data: {
            calls: [{ ...callDetail, id: 'record-bob', caller_name: 'Bob' }], total: 1, total_pages: 1,
        } }); });
        expect(await screen.findByText('Bob')).toBeInTheDocument();
    });

    it('keeps the latest statistics when an older request resolves last', async () => {
        const get = vi.mocked(axios.get).getMockImplementation()!;
        const pending = new Map<string, (value: unknown) => void>();
        const statsFor = (total: number) => ({ data: { total_calls: total, outcomes: {}, providers: {}, top_tools: {} } });
        vi.mocked(axios.get).mockImplementation(async (url, config) => {
            if (url === '/api/calls/stats') {
                const caller = (config?.params as Record<string, string> | undefined)?.caller_name;
                if (!caller) return statsFor(5);
                return new Promise(resolve => pending.set(caller, resolve));
            }
            return get(url, config);
        });

        render(<MemoryRouter initialEntries={['/history']}><CallHistoryPage /></MemoryRouter>);
        expect(await screen.findByText('5')).toBeInTheDocument();

        fireEvent.click(screen.getByTitle('Filters'));
        const callerName = screen.getByPlaceholderText('Name');
        fireEvent.change(callerName, { target: { value: 'Alice' } });
        await waitFor(() => expect(pending.has('Alice')).toBe(true));
        fireEvent.change(callerName, { target: { value: 'Bob' } });
        await waitFor(() => expect(pending.has('Bob')).toBe(true));

        // Responses arrive in reverse order: Bob (current filter) first, then Alice (stale).
        await act(async () => { pending.get('Bob')!(statsFor(222)); });
        expect(await screen.findByText('222')).toBeInTheDocument();
        await act(async () => { pending.get('Alice')!(statsFor(111)); });

        expect(screen.getByText('222')).toBeInTheDocument();
        expect(screen.queryByText('111')).not.toBeInTheDocument();
    });

    it('keeps the empty-history message distinct from an empty filter result', async () => {
        const get = vi.mocked(axios.get).getMockImplementation()!;
        vi.mocked(axios.get).mockImplementation(async (url, config) => {
            if (url === '/api/calls') return { data: { calls: [], total: 0, total_pages: 1 } };
            return get(url, config);
        });
        render(<MemoryRouter initialEntries={['/history']}><CallHistoryPage /></MemoryRouter>);

        expect(await screen.findByRole('heading', { name: 'No Calls Found', level: 2 })).toBeInTheDocument();
        expect(screen.getByText('Call history will appear here once calls are made.')).toBeInTheDocument();
        fireEvent.change(screen.getByRole('textbox', { name: 'Search transcripts' }), { target: { value: 'missing' } });
        expect(await screen.findByText('No calls match your filters. Try adjusting your search criteria.')).toBeInTheDocument();
        expect(screen.queryByText('Call history will appear here once calls are made.')).not.toBeInTheDocument();
    });

    it('keeps row deletion from opening call details and leaves the row intact on cancellation', async () => {
        render(
            <MemoryRouter initialEntries={['/history']}>
                <CallHistoryPage />
            </MemoryRouter>,
        );

        fireEvent.click(await screen.findByRole('button', { name: 'Delete', exact: true }));

        await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
        expect(axios.delete).not.toHaveBeenCalled();
        expect(screen.queryByRole('dialog', { name: 'Call Details' })).not.toBeInTheDocument();
        expect(vi.mocked(axios.get).mock.calls.some(([url]) => url === '/api/calls/record-1')).toBe(false);
        expect(screen.getByRole('button', { name: 'Delete', exact: true })).toBeInTheDocument();
    });

    it('updates the recording button name for play, pause, resume, and playback completion', async () => {
        const get = vi.mocked(axios.get).getMockImplementation()!;
        vi.mocked(axios.get).mockImplementation(async (url, config) => {
            if (url === '/api/calls/record-1/recording') {
                return { data: { has_recording: true, filename: 'call.wav', file_size_bytes: 1024 } };
            }
            if (url === '/api/calls/record-1/recording/audio') return { data: new Blob() };
            return get(url, config);
        });
        const audio = document.createElement('audio');
        audio.src = 'blob:recording';
        const play = vi.spyOn(audio, 'play').mockResolvedValue(undefined);
        const pause = vi.spyOn(audio, 'pause').mockImplementation(() => undefined);
        vi.stubGlobal('Audio', vi.fn(function () { return audio; }));
        vi.stubGlobal('URL', class extends URL {
            static createObjectURL = vi.fn(() => 'blob:recording');
            static revokeObjectURL = vi.fn();
        });

        const { unmount } = render(
            <MemoryRouter initialEntries={['/history?id=record-1']}>
                <CallHistoryPage />
            </MemoryRouter>,
        );
        try {
            fireEvent.click(await screen.findByRole('button', { name: 'Play recording' }));
            const pauseButton = await screen.findByRole('button', { name: 'Pause', exact: true });
            expect(pauseButton).toHaveAttribute('title', 'Pause');
            fireEvent.click(pauseButton);
            expect(pause).toHaveBeenCalledTimes(1);
            fireEvent.click(screen.getByRole('button', { name: 'Play recording' }));
            await screen.findByRole('button', { name: 'Pause', exact: true });
            expect(play).toHaveBeenCalledTimes(2);
            fireEvent.ended(audio);
            expect(await screen.findByRole('button', { name: 'Play recording' })).toHaveAttribute('title', 'Play recording');
        } finally {
            unmount();
            vi.restoreAllMocks();
            vi.unstubAllGlobals();
        }
    });

});
