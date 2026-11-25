// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import Wizard from './Wizard';

vi.mock('axios');
vi.mock('../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: vi.fn() }) }));
const saved = { provider: 'openai_realtime', openai_key: { set: true }, asterisk_host: '127.0.0.1', asterisk_username: 'test', asterisk_password: { set: true }, asterisk_app: 'custom-app' };
describe('Fresh setup journey', () => {
    beforeEach(() => {
        vi.resetAllMocks();
        sessionStorage.clear();
        vi.mocked(axios.get).mockImplementation(async (url) => ({ data:
            url === '/api/wizard/load-config' ? saved :
            url === '/api/wizard/engine-status' ? { running: false, exists: false } :
            url === '/api/wizard/readiness' ? { stage: 'configuration_saved', checks: {}, ready_for_test: false, agent_slug: 'receptionist', destination: 'from-ai-agent,s,1', dialplan: 'Set(AI_AGENT=receptionist)\nStasis(custom-app)' } :
            { catalog: { stt: [], tts: [], llm: [] }, languages: {} }
        }));
        vi.mocked(axios.post).mockResolvedValue({ data: { valid: true, status: 'success' } });
    });
    it('validates a key, saves the selected topology, then offers engine start and first-call instructions', async () => {
        render(<MemoryRouter><Wizard /></MemoryRouter>);
        await waitFor(() => expect(axios.get).toHaveBeenCalledWith('/api/wizard/load-config'));
        fireEvent.click(screen.getByText('Next'));
        fireEvent.click(screen.getByText('Next'));
        await screen.findByText('Set');
        fireEvent.click(screen.getByText('Next'));
        await screen.findByLabelText('Deployment topology');
        fireEvent.click(screen.getByRole('button', { name: 'Test Connection' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/wizard/validate-connection', expect.objectContaining({ username: 'test' })));
        expect(vi.mocked(axios.post).mock.calls.find(([url]) => url.endsWith('/validate-connection'))?.[1]).not.toHaveProperty('password');
        fireEvent.change(screen.getByLabelText('Deployment topology'), { target: { value: 'remote' } });
        fireEvent.change(screen.getByLabelText('AVA media IPv4 address'), { target: { value: '192.0.2.20' } });
        fireEvent.change(screen.getByDisplayValue('127.0.0.1'), { target: { value: '192.0.2.10' } });
        fireEvent.click(screen.getByText('Finish Setup'));
        expect(await screen.findByText('Configuration saved')).toBeTruthy();
        expect(axios.post).toHaveBeenCalledWith('/api/wizard/validate-key', { provider: 'openai' });
        const posted = vi.mocked(axios.post).mock.calls.find(([path]) => path === '/api/wizard/save')?.[1];
        expect(posted).not.toHaveProperty('openai_key');
        expect(posted).not.toHaveProperty('asterisk_password');
        expect(await screen.findByDisplayValue(/AI_AGENT=receptionist/)).toBeTruthy();
        expect(screen.queryByText('Setup Complete!')).toBeNull();
        expect(axios.post).toHaveBeenCalledWith('/api/wizard/save', expect.objectContaining({ deployment_topology: 'remote', media_advertise_host: '192.0.2.20', asterisk_app: 'custom-app' }));
    });
    it('keeps a password edited while the previous value is being saved', async () => {
        let completeSave!: () => void;
        const pending = new Promise<void>(resolve => { completeSave = resolve; });
        vi.mocked(axios.post).mockImplementation(async url => {
            if (url === '/api/wizard/save') await pending;
            return { data: { valid: true, status: 'success' } };
        });
        render(<MemoryRouter><Wizard /></MemoryRouter>);
        await waitFor(() => expect(axios.get).toHaveBeenCalledWith('/api/wizard/load-config'));
        fireEvent.click(screen.getByText('Next'));
        fireEvent.click(screen.getByText('Next'));
        await screen.findByText('Set');
        fireEvent.click(screen.getByText('Next'));
        fireEvent.click(await screen.findByRole('button', { name: 'Replace ARI Password' }));
        const password = screen.getByLabelText('ARI Password');
        fireEvent.change(password, { target: { value: 'first-replacement' } });
        fireEvent.click(screen.getByText('Finish Setup'));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/wizard/save',
            expect.objectContaining({ asterisk_password: 'first-replacement' })));
        fireEvent.change(password, { target: { value: 'later-replacement' } });
        completeSave();
        fireEvent.click(await screen.findByText('Edit setup settings'));
        expect(await screen.findByLabelText('ARI Password')).toHaveValue('later-replacement');
    });
    it('does not validate an unset key and sends only a newly entered replacement', async () => {
        vi.mocked(axios.get).mockResolvedValueOnce({ data: { ...saved, openai_key: { set: false } } });
        render(<MemoryRouter><Wizard /></MemoryRouter>);
        await waitFor(() => expect(axios.get).toHaveBeenCalledWith('/api/wizard/load-config'));
        fireEvent.click(screen.getByText('Next'));
        fireEvent.click(screen.getByText('Next'));
        await screen.findByText('Not set');
        fireEvent.click(screen.getByText('Next'));
        expect(vi.mocked(axios.post).mock.calls.some(([url]) => url.endsWith('/validate-key'))).toBe(false);
        fireEvent.click(screen.getByRole('button', { name: 'Replace OpenAI API Key' }));
        const replacement = 'synthetic-replacement';
        fireEvent.change(screen.getByLabelText('OpenAI API Key'), { target: { value: replacement } });
        fireEvent.click(screen.getByText('Next'));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/wizard/validate-key', { provider: 'openai', api_key: replacement }));
    });
    it('resumes a saved installation at the handoff', async () => {
        vi.mocked(axios.get).mockResolvedValueOnce({ data: { ...saved, onboarding: { stage: 'configuration_saved' } } });
        render(<MemoryRouter><Wizard /></MemoryRouter>);
        expect(await screen.findByText('Configuration saved')).toBeTruthy();
        expect(await screen.findByRole('button', { name: 'Confirm successful test call' })).toBeDisabled();
    });
    it('does not claim the engine is running when a start request times out and the container has stopped', async () => {
        vi.mocked(axios.get).mockResolvedValueOnce({ data: { ...saved, onboarding: { stage: 'configuration_saved' } } });
        vi.mocked(axios.post).mockResolvedValueOnce({ data: { started: true, success: false, message: 'Readiness timed out' } });
        render(<MemoryRouter><Wizard /></MemoryRouter>);
        fireEvent.click(await screen.findByRole('button', { name: 'Start AI Engine' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/wizard/start-engine'));
        await waitFor(() => expect(screen.getByRole('button', { name: 'Start AI Engine' })).toBeEnabled());
        expect(screen.queryByText('AI Engine is running')).toBeNull();
        expect(screen.getByRole('button', { name: 'Confirm successful test call' })).toBeDisabled();
    });
    it('blocks saving when existing settings cannot be loaded', async () => {
        vi.mocked(axios.get).mockRejectedValueOnce(new Error('unavailable'));
        render(<MemoryRouter><Wizard /></MemoryRouter>);
        await screen.findByRole('alert');
        fireEvent.click(screen.getByText('Next'));
        expect(screen.getByText('Welcome to the Asterisk AI Voice Agent setup.')).toBeTruthy();
        expect(axios.post).not.toHaveBeenCalled();
    });
});
