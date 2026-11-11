// @vitest-environment jsdom
import { useState } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { MemoryRouter } from 'react-router-dom';

import ToolForm from './ToolForm';

// ToolForm fetches email-template defaults on mount; stub axios so the test
// doesn't attempt a real network call.
vi.mock('axios', () => ({
    default: {
        get: vi.fn(() => Promise.reject(new Error('not mocked'))),
        post: vi.fn(() => Promise.reject(new Error('not mocked'))),
        isCancel: vi.fn(() => false),
    },
}));

type TestToolConfig = {
    transfer?: {
        enabled?: boolean;
        deferred_audio_drain_timeout_sec?: number;
        deferred_audio_drain_quiet_ms?: number;
        deferred_silent_commit_timeout_sec?: number;
    };
    extensions?: {
        internal?: Record<string, {
            dial_string?: string;
            transfer?: boolean;
            device_states?: { id?: string; status?: string }[];
        }>;
    };
    check_extension_status?: {
        state_mapping?: { free?: string[]; busy?: string[]; unavailable?: string[] };
    };
};

const baseConfig = (): TestToolConfig => ({
    extensions: {
        internal: {
            '102': {
                dial_string: 'PJSIP/102',
                transfer: true,
            },
        },
    },
});

// ToolForm is a fully controlled component (config in, onChange out). This
// harness mirrors what the real config page does — feed onChange's result
// back in as the next config — so DOM assertions after an interaction see
// the update, the same way they would in the app.
const Harness = ({ onChange }: { onChange: (c: TestToolConfig) => void }) => {
    const [config, setConfig] = useState<TestToolConfig>(baseConfig());
    return (
        <ToolForm
            config={config}
            onChange={(next) => {
                onChange(next);
                setConfig(next);
            }}
        />
    );
};

describe('ToolForm — deferred transfer audio safety (issue #662)', () => {
    it('shows safe drain defaults and persists operator changes', async () => {
        const onChange = vi.fn();

        render(<Harness onChange={onChange} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        const timeout = screen.getByLabelText('Deferred Audio Drain Timeout (seconds)');
        const quiet = screen.getByLabelText('Deferred Audio Quiet Period (ms)');
        expect(timeout).toHaveValue(15);
        expect(quiet).toHaveValue(500);
        expect(timeout).toHaveAttribute('min', '0');
        expect(timeout).toHaveAttribute('max', '30');
        expect(quiet).toHaveAttribute('min', '0');
        expect(quiet).toHaveAttribute('max', '5000');

        fireEvent.change(timeout, { target: { value: '0' } });
        fireEvent.change(quiet, { target: { value: '0' } });
        let lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.transfer.deferred_audio_drain_timeout_sec).toBe(0);
        expect(lastCall.transfer.deferred_audio_drain_quiet_ms).toBe(0);

        fireEvent.change(timeout, { target: { value: '31' } });
        fireEvent.change(quiet, { target: { value: '5001' } });
        lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.transfer.deferred_audio_drain_timeout_sec).toBe(30);
        expect(lastCall.transfer.deferred_audio_drain_quiet_ms).toBe(5000);

        fireEvent.change(timeout, { target: { value: '20' } });
        fireEvent.change(quiet, { target: { value: '750' } });

        lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.transfer.deferred_audio_drain_timeout_sec).toBe(20);
        expect(lastCall.transfer.deferred_audio_drain_quiet_ms).toBe(750);
    });
});

