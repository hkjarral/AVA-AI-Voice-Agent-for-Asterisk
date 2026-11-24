// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import EnvPage from './EnvPage';

vi.mock('axios');
vi.mock('../../auth/AuthContext', () => ({ useAuth: () => ({ token: 'test-token', loading: false }) }));
vi.mock('../../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: vi.fn().mockResolvedValue(false) }) }));

let savedEnv: Record<string, string>;
beforeEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, '', '/env#local-ai');
  savedEnv = { LOCAL_TTS_BACKEND: 'kokoro', KOKORO_MODE: 'local', KOKORO_VOICE: 'af_heart', KOKORO_LANG: 'a' };
  vi.mocked(axios.get).mockImplementation(async (url: string) => {
    if (url === '/api/config/env') return { data: { ...savedEnv } };
    if (url === '/api/config/env/status') return { data: { apply_plan: [], pending_restart: false } };
    if (url === '/api/config/yaml') return { data: { providers: {} } };
    if (url === '/api/local-ai/capabilities') return { data: { tts: { kokoro: { available: true } } } };
    return { data: {} };
  });
  vi.mocked(axios.post).mockImplementation(async (url: string, payload: unknown) => {
    if (url === '/api/config/env') savedEnv = { ...savedEnv, ...payload as Record<string, string> };
    return { data: { changed_keys: ['KOKORO_VOICE', 'KOKORO_LANG'] } };
  });
});
afterEach(cleanup);

describe('Kokoro environment settings', () => {
  it.each([[undefined, 'j'], ['p', 'p']])('displays the effective language for a stock voice with configured language %s', async (language, expected) => {
    savedEnv = { LOCAL_TTS_BACKEND: 'kokoro', KOKORO_MODE: 'local', KOKORO_VOICE: 'jf_alpha', ...(language ? { KOKORO_LANG: language } : {}) };
    render(<MemoryRouter><EnvPage /></MemoryRouter>);
    await screen.findByRole('option', { name: 'jf_alpha (ja-JP)' });
    await waitFor(() => expect(screen.getByLabelText('Kokoro Language')).toHaveValue(expected));
    expect(axios.post).not.toHaveBeenCalled();
  });
  it('saves and reopens the matching voice and language as one selection', async () => {
    const view = render(<MemoryRouter><EnvPage /></MemoryRouter>);
    const japanese = await screen.findByRole('option', { name: 'jf_alpha (ja-JP)' });
    fireEvent.change(japanese.closest('select')!, { target: { value: 'jf_alpha' } });
    expect(screen.getByLabelText('Kokoro Language')).toHaveValue('j');
    fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/config/env', expect.objectContaining({ KOKORO_VOICE: 'jf_alpha', KOKORO_LANG: 'j' }), expect.anything()));
    view.unmount();
    render(<MemoryRouter><EnvPage /></MemoryRouter>);
    await screen.findByRole('option', { name: 'jf_alpha (ja-JP)' });
    await waitFor(() => expect(screen.getByLabelText('Kokoro Language')).toHaveValue('j'));
    expect((screen.getByRole('option', { name: 'jf_alpha (ja-JP)' }) as HTMLOptionElement).selected).toBe(true);
  });
});
