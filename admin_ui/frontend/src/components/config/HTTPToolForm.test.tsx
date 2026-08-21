// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import HTTPToolForm from './HTTPToolForm';

vi.mock('../../auth/AuthContext', () => ({
    useAuth: () => ({ token: 'test-token' }),
}));

vi.mock('../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn() }),
}));

vi.mock('axios', () => ({
    default: {
        get: vi.fn().mockResolvedValue({ data: { providers: [] } }),
        post: vi.fn(),
    },
}));

const renderForm = (phase: 'pre_call' | 'in_call' | 'post_call') =>
    render(<HTTPToolForm config={{}} onChange={vi.fn()} phase={phase} />);

const expectThemeAwareControl = (control: HTMLElement) => {
    expect(control).toHaveClass('bg-background');
    expect(control).toHaveClass('text-foreground');
    expect(control).toHaveClass('caret-foreground');
    expect(control).toHaveClass('placeholder:text-muted-foreground');
};

describe('HTTPToolForm editor colors', () => {
    it('styles pre-call header, query, output, and body controls for dark mode', () => {
        renderForm('pre_call');
        fireEvent.click(screen.getByRole('button', { name: 'Add Lookup' }));

        expectThemeAwareControl(screen.getByPlaceholderText('Header name'));
        expectThemeAwareControl(screen.getByPlaceholderText('Value (use ${VAR} for secrets)'));
        expectThemeAwareControl(screen.getByPlaceholderText('Parameter name (e.g., phone)'));
        expectThemeAwareControl(screen.getByPlaceholderText('Value (e.g., {caller_number})'));
        expectThemeAwareControl(screen.getByPlaceholderText('Variable name (e.g., customer_name)'));
        expectThemeAwareControl(screen.getByPlaceholderText('JSON path (e.g., contact.name)'));

        fireEvent.change(screen.getByLabelText('Method'), { target: { value: 'POST' } });
        expectThemeAwareControl(
            screen.getByPlaceholderText('{"phone": "{caller_number}", "context": "{context_name}"}')
        );
    });

    it('starts GET lookups without a JSON header and clears a hidden body on method change', () => {
        renderForm('pre_call');
        fireEvent.click(screen.getByRole('button', { name: 'Add Lookup' }));

        expect(screen.queryByText(/Content-Type: application\/json/)).not.toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Method'), { target: { value: 'POST' } });
        expect(screen.getByText(/Content-Type: application\/json/)).toBeInTheDocument();
        const body = screen.getByPlaceholderText(
            '{"phone": "{caller_number}", "context": "{context_name}"}'
        );
        fireEvent.change(body, { target: { value: '{"stale":true}' } });
        fireEvent.change(screen.getByLabelText('Method'), { target: { value: 'GET' } });
        expect(screen.queryByDisplayValue('{"stale":true}')).not.toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('Method'), { target: { value: 'POST' } });
        expect(screen.getByPlaceholderText(
            '{"phone": "{caller_number}", "context": "{context_name}"}'
        )).toHaveValue('');
    });

    it('preserves a configured body when switching from POST to DELETE', () => {
        renderForm('pre_call');
        fireEvent.click(screen.getByRole('button', { name: 'Add Lookup' }));
        fireEvent.change(screen.getByLabelText('Method'), { target: { value: 'POST' } });
        const body = screen.getByPlaceholderText(
            '{"phone": "{caller_number}", "context": "{context_name}"}'
        );
        fireEvent.change(body, { target: { value: '{"delete":true}' } });
        fireEvent.change(screen.getByLabelText('Method'), { target: { value: 'DELETE' } });

        expect(screen.getByDisplayValue('{"delete":true}')).toBeInTheDocument();
    });

    it('styles in-call parameter, query, output, body, and description controls', () => {
        renderForm('in_call');
        fireEvent.click(screen.getByRole('button', { name: 'Add Tool' }));

        expectThemeAwareControl(screen.getByPlaceholderText('Header name'));
        expectThemeAwareControl(
            screen.getByPlaceholderText(
                /Describe what this tool does and when the AI should use it/
            )
        );
        expectThemeAwareControl(screen.getByPlaceholderText('Parameter name'));
        expectThemeAwareControl(screen.getByPlaceholderText('Value (e.g., {date})'));
        expectThemeAwareControl(screen.getByPlaceholderText('Variable name (e.g., available)'));
        expectThemeAwareControl(screen.getByPlaceholderText('JSON path (e.g., data.available)'));
        expectThemeAwareControl(
            screen.getByPlaceholderText(
                '{"caller": "{caller_number}", "date": "{date}", "time": "{time}"}'
            )
        );

        fireEvent.click(screen.getByRole('button', { name: 'Add Parameter' }));
        expectThemeAwareControl(screen.getByPlaceholderText('Name'));
        expectThemeAwareControl(screen.getByPlaceholderText('Description for AI'));
        expectThemeAwareControl(screen.getByDisplayValue('string'));
    });

    it('styles shared headers and the post-call payload control', () => {
        renderForm('post_call');
        fireEvent.click(screen.getByRole('button', { name: 'Add Webhook' }));

        expectThemeAwareControl(screen.getByPlaceholderText('Header name'));
        expectThemeAwareControl(screen.getByPlaceholderText('Value (use ${VAR} for secrets)'));
        const payload = screen.getByRole('dialog').querySelector('textarea');
        expect(payload).not.toBeNull();
        expectThemeAwareControl(payload!);
        expect((payload as HTMLTextAreaElement).value).toContain('"summary": {summary_json}');
    });

    it('surfaces configured LLM selection, timeout, and editable prompt', async () => {
        vi.mocked(axios.get).mockResolvedValueOnce({
            data: {
                providers: [
                    {
                        key: 'deepseek_llm',
                        label: 'DeepSeek',
                        type: 'openai',
                        model: 'deepseek-chat',
                        credential_required: true,
                        credential_configured: true,
                        ready: true,
                    },
                ],
            },
        });
        renderForm('post_call');
        fireEvent.click(screen.getByRole('button', { name: 'Add Webhook' }));
        fireEvent.click(screen.getByLabelText('Generate AI Summary'));

        await waitFor(() =>
            expect(screen.getByLabelText('Summary Provider')).toHaveTextContent(
                'DeepSeek — deepseek-chat'
            )
        );
        expect(screen.getByLabelText('Max Summary Words')).toHaveValue(100);
        expect(screen.getByLabelText('Summary Timeout (ms)')).toHaveValue(15000);
        const prompt = screen.getByLabelText('Summary Prompt');
        expect((prompt as HTMLTextAreaElement).value).toContain('{max_words}');

        fireEvent.change(prompt, { target: { value: 'Return a {max_words}-word CRM note.' } });
        expect(prompt).toHaveValue('Return a {max_words}-word CRM note.');
        fireEvent.click(screen.getByRole('button', { name: 'Reset to recommended' }));
        expect((prompt as HTMLTextAreaElement).value).toContain("caller's main request");
    });

    it('allows unrelated saves for an unchanged legacy summary configuration', () => {
        const onChange = vi.fn();
        const legacyConfig = {
            legacy_hook: {
                kind: 'generic_webhook',
                phase: 'post_call',
                enabled: true,
                is_global: true,
                url: 'https://hooks.example.com/original',
                method: 'POST',
                generate_summary: true,
                summary_max_words: 100,
                summary_timeout_ms: 15000,
                summary_prompt: 'Summarize in {max_words} words.',
            },
        };
        render(<HTTPToolForm config={legacyConfig} onChange={onChange} phase="post_call" />);

        fireEvent.click(screen.getByRole('button', { name: 'Edit legacy_hook' }));
        fireEvent.change(screen.getByLabelText('URL'), {
            target: { value: 'https://hooks.example.com/updated' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));

        expect(onChange).toHaveBeenCalledOnce();
        expect(onChange.mock.calls[0][0].legacy_hook.url).toBe(
            'https://hooks.example.com/updated'
        );
        expect(onChange.mock.calls[0][0].legacy_hook.summary_provider).toBeUndefined();
    });

    it('requires a provider when legacy summary settings are changed', () => {
        const onChange = vi.fn();
        const legacyConfig = {
            legacy_hook: {
                kind: 'generic_webhook',
                phase: 'post_call',
                enabled: true,
                is_global: true,
                url: 'https://hooks.example.com/original',
                method: 'POST',
                generate_summary: true,
                summary_max_words: 100,
                summary_timeout_ms: 15000,
                summary_prompt: 'Summarize in {max_words} words.',
            },
        };
        render(<HTTPToolForm config={legacyConfig} onChange={onChange} phase="post_call" />);

        fireEvent.click(screen.getByRole('button', { name: 'Edit legacy_hook' }));
        fireEvent.change(screen.getByLabelText('Max Summary Words'), { target: { value: '120' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));

        expect(onChange).not.toHaveBeenCalled();
    });
});
