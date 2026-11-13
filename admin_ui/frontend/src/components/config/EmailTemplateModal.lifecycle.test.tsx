// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { toast } from 'sonner';
import { EmailTemplateModal } from './EmailTemplateModal';

vi.mock('axios');
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@monaco-editor/react', () => ({
    default: ({ value, onChange, options }: { value: string; onChange: (value: string) => void; options: { readOnly: boolean } }) => (
        <textarea aria-label="Template source" value={value} readOnly={options.readOnly} onChange={event => onChange(event.target.value)} />
    ),
}));
type Reply = { data: unknown };
const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};
const props = () => ({
    isOpen: true, onClose: vi.fn(), tool: 'send_email_summary' as const,
    currentTemplate: '<p>Existing summary</p>', includeTranscript: true,
    defaultTemplate: '<p>Default summary</p>', variableNames: [], defaultsStatusText: 'Loaded',
    onReloadDefaults: vi.fn().mockResolvedValue(undefined), onSave: vi.fn().mockResolvedValue(undefined),
});
const flush = async () => { await act(async () => {}); };
const previewButton = () => screen.getAllByRole('button', { name: 'Preview' }).find(button => !button.hasAttribute('disabled'))!;
beforeEach(() => { vi.resetAllMocks(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('Email template opening and preview ownership', () => {
    it('ignores a late preview after close/reopen, including the same tool', async () => {
        const pending = deferred<Reply>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        const input = props();
        const view = render(<EmailTemplateModal {...input} />);
        fireEvent.click(previewButton());
        const signal = vi.mocked(axios.post).mock.calls[0][2]?.signal;
        view.rerender(<EmailTemplateModal {...input} isOpen={false} />);
        expect(signal?.aborted).toBe(true);
        view.rerender(<EmailTemplateModal {...input} />);
        await act(async () => { pending.resolve({ data: { success: true, html: '<p>Obsolete opening</p>' } }); });
        expect(screen.queryByTitle('Email Preview')).not.toBeInTheDocument();
        expect(screen.getByLabelText('Template source')).toHaveValue(input.currentTemplate);
    });

    it('starts a new draft when switching the tool while open', async () => {
        const pending = deferred<Reply>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        const input = props();
        const view = render(<EmailTemplateModal {...input} />);
        fireEvent.change(screen.getByLabelText('Template source'), { target: { value: '<p>Unsubmitted summary draft</p>' } });
        fireEvent.click(previewButton());
        view.rerender(<EmailTemplateModal {...input} tool="request_transcript" currentTemplate="<p>Transcript</p>" />);
        await act(async () => { pending.resolve({ data: { success: true, html: '<p>Old summary</p>' } }); });
        expect(screen.getByRole('dialog')).toHaveAccessibleName('Request Transcript – Email Template');
        expect(screen.getByLabelText('Template source')).toHaveValue('<p>Transcript</p>');
        expect(screen.queryByTitle('Email Preview')).not.toBeInTheDocument();
    });

    it('invalidates an in-flight preview on draft edits and accepts the next preview only', async () => {
        const old = deferred<Reply>();
        vi.mocked(axios.post).mockReturnValueOnce(old.promise).mockResolvedValueOnce({ data: { success: true, html: '<p>Current preview</p>' } });
        render(<EmailTemplateModal {...props()} />);
        fireEvent.click(previewButton());
        const signal = vi.mocked(axios.post).mock.calls[0][2]?.signal;
        fireEvent.change(screen.getByLabelText('Template source'), { target: { value: '<p>Current draft</p>' } });
        expect(signal?.aborted).toBe(true);
        fireEvent.click(previewButton());
        await flush();
        await act(async () => { old.resolve({ data: { success: true, html: '<p>Old preview</p>' } }); });
        expect(screen.getByTitle('Email Preview')).toHaveAttribute('srcdoc', '<p>Current preview</p>');
        expect(axios.post).toHaveBeenLastCalledWith('/api/tools/email-templates/preview', expect.objectContaining({ html_template: '<p>Current draft</p>' }), expect.objectContaining({ signal: expect.any(AbortSignal) }));
    });

    it('clears preview output when transcript options change without resetting the draft', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: { success: true, html: '<p>Preview</p>' } });
        const input = props();
        const view = render(<EmailTemplateModal {...input} />);
        fireEvent.change(screen.getByLabelText('Template source'), { target: { value: '<p>Draft</p>' } });
        fireEvent.click(previewButton());
        await flush();
        view.rerender(<EmailTemplateModal {...input} includeTranscript={false} />);
        expect(screen.queryByTitle('Email Preview')).not.toBeInTheDocument();
        expect(screen.getByLabelText('Template source')).toHaveValue('<p>Draft</p>');
    });

    it('keeps customized edits when parent configuration/defaults refresh during an opening', () => {
        const input = props();
        const view = render(<EmailTemplateModal {...input} />);
        fireEvent.change(screen.getByLabelText('Template source'), { target: { value: '<p>Unsaved edit</p>' } });
        view.rerender(<EmailTemplateModal {...input} currentTemplate="<p>Refreshed saved value</p>" defaultTemplate="<p>New default</p>" />);
        expect(screen.getByLabelText('Template source')).toHaveValue('<p>Unsaved edit</p>');
        view.rerender(<EmailTemplateModal {...input} isOpen={false} />);
        view.rerender(<EmailTemplateModal {...input} currentTemplate="<p>Refreshed saved value</p>" />);
        expect(screen.getByLabelText('Template source')).toHaveValue('<p>Refreshed saved value</p>');
    });

    it('shows a late-loaded default without overwriting customized text', () => {
        const input = props();
        const view = render(<EmailTemplateModal {...input} currentTemplate={null} defaultTemplate="" />);
        view.rerender(<EmailTemplateModal {...input} currentTemplate={null} />);
        expect(screen.getByLabelText('Template source')).toHaveValue(input.defaultTemplate);
        expect(screen.getByLabelText('Template source')).toHaveAttribute('readonly');
        fireEvent.click(screen.getByRole('checkbox'));
        fireEvent.change(screen.getByLabelText('Template source'), { target: { value: '<p>Custom text</p>' } });
        view.rerender(<EmailTemplateModal {...input} currentTemplate={null} defaultTemplate="<p>Another default</p>" />);
        expect(screen.getByLabelText('Template source')).toHaveValue('<p>Custom text</p>');
    });

    it('ignores stale preview failures after unmount', async () => {
        const pending = deferred<Reply>();
        vi.mocked(axios.post).mockReturnValue(pending.promise);
        const view = render(<EmailTemplateModal {...props()} />);
        fireEvent.click(previewButton());
        view.unmount();
        await act(async () => { pending.reject(new Error('Obsolete preview error')); });
        expect(toast.error).not.toHaveBeenCalled();
    });
});

