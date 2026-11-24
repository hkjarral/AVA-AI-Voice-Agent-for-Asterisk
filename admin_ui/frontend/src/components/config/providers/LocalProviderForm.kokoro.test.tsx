// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import axios from 'axios';
import LocalProviderForm from './LocalProviderForm';

vi.mock('axios', () => ({ default: { get: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function show(config: Record<string, unknown>) {
  vi.mocked(axios.get).mockImplementation(async (url: string) => ({ data: url.includes('/models') ? { tts: { kokoro: [{ id: 'kokoro_82m', voice_files: { af_heart: 'af_heart.pt', jf_alpha: 'jf_alpha.pt' } }] }, stt: {}, llm: [] } : {} }));
  const onChange = vi.fn();
  render(<MemoryRouter><LocalProviderForm config={{ ...config, capabilities: ['tts'] }} onChange={onChange} /></MemoryRouter>);
  return onChange;
}

describe('Kokoro provider controls', () => {
  it('shows global voice and language without offering an ineffective provider edit', async () => {
    const onChange = show({ tts_backend: 'kokoro', kokoro_voice: 'af_heart', kokoro_lang: 'a' });
    const japanese = await screen.findByRole('option', { name: 'jf_alpha' });
    expect(japanese.closest('select')?.disabled).toBe(true);
    expect((screen.getByLabelText('Kokoro Language') as HTMLSelectElement).disabled).toBe(true);
    expect(screen.getByRole('link', { name: 'System → Environment' }).getAttribute('href')).toBe('/env#local-ai');
    expect(onChange).not.toHaveBeenCalled();
  });
  it('renders existing custom hints without overwriting them on unrelated changes', async () => {
    const onChange = show({ tts_backend: 'kokoro', kokoro_voice: 'custom-id', kokoro_lang: 'p' });
    expect(await screen.findByRole('option', { name: 'custom-id (custom/current)' })).toBeTruthy();
    const socket = screen.getByDisplayValue('${LOCAL_WS_URL:-ws://local_ai_server:8765}');
    fireEvent.change(socket, { target: { value: 'ws://new-local:8765' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ kokoro_voice: 'custom-id', kokoro_lang: 'p' }));
  });
});
