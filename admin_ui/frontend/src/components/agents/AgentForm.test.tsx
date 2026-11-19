// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { StrictMode } from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import AgentForm, { type Agent } from './AgentForm';

vi.mock('axios');
vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
const agent: Agent = {
    slug: 'sales', display_name: 'Sales', provider: 'managed', voice: 'keep-this-voice',
    prompt: 'Keep the prompt', is_active: 1, is_default: 1, is_operator_managed: 0,
    email_enabled: null, tools_json: '["hangup_call"]',
    extra_json: '{"future_setting":{"keep":true},"no_input":{"initial_timeout_sec":42}}',
    tool_configs_json: '{"transfer":{"destination_policy":"selected","destination_keys":["sales"]}}',
};

beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(axios.get).mockImplementation(async url => ({ data: String(url).endsWith('/yaml')
        ? { content: 'providers:\n  managed:\n    type: elevenlabs_agent\n    enabled: true\n' }
        : String(url).endsWith('/meta') ? { providers: [{ name: 'managed', kind: 'elevenlabs_agent', voice_mode: 'platform_managed', voices: [] }] }
        : { tools: [{ name: 'hangup_call', phase: 'in_call', source: 'builtin' }] } }));
    vi.mocked(axios.patch).mockResolvedValue({ data: agent });
});

describe('Agent settings', () => {
    it.each([true, false])('shows loading instead of zero counts until the catalog resolves (has tools: %s)', async hasTools => {
        let complete!: (response: { data: { tools: { name: string; phase: string; source: string }[] } }) => void;
        const pending = new Promise<{ data: { tools: { name: string; phase: string; source: string }[] } }>(resolve => { complete = resolve; });
        const originalGet = vi.mocked(axios.get).getMockImplementation()!;
        vi.mocked(axios.get).mockImplementation((url, config) => String(url).endsWith('/catalog') ? pending : originalGet(url, config));
        render(<AgentForm isOpen agent={agent} initialSection="tools" onClose={vi.fn()} onSaved={vi.fn()} />);
        expect(screen.getByRole('status', { name: 'Loading tools' })).toHaveTextContent('Loading tools and selections');
        expect(screen.queryByText(/0 selected/)).not.toBeInTheDocument();
        expect(screen.queryByText(/No in-call tools available/)).not.toBeInTheDocument();
        await act(async () => complete({ data: { tools: hasTools ? [{ name: 'hangup_call', phase: 'in_call', source: 'builtin' }] : [] } }));
        expect(screen.queryByRole('status', { name: 'Loading tools' })).not.toBeInTheDocument();
        if (hasTools) {
            expect(screen.getByRole('checkbox', { name: /hangup_call/ })).toBeChecked();
            expect(screen.getByRole('button', { name: /In-call.*1 selected/ })).toBeVisible();
        } else {
            expect(screen.getByText('No in-call tools available.')).toBeVisible();
        }
        expect(axios.patch).not.toHaveBeenCalled();
    });

    it('replaces loading with the catalog error without clearing saved selections', async () => {
        let fail!: (error: Error) => void;
        const pending = new Promise<never>((_, reject) => { fail = reject; });
        const originalGet = vi.mocked(axios.get).getMockImplementation()!;
        vi.mocked(axios.get).mockImplementation((url, config) => String(url).endsWith('/catalog') ? pending : originalGet(url, config));
        render(<AgentForm isOpen agent={agent} initialSection="tools" onClose={vi.fn()} onSaved={vi.fn()} />);
        expect(screen.getByRole('status', { name: 'Loading tools' })).toBeVisible();
        await act(async () => fail(new Error('Catalog unavailable')));
        expect(screen.queryByRole('status', { name: 'Loading tools' })).not.toBeInTheDocument();
        expect(screen.getByText(/Couldn.t load the tool catalog/)).toBeVisible();
        expect(screen.queryByText(/0 selected/)).not.toBeInTheDocument();
        await userEvent.click(screen.getByRole('button', { name: 'General' }));
        await userEvent.click(screen.getByRole('button', { name: 'Female' }));
        await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(axios.patch).toHaveBeenCalledWith('/api/agents/sales', { avatar_preset: 'female' }));
    });

    it('saves only appearance, retaining platform voice and all inherited policies', async () => {
        const saved = vi.fn();
        render(<StrictMode><AgentForm isOpen agent={agent} onClose={vi.fn()} onSaved={saved} /></StrictMode>);
        await screen.findByDisplayValue('Sales');
        await userEvent.click(screen.getByRole('button', { name: 'Female' }));
        await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(saved).toHaveBeenCalledOnce());
        expect(axios.patch).toHaveBeenCalledWith('/api/agents/sales', { avatar_preset: 'female' });
    });

    it('keeps hidden fields mounted while changing sections and only sends the edited prompt', async () => {
        render(<AgentForm isOpen agent={agent} onClose={vi.fn()} onSaved={vi.fn()} />);
        await userEvent.click(screen.getByRole('button', { name: 'Conversation' }));
        const prompt = screen.getByLabelText('Prompt');
        await userEvent.clear(prompt); await userEvent.type(prompt, 'Updated instructions');
        await userEvent.click(screen.getByRole('button', { name: 'Advanced' }));
        await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(axios.patch).toHaveBeenCalledWith('/api/agents/sales', { prompt: 'Updated instructions' }));
    });

    it('keeps a failed save editable and does not dismiss the editor', async () => {
        vi.mocked(axios.patch).mockRejectedValue({ response: { data: { detail: 'Image could not be saved' } } });
        const saved = vi.fn();
        render(<AgentForm isOpen agent={agent} onClose={vi.fn()} onSaved={saved} />);
        await userEvent.click(screen.getByRole('button', { name: 'Female' }));
        await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        expect(await screen.findByRole('alert')).toHaveTextContent('Image could not be saved');
        expect(saved).not.toHaveBeenCalled();
        expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled();
    });

    it('can remove a custom image and explicitly clear it on save', async () => {
        render(<AgentForm isOpen agent={{ ...agent, avatar_preset: 'female', avatar_image: 'data:image/png;base64,aA==' }} onClose={vi.fn()} onSaved={vi.fn()} />);
        await userEvent.click(screen.getByRole('button', { name: 'Use default image' }));
        await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
        await waitFor(() => expect(axios.patch).toHaveBeenCalledWith('/api/agents/sales', { avatar_image: null }));
    });
});
