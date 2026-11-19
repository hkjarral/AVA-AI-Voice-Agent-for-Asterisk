// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import yaml from 'js-yaml';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PipelinesPage from './PipelinesPage';
import ProfilesPage from './ProfilesPage';

type TestConfig = {
    active_pipeline: string;
    providers: Record<string, { capabilities: string[] }>;
    pipelines: Record<string, { stt: string; llm: string; tts: string; description?: string }>;
    profiles: { default: string; active: { internal_rate_hz: number }; spare?: { internal_rate_hz: number } };
    contexts: Record<string, { profile?: string }>;
};

const mocks = vi.hoisted(() => ({ config: {} as TestConfig, confirm: vi.fn(), error: vi.fn() }));
vi.mock('axios');
vi.mock('sonner', () => ({ toast: { error: mocks.error, success: vi.fn(), warning: vi.fn() } }));
vi.mock('../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: mocks.confirm }) }));
vi.mock('../hooks/useRestartRequired', () => ({ useRestartRequired: () => ({ restartRequired: false, refetch: vi.fn().mockResolvedValue(undefined) }) }));
vi.mock('../utils/configCache', () => ({
    getCachedConfig: () => ({ config: mocks.config, yamlError: null }),
    loadConfigYaml: async () => ({ config: mocks.config, yamlError: null }),
}));
// Keep the actual save validation/serialization while avoiding unrelated provider form UI.
vi.mock('../components/config/PipelineForm', () => ({ default: ({ config, onChange }: { config: { description?: string }; onChange: (config: { description?: string }) => void }) => (
    <input aria-label="Pipeline draft" value={config.description || ''} onChange={e => onChange({ ...config, description: e.target.value })} />
) }));

const deferred = () => {
    let resolve!: (value: unknown) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};

beforeEach(() => {
    vi.resetAllMocks();
    mocks.config = {
        active_pipeline: 'active',
        providers: { local_stt: { capabilities: ['stt'] }, local_llm: { capabilities: ['llm'] }, local_tts: { capabilities: ['tts'] } },
        pipelines: {
            active: { stt: 'local_stt', llm: 'local_llm', tts: 'local_tts' },
            spare: { stt: 'local_stt', llm: 'local_llm', tts: 'local_tts' },
        },
        profiles: { default: 'active', active: { internal_rate_hz: 8000 }, spare: { internal_rate_hz: 16000 } },
        contexts: { demo: { profile: 'spare' } },
    };
    mocks.confirm.mockResolvedValue(true);
    vi.mocked(axios.get).mockImplementation(async url => ({ data: url === '/api/agents' ? [] : {} }));
});

for (const [kind, Page] of [['pipeline', PipelinesPage], ['profile', ProfilesPage]] as const) {
    describe(`${kind} pending actions`, () => {
        it('serializes saves, locks the editor while pending, and retains the draft on rejection for retry', async () => {
            const request = deferred();
            vi.mocked(axios.post).mockReturnValueOnce(request.promise).mockResolvedValue({ data: {} });
            const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
            render(<Page />);
            await act(async () => undefined);
            fireEvent.click(screen.getByRole('button', { name: `Edit ${kind} spare` }));
            const dialog = screen.getByRole('dialog');
            const input = kind === 'pipeline' ? screen.getByRole('textbox', { name: 'Pipeline draft' }) : within(dialog).getByRole('spinbutton', { name: /Internal Sample Rate/ });
            fireEvent.change(input, { target: { value: kind === 'pipeline' ? 'Keep this draft' : '24000' } });
            const save = within(dialog).getByRole('button', { name: 'Save Changes' });
            // Same event turn: a state-only guard would allow both submissions.
            act(() => { fireEvent.click(save); fireEvent.click(save); });
            expect(axios.post).toHaveBeenCalledTimes(1);
            expect(save).toBeDisabled();
            expect(save.querySelector('.animate-spin')).not.toBeNull();
            expect(input).toBeDisabled();
            expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled();
            expect(within(dialog).getByRole('button', { name: 'Close' })).toBeDisabled();
            fireEvent.keyDown(document, { key: 'Escape' });
            expect(dialog).toBeInTheDocument();
            expect(screen.getByRole('button', { name: `Delete ${kind} spare` })).toBeDisabled();
            await act(async () => request.reject(new Error('save failed')));
            expect(mocks.error).toHaveBeenCalledWith('Failed to save configuration', ...(kind === 'profile' ? [expect.any(Object)] : []));
            expect(save).toBeEnabled();
            expect(input).toHaveValue(kind === 'pipeline' ? 'Keep this draft' : 24000);
            expect(mocks.config.pipelines.spare.description).toBeUndefined();
            expect(mocks.config.profiles.spare?.internal_rate_hz).toBe(16000);
            fireEvent.click(save);
            await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
            expect(axios.post).toHaveBeenCalledTimes(2);
            consoleError.mockRestore();
        });

        it('closes only after a delayed successful save, and leaves a remounted editor alone', async () => {
            const request = deferred();
            vi.mocked(axios.post).mockReturnValue(request.promise);
            const first = render(<Page />);
            await act(async () => undefined);
            fireEvent.click(screen.getByRole('button', { name: `Edit ${kind} spare` }));
            fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
            expect(screen.getByRole('dialog')).toBeInTheDocument();
            expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
            first.unmount();
            render(<Page />);
            await act(async () => undefined);
            fireEvent.click(screen.getByRole('button', { name: `Edit ${kind} spare` }));
            await act(async () => request.resolve({ data: {} }));
            expect(screen.getByRole('dialog')).toBeInTheDocument();
            expect(screen.getByRole('button', { name: 'Save Changes' })).toBeEnabled();
            fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
            await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        });

        it('holds the deletion guard through confirmation, releases on cancellation, then deletes once', async () => {
            const confirmation = deferred();
            mocks.confirm.mockReturnValueOnce(confirmation.promise);
            const request = deferred();
            vi.mocked(axios.post).mockReturnValue(request.promise);
            render(<Page />);
            await act(async () => undefined);
            const remove = screen.getByRole('button', { name: `Delete ${kind} spare` });
            act(() => { fireEvent.click(remove); fireEvent.click(remove); });
            expect(mocks.confirm).toHaveBeenCalledTimes(1);
            expect(axios.post).not.toHaveBeenCalled();
            await act(async () => confirmation.resolve(false));
            expect(remove).toBeEnabled();
            fireEvent.click(remove);
            await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
            expect(remove).toBeDisabled();
            expect(remove.querySelector('.animate-spin')).not.toBeNull();
            fireEvent.click(remove);
            expect(axios.post).toHaveBeenCalledTimes(1);
            const sent = yaml.load((vi.mocked(axios.post).mock.calls[0][1] as { content: string }).content) as TestConfig;
            expect(sent[`${kind}s`].spare).toBeUndefined();
            if (kind === 'profile') expect(sent.contexts.demo.profile).toBeUndefined();
            await act(async () => request.resolve({ data: {} }));
            expect(screen.queryByRole('button', { name: `Delete ${kind} spare` })).not.toBeInTheDocument();
        });

        it('recovers from a rejected delete without removing the item and allows retry', async () => {
            const request = deferred();
            vi.mocked(axios.post).mockReturnValueOnce(request.promise).mockResolvedValue({ data: {} });
            const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
            render(<Page />);
            await act(async () => undefined);
            const remove = screen.getByRole('button', { name: `Delete ${kind} spare` });
            fireEvent.click(remove);
            await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
            await act(async () => request.reject(new Error('delete failed')));
            expect(remove).toBeEnabled();
            expect(mocks.error).toHaveBeenCalled();
            fireEvent.click(remove);
            await waitFor(() => expect(screen.queryByRole('button', { name: `Delete ${kind} spare` })).not.toBeInTheDocument());
            expect(axios.post).toHaveBeenCalledTimes(2);
            consoleError.mockRestore();
        });
    });
}

it('keeps the profile modal open after failed deletion, then closes it after a successful retry', async () => {
    const request = deferred();
    vi.mocked(axios.post).mockReturnValueOnce(request.promise).mockResolvedValue({ data: {} });
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(<ProfilesPage />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole('button', { name: 'Edit profile spare' }));
    const dialog = screen.getByRole('dialog');
    const remove = within(dialog).getByRole('button', { name: 'Delete' });
    fireEvent.click(remove);
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(remove).toBeDisabled();
    expect(remove.querySelector('.animate-spin')).not.toBeNull();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(dialog).toBeInTheDocument();
    await act(async () => request.reject(new Error('failed')));
    expect(remove).toBeEnabled();
    fireEvent.click(remove);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    consoleError.mockRestore();
});

it('preserves the active pipeline deletion guard', async () => {
    render(<PipelinesPage />);
    await act(async () => undefined);
    fireEvent.click(screen.getByRole('button', { name: 'Delete pipeline active' }));
    await waitFor(() => expect(mocks.error).toHaveBeenCalledWith('Cannot delete pipeline "active"', expect.any(Object)));
    expect(mocks.confirm).not.toHaveBeenCalled();
    expect(axios.post).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Delete pipeline active' })).toBeEnabled();
});
