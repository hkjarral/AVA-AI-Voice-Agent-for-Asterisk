// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import axios from 'axios';
import { expect, it, vi } from 'vitest';
import LLMPage from './LLMPage';

vi.mock('axios');
vi.mock('../../hooks/useRestartRequired', () => ({ useRestartRequired: () => ({ restartRequired: false, refetch: vi.fn() }) }));

it('saves its own environment settings without resending credentials or unrelated settings', async () => {
    vi.mocked(axios.get).mockImplementation(async url => ({ data: url.endsWith('/env') ? {
        LOCAL_LLM_CHAT_FORMAT: 'chatml', LOCAL_LLM_VOICE_PREAMBLE: { set: true }, CUSTOM_SECRET: { set: true }, ASTERISK_HOST: 'example.invalid',
    } : url.endsWith('/yaml') ? { content: '{}' } : {} }));
    vi.mocked(axios.post).mockResolvedValue({ data: {} });
    render(<LLMPage />);
    const save = await screen.findByRole('button', { name: 'Save Changes' });
    expect((screen.getByPlaceholderText(/You are a voice assistant/) as HTMLTextAreaElement).disabled).toBe(true);
    fireEvent.click(save);
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/config/env', { LOCAL_LLM_CHAT_FORMAT: 'chatml' }));
});
