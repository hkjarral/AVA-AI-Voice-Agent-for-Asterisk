// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor, within, act } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import yaml from 'js-yaml';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ProvidersPage from './ProvidersPage';

const mocks = vi.hoisted(() => ({ config: {} as any, load: vi.fn() }));
vi.mock('axios');
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() } }));
vi.mock('../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: vi.fn().mockResolvedValue(true) }) }));
vi.mock('../hooks/useRestartRequired', () => ({ useRestartRequired: () => ({ restartRequired: false, refetch: vi.fn() }) }));
vi.mock('../utils/configCache', () => ({ getCachedConfig: () => ({ config: mocks.config, yamlError: null }), loadConfigYaml: mocks.load }));

const originalUrl = 'http://127.0.0.1:8088/custom/v2';
const editedUrl = 'http://192.168.10.10:9090/custom/v3';

async function openEditor() {
    render(<MemoryRouter><ProvidersPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Settings for custom_llm', exact: true }));
    return screen.findByRole('dialog', { name: 'Edit Provider: custom_llm' });
}

describe('provider connection results', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocks.config = { providers: { custom_llm: { type: 'openai', capabilities: ['llm'], enabled: false, chat_base_url: originalUrl, chat_model: 'Qwen3-8B', api_key: 'not-needed' } } };
        mocks.load.mockImplementation(async () => ({ config: mocks.config, yamlError: null }));
        vi.mocked(axios.get).mockResolvedValue({ data: {} });
        vi.mocked(axios.post).mockResolvedValue({ data: { success: true, message: 'Connected at http://127.0.0.1:8088; inference was not tested' } });
    });

    it('tests unsaved URL edits, displays details, and clears them after another edit', async () => {
        const dialog = await openEditor();
        fireEvent.change(within(dialog).getByDisplayValue(originalUrl), { target: { value: editedUrl } });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: { success: false, message: 'Models probe failed (HTTP 401) at http://192.168.10.10:9090' } });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Test Connection' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/config/providers/test', {
            name: 'custom_llm', config: expect.objectContaining({ chat_base_url: editedUrl, api_key: 'not-needed' }),
        }));
        expect(await within(dialog).findByRole('status')).toHaveTextContent('HTTP 401');
        expect(within(dialog).getByRole('status')).toHaveTextContent('http://192.168.10.10:9090');
        fireEvent.change(within(dialog).getByDisplayValue(editedUrl), { target: { value: originalUrl } });
        expect(within(dialog).queryByRole('status')).not.toBeInTheDocument();
    });

    it('ignores a test response after the URL changed during the request', async () => {
        const dialog = await openEditor();
        let resolve: (value: any) => void = () => undefined;
        vi.mocked(axios.post).mockReturnValueOnce(new Promise(r => { resolve = r; }));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Test Connection' }));
        fireEvent.change(within(dialog).getByDisplayValue(originalUrl), { target: { value: editedUrl } });
        await act(async () => resolve({ data: { success: true, message: 'Obsolete success' } }));
        expect(within(dialog).queryByRole('status')).not.toBeInTheDocument();
        expect(screen.queryByText('Obsolete success')).not.toBeInTheDocument();
        expect(within(dialog).getByRole('button', { name: 'Test Connection' })).toBeEnabled();
    });

    it('does not publish a late result after closing the editor', async () => {
        const dialog = await openEditor();
        let resolve: (value: any) => void = () => undefined;
        vi.mocked(axios.post).mockReturnValueOnce(new Promise(r => { resolve = r; }));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Test Connection' }));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
        await act(async () => resolve({ data: { success: true, message: 'Obsolete success' } }));
        expect(screen.queryByText('Obsolete success')).not.toBeInTheDocument();
    });

    it('does not reuse a pending result after closing and reopening the same editor', async () => {
        const dialog = await openEditor();
        let resolve: (value: unknown) => void = () => undefined;
        vi.mocked(axios.post).mockReturnValueOnce(new Promise(r => { resolve = r; }));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Test Connection' }));
        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
        fireEvent.click(screen.getByRole('button', { name: 'Settings for custom_llm', exact: true }));
        const reopened = await screen.findByRole('dialog', { name: 'Edit Provider: custom_llm' });
        await act(async () => resolve({ data: { success: true, message: 'Obsolete reopened success' } }));
        expect(within(reopened).queryByRole('status')).not.toBeInTheDocument();
        expect(screen.queryByText('Obsolete reopened success')).not.toBeInTheDocument();
        expect(within(reopened).getByRole('button', { name: 'Test Connection' })).toBeEnabled();
    });

    it('keeps concurrent card requests, loading states and results independent', async () => {
        mocks.config.providers.other_llm = { ...mocks.config.providers.custom_llm, chat_base_url: editedUrl };
        let resolveFirst: (value: unknown) => void = () => undefined;
        let rejectSecond: (value: unknown) => void = () => undefined;
        vi.mocked(axios.post)
            .mockReturnValueOnce(new Promise(r => { resolveFirst = r; }))
            .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectSecond = reject; }));
        render(<MemoryRouter><ProvidersPage /></MemoryRouter>);
        const first = await screen.findByRole('button', { name: 'Test connection for custom_llm', exact: true });
        const second = screen.getByRole('button', { name: 'Test connection for other_llm', exact: true });
        fireEvent.click(first);
        fireEvent.click(second);
        expect(first).toBeDisabled();
        expect(second).toBeDisabled();
        await act(async () => rejectSecond({ response: { data: { detail: 'Other endpoint unavailable' } } }));
        expect(first).toBeDisabled();
        expect(second).toBeEnabled();
        expect(screen.getByText('Other endpoint unavailable')).toBeInTheDocument();
        await act(async () => resolveFirst({ data: { success: true, message: 'First endpoint connected' } }));
        expect(first).toBeEnabled();
        expect(screen.getByText('First endpoint connected')).toBeInTheDocument();
        expect(screen.getByText('Other endpoint unavailable')).toBeInTheDocument();
    });

    it('preserves a card request when another provider editor opens', async () => {
        mocks.config.providers.other_llm = { ...mocks.config.providers.custom_llm, chat_base_url: editedUrl };
        let resolve: (value: unknown) => void = () => undefined;
        vi.mocked(axios.post).mockReturnValueOnce(new Promise(r => { resolve = r; }));
        render(<MemoryRouter><ProvidersPage /></MemoryRouter>);
        fireEvent.click(await screen.findByRole('button', { name: 'Test connection for custom_llm', exact: true }));
        fireEvent.click(screen.getByRole('button', { name: 'Settings for other_llm', exact: true }));
        const dialog = await screen.findByRole('dialog', { name: 'Edit Provider: other_llm' });
        expect(within(dialog).getByRole('button', { name: 'Test Connection' })).toBeEnabled();
        await act(async () => resolve({ data: { success: true, message: 'Card connected independently' } }));
        expect(within(dialog).queryByRole('status')).not.toBeInTheDocument();
        fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
        expect(screen.getByText('Card connected independently')).toBeInTheDocument();
    });

    it('renders structured backend errors as readable messages', async () => {
        const dialog = await openEditor();
        vi.mocked(axios.post).mockRejectedValueOnce({ response: { data: { detail: { message: 'Blocked metadata destination' } } } });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Test Connection' }));
        expect(await within(dialog).findByRole('status')).toHaveTextContent('Blocked metadata destination');
    });

    it('preserves an edited endpoint through save and reopen', async () => {
        const dialog = await openEditor();
        fireEvent.change(within(dialog).getByDisplayValue(originalUrl), { target: { value: editedUrl } });
        vi.mocked(axios.post).mockImplementation(async (url, body: any) => {
            if (url === '/api/config/yaml') mocks.config = yaml.load(body.content);
            return { data: {} };
        });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save Changes' }));
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(mocks.config.providers.custom_llm.chat_base_url).toBe(editedUrl);
        fireEvent.click(screen.getByRole('button', { name: 'Settings for custom_llm', exact: true }));
        const reopened = await screen.findByRole('dialog', { name: 'Edit Provider: custom_llm' });
        expect(within(reopened).getByDisplayValue(editedUrl)).toBeInTheDocument();
    });
});
