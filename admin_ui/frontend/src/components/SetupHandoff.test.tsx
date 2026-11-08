// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import SetupHandoff from './SetupHandoff';

vi.mock('axios');
const response = (ready: boolean) => ({ data: {
    stage: 'configuration_saved', agent_slug: 'receptionist', app_name: 'custom-app',
    dialplan: '[from-ai-agent]\n same => n,Set(AI_AGENT=receptionist)\n same => n,Stasis(custom-app)',
    destination: 'from-ai-agent,s,1', audio_transport: 'audiosocket', ready_for_test: ready,
    checks: { reachable: true, ready, ari_connected: ready, transport_ok: ready, provider_ok: ready, agent_resolved: true },
} });
describe('First call handoff', () => {
    beforeEach(() => vi.resetAllMocks());
    it('shows complete FreePBX/plain Asterisk instructions without claiming a stopped engine is ready', async () => {
        vi.mocked(axios.get).mockResolvedValue(response(false));
        render(<MemoryRouter><SetupHandoff /></MemoryRouter>);
        expect(await screen.findByDisplayValue(/AI_AGENT=receptionist/)).toBeTruthy();
        expect(screen.getByText(/from-ai-agent,s,1/)).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Confirm successful test call' })).toBeDisabled();
        expect(screen.queryByText(/Onboarding complete/)).toBeNull();
        fireEvent.change(screen.getByLabelText('PBX type'), { target: { value: 'asterisk' } });
        expect(screen.getByText('asterisk -rx "dialplan reload"')).toBeTruthy();
        expect(screen.getByText('Goto(from-ai-agent,s,1)')).toBeTruthy();
    });
    it('requires all call observations and records explicit acceptance', async () => {
        vi.mocked(axios.get).mockResolvedValue(response(true));
        vi.mocked(axios.post).mockResolvedValue({ data: { stage: 'verified' } });
        render(<MemoryRouter><SetupHandoff /></MemoryRouter>);
        await screen.findByDisplayValue(/AI_AGENT=receptionist/);
        const button = screen.getByRole('button', { name: 'Confirm successful test call' });
        expect(button).toBeDisabled();
        screen.getAllByRole('checkbox').forEach(box => fireEvent.click(box));
        fireEvent.click(button);
        expect(await screen.findByText(/First call verified by you/)).toBeTruthy();
        expect(axios.post).toHaveBeenCalledWith('/api/wizard/complete', { greeting_heard: true, two_way_audio: true, clean_hangup: true, history_checked: true });
    });
    it('clears stale readiness after a failed recheck and can recover', async () => {
        vi.mocked(axios.get).mockResolvedValueOnce(response(true)).mockRejectedValueOnce(new Error('offline')).mockResolvedValue(response(false));
        render(<MemoryRouter><SetupHandoff /></MemoryRouter>);
        await screen.findByDisplayValue(/AI_AGENT=receptionist/);
        fireEvent.click(screen.getByText('Recheck readiness'));
        expect(await screen.findByRole('alert')).toHaveTextContent('could not be checked');
        expect(screen.queryByDisplayValue(/AI_AGENT=receptionist/)).toBeNull();
        fireEvent.click(screen.getByText('Recheck readiness'));
        await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    });
});
