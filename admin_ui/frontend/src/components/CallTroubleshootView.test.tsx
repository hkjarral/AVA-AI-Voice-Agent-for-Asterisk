// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import CallTroubleshootView from './logs/CallTroubleshootView';

vi.mock('axios');
const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('sonner', () => ({ toast: toastMocks }));

const preview = {
    call: {
        call_id: '1789247215.144',
        start_time: '2026-09-13T12:00:00Z',
        duration_seconds: 20,
        provider_name: 'google_live',
        pipeline_name: null,
        agent: 'support',
        outcome: 'completed',
    },
    analysis: {
        status: 'review',
        headline: '1 warning event to review',
        event_count: 3,
        findings: [
            {
                severity: 'warning',
                message: 'Provider jitter',
                component: 'src.providers.google_live',
            },
        ],
        lifecycle: [
            { name: 'Call started', captured: true },
            { name: 'Call ended', captured: true },
        ],
        missing_evidence: [],
        recommendation: 'Review warnings in context.',
    },
    settings: { resolved: { audio_profile: 'telephony_ulaw_8k' } },
    tool_counts: { pre_call: 1, in_call: 2, post_call: 1 },
    log_evidence: {
        available: true,
        format: 'mixed',
        observed_levels: ['info', 'warning'],
        matching_events: 3,
        original_bytes: 2048,
    },
    sources: {
        ai_engine: { selected: true, required: true, reason: 'Core evidence.' },
        local_ai_server: {
            selected: true,
            required: false,
            recommended: false,
            reason: 'Probably not needed.',
        },
        admin_ui: {
            selected: true,
            required: false,
            recommended: false,
            reason: 'Optional management context.',
        },
    },
};

describe('CallTroubleshootView', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.mocked(axios.get).mockResolvedValue({ data: preview });
        vi.mocked(axios.post).mockResolvedValue({
            data: new Blob(['zip']),
            headers: { 'content-disposition': 'attachment; filename="ava-call-support.zip"' },
        });
        window.URL.createObjectURL = vi.fn(() => 'blob:test');
        window.URL.revokeObjectURL = vi.fn();
        HTMLAnchorElement.prototype.click = vi.fn();
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText: vi.fn().mockResolvedValue(undefined) },
        });
    });

    it('shows actionable summary and defaults optional evidence to selected', async () => {
        render(
            <MemoryRouter>
                <CallTroubleshootView
                    callId="1789247215.144"
                    events={[]}
                    onChooseAnotherCall={vi.fn()}
                />
            </MemoryRouter>
        );

        expect(
            await screen.findByRole('heading', { name: 'Troubleshoot Call' })
        ).toBeInTheDocument();
        expect(screen.getByText('1 warning event to review')).toBeInTheDocument();
        expect(screen.getByText(/mixed/i)).toBeInTheDocument();
        const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
        expect(checkboxes).toHaveLength(5);
        expect(checkboxes.every(checkbox => checkbox.checked)).toBe(true);
        expect(
            screen.getByText(/No recordings, phone numbers, API keys or secrets/)
        ).toBeInTheDocument();
    });

    it('downloads the selected package options', async () => {
        render(
            <MemoryRouter>
                <CallTroubleshootView
                    callId="1789247215.144"
                    events={[]}
                    onChooseAnotherCall={vi.fn()}
                />
            </MemoryRouter>
        );
        await screen.findByRole('heading', { name: 'Troubleshoot Call' });
        fireEvent.click(screen.getAllByRole('button', { name: /Download Support Package/ })[0]);

        await waitFor(() =>
            expect(axios.post).toHaveBeenCalledWith(
                '/api/support/call-bundle',
                expect.objectContaining({
                    call_id: '1789247215.144',
                    include_local_ai_server: true,
                    include_admin_ui: true,
                    include_transcript: true,
                    include_tools: true,
                    include_settings: true,
                }),
                { responseType: 'blob' }
            )
        );
    });

    it('copies the call ID after awaiting the clipboard write', async () => {
        render(
            <MemoryRouter>
                <CallTroubleshootView
                    callId="1789247215.144"
                    events={[]}
                    onChooseAnotherCall={vi.fn()}
                />
            </MemoryRouter>
        );
        await screen.findByRole('heading', { name: 'Troubleshoot Call' });
        fireEvent.click(screen.getByTitle('Copy Call ID'));

        await waitFor(() =>
            expect(navigator.clipboard.writeText).toHaveBeenCalledWith('1789247215.144')
        );
        expect(toastMocks.success).toHaveBeenCalledWith('Call ID copied');
    });

    it('reports clipboard unavailability without throwing on an insecure origin', async () => {
        Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
        render(
            <MemoryRouter>
                <CallTroubleshootView
                    callId="1789247215.144"
                    events={[]}
                    onChooseAnotherCall={vi.fn()}
                />
            </MemoryRouter>
        );
        await screen.findByRole('heading', { name: 'Troubleshoot Call' });
        fireEvent.click(screen.getByTitle('Copy Call ID'));

        expect(toastMocks.error).toHaveBeenCalledWith('Could not copy the Call ID');
    });

    it('reports rejected clipboard writes', async () => {
        Object.defineProperty(navigator, 'clipboard', {
            configurable: true,
            value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
        });
        render(
            <MemoryRouter>
                <CallTroubleshootView
                    callId="1789247215.144"
                    events={[]}
                    onChooseAnotherCall={vi.fn()}
                />
            </MemoryRouter>
        );
        await screen.findByRole('heading', { name: 'Troubleshoot Call' });
        fireEvent.click(screen.getByTitle('Copy Call ID'));

        await waitFor(() =>
            expect(toastMocks.error).toHaveBeenCalledWith('Could not copy the Call ID')
        );
    });
});
