// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import Wizard from './Wizard';

vi.mock('axios');
vi.mock('../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: vi.fn() }) }));
const saved = { provider: 'openai_realtime', openai_key: 'synthetic-key', asterisk_host: '127.0.0.1', asterisk_username: 'test', asterisk_password: 'synthetic-password', asterisk_app: 'custom-app' };
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
        await screen.findByDisplayValue('synthetic-key');
        fireEvent.click(screen.getByText('Next'));
        await screen.findByLabelText('Deployment topology');
        fireEvent.change(screen.getByLabelText('Deployment topology'), { target: { value: 'remote' } });
        fireEvent.change(screen.getByLabelText('AVA media IPv4 address'), { target: { value: '192.0.2.20' } });
        fireEvent.change(screen.getByDisplayValue('127.0.0.1'), { target: { value: '192.0.2.10' } });
        fireEvent.click(screen.getByText('Finish Setup'));
        expect(await screen.findByText('Configuration saved')).toBeTruthy();
        expect(await screen.findByDisplayValue(/AI_AGENT=receptionist/)).toBeTruthy();
        expect(screen.queryByText('Setup Complete!')).toBeNull();
        expect(axios.post).toHaveBeenCalledWith('/api/wizard/save', expect.objectContaining({ deployment_topology: 'remote', media_advertise_host: '192.0.2.20', asterisk_app: 'custom-app' }));
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
