// @vitest-environment jsdom
import { act, fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { CallRecordingPlayer } from './CallRecordingPlayer';

vi.mock('axios');
const info = { has_recording: true, filename: 'synthetic.ulaw', file_size_bytes: 160, duration_hint: null };
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; };
let player: HTMLAudioElement;

beforeEach(() => {
    vi.mocked(axios.get).mockReset();
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/recording') ? info : new Blob(['synthetic']) }));
    player = document.createElement('audio');
    vi.spyOn(player, 'play').mockResolvedValue(undefined);
    vi.spyOn(player, 'pause').mockImplementation(() => undefined);
    vi.stubGlobal('Audio', vi.fn(function () { return player; }));
    vi.stubGlobal('URL', class extends URL {
        static createObjectURL = vi.fn(() => 'blob:synthetic');
        static revokeObjectURL = vi.fn();
    });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('recording access lifecycle', () => {
    it('distinguishes metadata failure from missing and allows retry for a late file', async () => {
        vi.mocked(axios.get).mockRejectedValueOnce({ response: { data: { detail: 'Multiple recordings match this call' } } });
        render(<CallRecordingPlayer recordId="one" />);
        expect(await screen.findByRole('alert')).toHaveTextContent('Multiple recordings');
        expect(screen.queryByText('No recording available')).not.toBeInTheDocument();
        vi.mocked(axios.get).mockResolvedValueOnce({ data: { has_recording: false } });
        fireEvent.click(screen.getByText('Refresh recording'));
        expect(await screen.findByText('No recording available')).toBeInTheDocument();
        fireEvent.click(screen.getByText('Refresh recording'));
        expect(await screen.findByRole('button', { name: 'Play recording' })).toBeEnabled();
    });

    it('ignores stale metadata after switching calls', async () => {
        const first = deferred<{ data: typeof info }>();
        vi.mocked(axios.get).mockReturnValueOnce(first.promise);
        const view = render(<CallRecordingPlayer recordId="one" />);
        const signal = vi.mocked(axios.get).mock.calls[0][1]?.signal;
        view.rerender(<CallRecordingPlayer recordId="two" />);
        await screen.findByRole('button', { name: 'Play recording' });
        await act(async () => first.resolve({ data: { ...info, filename: 'stale.wav' } }));
        expect(signal?.aborted).toBe(true);
        expect(screen.queryByText(/stale.wav/)).not.toBeInTheDocument();
    });

    it('cancels pending audio on close without creating a URL or playing', async () => {
        const pending = deferred<{ data: Blob }>();
        const view = render(<CallRecordingPlayer recordId="one" />);
        await screen.findByRole('button', { name: 'Play recording' });
        vi.mocked(axios.get).mockReturnValueOnce(pending.promise);
        fireEvent.click(screen.getByRole('button', { name: 'Play recording' }));
        expect(screen.getByRole('button', { name: 'Download recording' })).toBeDisabled();
        const calls = vi.mocked(axios.get).mock.calls;
        const signal = calls[calls.length - 1]?.[1]?.signal;
        view.unmount();
        await act(async () => pending.resolve({ data: new Blob() }));
        expect(signal?.aborted).toBe(true);
        expect(URL.createObjectURL).not.toHaveBeenCalled();
        expect(player.play).not.toHaveBeenCalled();
    });

    it('stops playback and releases URLs when changing calls', async () => {
        const view = render(<CallRecordingPlayer recordId="one" />);
        fireEvent.click(await screen.findByRole('button', { name: 'Play recording' }));
        await screen.findByRole('button', { name: 'Pause' });
        view.rerender(<CallRecordingPlayer recordId="two" />);
        expect(player.pause).toHaveBeenCalled();
        expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic');
    });

    it('downloads a WAV with authenticated axios and releases its URL', async () => {
        const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
            expect(this.download).toBe('synthetic.wav');
            expect(this.href).toBe('blob:synthetic');
        });
        const view = render(<CallRecordingPlayer recordId="one" />);
        fireEvent.click(await screen.findByRole('button', { name: 'Download recording' }));
        await waitFor(() => expect(click).toHaveBeenCalledOnce());
        expect(axios.get).toHaveBeenLastCalledWith('/api/calls/one/recording/download', expect.objectContaining({ responseType: 'blob', signal: expect.any(AbortSignal) }));
        view.unmount();
        expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic');
    });

    it('shows a download error and re-enables retry', async () => {
        render(<CallRecordingPlayer recordId="one" />);
        const button = await screen.findByRole('button', { name: 'Download recording' });
        vi.mocked(axios.get).mockRejectedValueOnce({ response: { data: { detail: 'Recording file not found' } } });
        fireEvent.click(button);
        expect(await screen.findByRole('alert')).toHaveTextContent('Recording file not found');
        expect(button).toBeEnabled();
    });
});
