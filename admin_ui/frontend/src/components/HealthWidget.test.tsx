// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios, { type AxiosResponse } from 'axios';
import { toast } from 'sonner';
import { HealthWidget } from './HealthWidget';

vi.mock('axios');
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));
const path = '/models/sense-voice';
beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(axios.get).mockImplementation(async url => {
        if (url === '/api/system/health') return { data: {
            ai_engine: { status: 'connected', details: {} },
            local_ai_server: { status: 'connected', details: { models: {
                stt: { backend: 'vosk', path: '/models/vosk', loaded: true },
                tts: { backend: 'piper', path: '/models/piper', loaded: true },
                llm: { path: '/models/llm', loaded: true },
            } } },
        } };
        if (url === '/api/local-ai/models') return { data: {
            stt: { sherpa: [{ name: 'SenseVoice', path, type: 'stt' }] },
            tts: { piper: [{ name: 'Test Piper', path: '/models/piper2', type: 'tts' }] }, llm: [],
        } };
        return { data: { stt: { sherpa: { available: true } }, tts: { piper: { available: true } } } };
    });
});
afterEach(cleanup);

it('sends the displayed defaults when selecting SenseVoice from another backend', async () => {
    vi.mocked(axios.post).mockResolvedValue({ data: { success: false, message: 'verification failed' } });
    render(<MemoryRouter><HealthWidget /></MemoryRouter>);
    const option = await screen.findByRole('option', { name: /SenseVoice/ });
    fireEvent.change(option.closest('select')!, { target: { value: `sherpa:${path}` } });
    expect(screen.getByLabelText('SenseVoice Language')).toHaveValue('auto');
    expect(screen.getByLabelText('Punctuation (ITN)')).toHaveValue('true');
    fireEvent.click(screen.getByRole('button', { name: /Apply & Restart/ }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/local-ai/switch', expect.objectContaining({
        sherpa_sense_voice_language: 'auto', sherpa_sense_voice_use_itn: true,
    })));
});

it('stops a multi-model apply when the first switch reports failure and keeps pending settings', async () => {
    vi.mocked(axios.post).mockResolvedValue({ data: { success: false, message: 'STT failed' } });
    render(<MemoryRouter><HealthWidget /></MemoryRouter>);
    const stt = await screen.findByRole('option', { name: /SenseVoice/ });
    fireEvent.change(stt.closest('select')!, { target: { value: `sherpa:${path}` } });
    const tts = screen.getByRole('option', { name: /Test Piper/ });
    fireEvent.change(tts.closest('select')!, { target: { value: 'piper:/models/piper2' } });
    fireEvent.click(screen.getByRole('button', { name: /Apply & Restart/ }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('STT failed'));
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /Apply & Restart/ })).toBeEnabled();
});


it('forwards explicit SenseVoice settings when selecting a renamed export', async () => {
    const originalGet = vi.mocked(axios.get).getMockImplementation()!;
    vi.mocked(axios.get).mockImplementation(async (url, config) => {
        const result = await originalGet(url, config) as AxiosResponse;
        if (url === '/api/system/health') result.data.local_ai_server.details.models.stt = {
            backend: 'sherpa', path: '/models/current-a', loaded: true, sherpa_model_family: 'sense_voice',
            sherpa_sense_voice_language: 'auto', sherpa_sense_voice_use_itn: true,
        };
        if (url === '/api/local-ai/models') result.data.stt.sherpa[0].path = '/models/custom-b';
        return result;
    });
    vi.mocked(axios.post).mockResolvedValue({ data: { success: false, message: 'test ended' } });
    render(<MemoryRouter><HealthWidget /></MemoryRouter>);
    const option = await screen.findByRole('option', { name: /SenseVoice/ });
    fireEvent.change(option.closest('select')!, { target: { value: 'sherpa:/models/custom-b' } });
    fireEvent.change(screen.getByLabelText('SenseVoice Language'), { target: { value: 'zh' } });
    fireEvent.change(screen.getByLabelText('Punctuation (ITN)'), { target: { value: 'false' } });
    fireEvent.click(screen.getByRole('button', { name: /Apply & Restart/ }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/local-ai/switch', expect.objectContaining({
        model_path: '/models/custom-b', sherpa_sense_voice_language: 'zh', sherpa_sense_voice_use_itn: false,
    })));
});