describe('Email template persistence lifecycle', () => {
    it('locks editing/dismissal and prevents duplicate saves while persistence is pending', async () => {
        const pending = deferred<void>();
        const input = props();
        input.onSave.mockReturnValue(pending.promise);
        render(<EmailTemplateModal {...input} />);
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
        expect(screen.getByRole('button', { name: 'Reload Defaults' })).toBeDisabled();
        expect(screen.getByLabelText('Template source')).toHaveAttribute('readonly');
        fireEvent.keyDown(document, { key: 'Escape' });
        fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
        fireEvent.click(screen.getByRole('button', { name: 'Saving…' }));
        expect(input.onClose).not.toHaveBeenCalled();
        expect(input.onSave).toHaveBeenCalledTimes(1);
        await act(async () => { pending.resolve(undefined); });
        expect(input.onClose).toHaveBeenCalledTimes(1);
    });

    it('preserves a failed draft for retry', async () => {
        const input = props();
        input.onSave.mockRejectedValueOnce(new Error('Synthetic rejected save'));
        render(<EmailTemplateModal {...input} />);
        fireEvent.change(screen.getByLabelText('Template source'), { target: { value: '<p>Retry this draft</p>' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        await flush();
        expect(input.onClose).not.toHaveBeenCalled();
        expect(screen.getByLabelText('Template source')).toHaveValue('<p>Retry this draft</p>');
        expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        await flush();
        expect(input.onSave).toHaveBeenNthCalledWith(2, '<p>Retry this draft</p>');
        expect(input.onClose).toHaveBeenCalledTimes(1);
    });

    it('does not close a new opening or toast when an older accepted save completes', async () => {
        const pending = deferred<void>();
        const input = props();
        input.onSave.mockReturnValue(pending.promise);
        const view = render(<EmailTemplateModal {...input} />);
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        view.rerender(<EmailTemplateModal {...input} isOpen={false} />);
        const currentClose = vi.fn();
        view.rerender(<EmailTemplateModal {...input} onClose={currentClose} />);
        await act(async () => { pending.resolve(undefined); });
        expect(currentClose).not.toHaveBeenCalled();
        expect(input.onClose).not.toHaveBeenCalled();
        expect(toast.success).not.toHaveBeenCalled();
        expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('submits null for default mode without sending email', async () => {
        const input = props();
        render(<EmailTemplateModal {...input} currentTemplate={null} />);
        fireEvent.click(screen.getByRole('button', { name: 'Save' }));
        await flush();
        expect(input.onSave).toHaveBeenCalledWith(null);
        expect(axios.post).not.toHaveBeenCalled();
    });
});