describe('ToolForm — deferred transfer silent reply commit timeout', () => {
    it('defaults to 4 seconds, clamps to 0-30 and persists operator changes', async () => {
        const onChange = vi.fn();

        render(<Harness onChange={onChange} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        const input = screen.getByLabelText('Silent Reply Commit Timeout (seconds)');
        expect(input).toHaveValue(4);
        expect(input).toHaveAttribute('min', '0');
        expect(input).toHaveAttribute('max', '30');

        const last = () => onChange.mock.calls[onChange.mock.calls.length - 1][0];
        fireEvent.change(input, { target: { value: '0' } });
        expect(last().transfer.deferred_silent_commit_timeout_sec).toBe(0);
        fireEvent.change(input, { target: { value: '45' } });
        expect(last().transfer.deferred_silent_commit_timeout_sec).toBe(30);
        fireEvent.change(input, { target: { value: '2.5' } });
        expect(last().transfer.deferred_silent_commit_timeout_sec).toBe(2.5);
    });
});

describe('ToolForm — per-extension availability signals (issue #577)', () => {
    it('adds a custom device state and reports it via onChange', async () => {
        const user = userEvent.setup();
        const onChange = vi.fn();

        render(<Harness onChange={onChange} />);
        // Let the mocked (rejecting) email-defaults fetch settle before interacting,
        // so its state update doesn't land outside of act() mid-test.
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        // Expert settings are gated behind the "Live Agent Expert Settings" switch.
        await user.click(screen.getByRole('checkbox', { name: /Live Agent Expert Settings/i }));

        await user.click(screen.getByRole('button', { name: /Add custom state/i }));

        const idInput = screen.getByPlaceholderText(/Custom:DND102/i);
        await user.type(idInput, 'Custom:DND102');

        const statusSelect = screen.getByLabelText('Custom device state 1 status', { exact: true });
        await user.selectOptions(statusSelect, 'dnd');

        expect(onChange).toHaveBeenCalled();
        const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.extensions.internal['102'].device_states).toContainEqual({
            id: 'Custom:DND102',
            status: 'dnd',
        });
    });

    it('shows the auto-detection note for each internal extension', async () => {
        const user = userEvent.setup();
        render(<ToolForm config={baseConfig()} onChange={vi.fn()} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        await user.click(screen.getByRole('checkbox', { name: /Live Agent Expert Settings/i }));

        expect(screen.getByText(/Auto \(over ARI\)/i)).toBeInTheDocument();
    });
});

describe('ToolForm — global device-state value mapping (issue #577)', () => {
    it('shows the default buckets in the state value mapping panel', async () => {
        const user = userEvent.setup();
        render(<ToolForm config={baseConfig()} onChange={vi.fn()} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        await user.click(screen.getByRole('button', { name: /State Value Mapping/i }));

        expect(screen.getByLabelText('Free')).toHaveValue('NOT_INUSE');
        expect(screen.getByLabelText('Busy')).toHaveValue('INUSE BUSY RINGING RINGINUSE ONHOLD');
        expect(screen.getByLabelText('Not available')).toHaveValue('UNAVAILABLE INVALID UNKNOWN');
    });

    it('adds ONHOLD to the free bucket and reports it via onChange', async () => {
        const user = userEvent.setup();
        const onChange = vi.fn();

        render(<Harness onChange={onChange} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        await user.click(screen.getByRole('button', { name: /State Value Mapping/i }));

        const freeInput = screen.getByLabelText('Free');
        await user.type(freeInput, ' ONHOLD');
        await user.tab();

        expect(onChange).toHaveBeenCalled();
        const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        const mapping = lastCall.check_extension_status.state_mapping;
        expect(mapping.free).toContain('ONHOLD');
        expect(mapping.free).toContain('NOT_INUSE');
        expect(mapping.busy).toEqual(['INUSE', 'BUSY', 'RINGING', 'RINGINUSE']);
        expect(mapping.unavailable).toEqual(['UNAVAILABLE', 'INVALID', 'UNKNOWN']);
    });

    it('keeps an explicitly-cleared bucket empty when another bucket is edited afterward (CodeRabbit round 2)', async () => {
        const user = userEvent.setup();
        const onChange = vi.fn();

        render(<Harness onChange={onChange} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        await user.click(screen.getByRole('button', { name: /State Value Mapping/i }));

        // Move NOT_INUSE out of Free by adding it to Busy; the dedup logic removes it
        // from Free, leaving Free explicitly empty.
        const busyInput = screen.getByLabelText('Busy');
        await user.type(busyInput, ' NOT_INUSE');
        await user.tab();

        expect(screen.getByLabelText('Free')).toHaveValue('');
        let lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.check_extension_status.state_mapping.free).toEqual([]);

        // Editing an unrelated bucket must not silently restore Free's default.
        const unavailableInput = screen.getByLabelText('Not available');
        await user.type(unavailableInput, ' FOO');
        await user.tab();

        expect(screen.getByLabelText('Free')).toHaveValue('');
        lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.check_extension_status.state_mapping.free).toEqual([]);
    });

    it('resets the mapping to defaults and omits state_mapping from config', async () => {
        const user = userEvent.setup();
        const onChange = vi.fn();

        render(<Harness onChange={onChange} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        await user.click(screen.getByRole('button', { name: /State Value Mapping/i }));

        const freeInput = screen.getByLabelText('Free');
        await user.type(freeInput, ' ONHOLD');
        await user.tab();

        await user.click(screen.getByRole('button', { name: /Reset to defaults/i }));

        expect(screen.getByLabelText('Free')).toHaveValue('NOT_INUSE');
        const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.check_extension_status?.state_mapping).toBeUndefined();
    });
});


describe('ToolForm farewell delay deprecation (issue #677)', () => {
    it('explains audio drain and preserves a legacy value when another tool setting changes', async () => {
        const onChange = vi.fn();
        render(<ToolForm config={{ ...baseConfig(), farewell_hangup_delay_sec: 4 }} onChange={onChange} />);
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

        expect(screen.queryByText('Farewell Hangup Delay (seconds)')).not.toBeInTheDocument();
        expect(screen.getByText(/legacy farewell_hangup_delay_sec setting is deprecated and ignored/i)).toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Default Farewell Message'), { target: { value: 'Goodbye!' } });
        expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ farewell_hangup_delay_sec: 4 }));
    });
});

