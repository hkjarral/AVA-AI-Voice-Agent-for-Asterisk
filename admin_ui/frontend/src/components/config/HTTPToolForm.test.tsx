// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import HTTPToolForm from './HTTPToolForm';

vi.mock('../../auth/AuthContext', () => ({
    useAuth: () => ({ token: 'test-token' }),
}));

vi.mock('../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn() }),
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
    });
});