describe('ToolForm attended transfer announcement voice', () => {
    const TTS_OPTIONS_URL = '/api/config/providers/tts-options';
    const TTS_OPTIONS = [
        { key: 'google_tts', label: 'Google Cloud Text-to-Speech', ready: true },
        { key: 'elevenlabs_tts', label: 'ElevenLabs', ready: false, reason: 'credential_missing' },
    ];

    type AttendedConfig = { attended_transfer: Record<string, unknown> };

    const attended = (extra: Record<string, unknown> = {}): AttendedConfig => ({
        attended_transfer: { enabled: true, screening_mode: 'basic_tts', ...extra },
    });

    const AttendedHarness = ({ initial, onChange }: { initial: AttendedConfig; onChange: (c: AttendedConfig) => void }) => {
        const [config, setConfig] = useState<AttendedConfig>(initial);
        return (
            <MemoryRouter>
                <ToolForm
                    config={config}
                    onChange={(next) => {
                        onChange(next);
                        setConfig(next);
                    }}
                />
            </MemoryRouter>
        );
    };

    const lastAttended = (onChange: ReturnType<typeof vi.fn>) => {
        const calls = onChange.mock.calls;
        return (calls[calls.length - 1][0] as AttendedConfig).attended_transfer;
    };

    beforeEach(() => {
        vi.mocked(axios.get).mockImplementation(async (url) => {
            if (url === TTS_OPTIONS_URL) return { data: TTS_OPTIONS };
            throw new Error('not mocked');
        });
    });

    afterEach(() => {
        vi.mocked(axios.get).mockImplementation(() => Promise.reject(new Error('not mocked')));
    });

    it('lists configured TTS providers after the Local AI Server default', async () => {
        render(<AttendedHarness initial={attended()} onChange={vi.fn()} />);
        const select = screen.getByLabelText('Announcement voice') as HTMLSelectElement;
        await waitFor(() => expect(select).toHaveTextContent('ElevenLabs — not ready'));

        expect(Array.from(select.options).map((option) => option.textContent)).toEqual([
            'Local AI Server (default)',
            'Google Cloud Text-to-Speech — ready',
            'ElevenLabs — not ready',
        ]);
        expect(select).toHaveValue('');
        expect(vi.mocked(axios.get)).toHaveBeenCalledWith(TTS_OPTIONS_URL);
        expect(screen.queryByLabelText('Voice name')).not.toBeInTheDocument();
    });

    it('writes tts_provider and tts_options only when the operator sets them', async () => {
        const onChange = vi.fn();
        render(<AttendedHarness initial={attended()} onChange={onChange} />);
        const select = screen.getByLabelText('Announcement voice');
        await waitFor(() => expect(select).toHaveTextContent('Google Cloud Text-to-Speech — ready'));

        fireEvent.change(select, { target: { value: 'google_tts' } });
        expect(lastAttended(onChange)).toEqual({ enabled: true, screening_mode: 'basic_tts', tts_provider: 'google_tts' });

        fireEvent.change(screen.getByLabelText('Voice name'), { target: { value: 'en-US-Chirp3-HD-Charon' } });
        fireEvent.change(screen.getByLabelText('Voice language'), { target: { value: 'en-US' } });
        expect(lastAttended(onChange)).toEqual({
            enabled: true,
            screening_mode: 'basic_tts',
            tts_provider: 'google_tts',
            tts_options: { voice: 'en-US-Chirp3-HD-Charon', language: 'en-US' },
        });

        fireEvent.change(screen.getByLabelText('Voice language'), { target: { value: '' } });
        expect(lastAttended(onChange).tts_options).toEqual({ voice: 'en-US-Chirp3-HD-Charon' });
    });

    it('removes tts_provider and tts_options when Local AI Server is chosen again', async () => {
        const onChange = vi.fn();
        render(
            <AttendedHarness
                initial={attended({ tts_provider: 'google_tts', tts_options: { voice: 'en-US-Chirp3-HD-Charon' } })}
                onChange={onChange}
            />,
        );
        const select = screen.getByLabelText('Announcement voice');
        await waitFor(() => expect(select).toHaveTextContent('Google Cloud Text-to-Speech — ready'));
        expect(select).toHaveValue('google_tts');
        expect(screen.getByLabelText('Voice name')).toHaveValue('en-US-Chirp3-HD-Charon');

        fireEvent.change(select, { target: { value: '' } });
        const saved = lastAttended(onChange);
        expect(saved).not.toHaveProperty('tts_provider');
        expect(saved).not.toHaveProperty('tts_options');
        expect(saved).toEqual({ enabled: true, screening_mode: 'basic_tts' });
    });

    it('adds no TTS keys when an unrelated attended-transfer field changes', async () => {
        const onChange = vi.fn();
        render(<AttendedHarness initial={attended()} onChange={onChange} />);
        await waitFor(() => expect(screen.getByLabelText('Announcement voice')).toHaveTextContent('ElevenLabs — not ready'));

        fireEvent.change(screen.getByLabelText('MOH Class'), { target: { value: 'jazz' } });
        expect(lastAttended(onChange)).toEqual({ enabled: true, screening_mode: 'basic_tts', moh_class: 'jazz' });
    });

    it('shows a not-ready status for a provider without credentials', async () => {
        render(<AttendedHarness initial={attended({ tts_provider: 'elevenlabs_tts' })} onChange={vi.fn()} />);

        expect(await screen.findByText('Provider is not ready')).toBeInTheDocument();
        expect(screen.getByText(/Its credentials are not configured\./)).toBeInTheDocument();
        // A new tab keeps this page's unsaved edits.
        const link = screen.getByRole('link', { name: 'Open Providers (opens in a new tab)' });
        expect(link).toHaveAttribute('href', '/providers');
        expect(link).toHaveAttribute('target', '_blank');
        expect(screen.getByText(/needs an AI Engine restart before attended transfers can use it/)).toBeInTheDocument();
    });

    it('shows an unknown state, not "not configured", when the provider list fails to load', async () => {
        vi.mocked(axios.get).mockImplementation(async (url) => {
            if (url === TTS_OPTIONS_URL) throw new Error('500');
            throw new Error('not mocked');
        });
        render(<AttendedHarness initial={attended({ tts_provider: 'google_tts' })} onChange={vi.fn()} />);

        expect(await screen.findByText("Couldn't load the provider list")).toBeInTheDocument();
        expect(screen.queryByText('Provider is not ready')).not.toBeInTheDocument();
        expect(screen.queryByText(/This provider is not configured/)).not.toBeInTheDocument();
        const select = screen.getByLabelText('Announcement voice') as HTMLSelectElement;
        expect(select).toHaveValue('google_tts');
        expect(Array.from(select.options).map((option) => option.textContent)).toEqual([
            'Local AI Server (default)',
            'google_tts',
        ]);
    });

    it('shows a ready status and no longer claims TTS requires Local AI Server', async () => {
        render(<AttendedHarness initial={attended({ tts_provider: 'google_tts' })} onChange={vi.fn()} />);

        expect(await screen.findByText('Provider is ready')).toBeInTheDocument();
        expect(screen.queryByText(/Requires Local AI Server for TTS/)).not.toBeInTheDocument();
        expect(screen.getByText(/Prompts are spoken by the selected announcement voice/)).toBeInTheDocument();
    });
});
