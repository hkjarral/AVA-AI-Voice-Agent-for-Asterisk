// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import HTTPToolForm from './HTTPToolForm';
vi.mock('../../auth/AuthContext', () => ({ useAuth: () => ({ token: 'synthetic' }) }));
vi.mock('../../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: vi.fn() }) }));
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(axios.get).mockResolvedValue({
        data: {
            providers: [
                {
                    key: 'test_llm',
                    label: 'Synthetic provider',
                    ready: true,
                    credential_configured: true,
                },
            ],
        },
    });
    vi.mocked(axios.post).mockImplementation(async (url, body) => ({
        data:
            url === '/api/pushover/templates/validate'
                ? { valid: !(body as {config?: {body?: string}})?.config?.body?.includes('{bad}'), issues: (body as {config?: {body?: string}})?.config?.body?.includes('{bad}') ? [{field:'body', severity:'error', error_code:'unsupported_template_variable', message:'Unsupported notification variable.'}] : [], references: [], variables: [], example_title: '{agent}: example', example_body: 'Caller: {caller_number}', preview: {title:'Synthetic preview', message:'Synthetic message'} }
                : url === '/api/pushover/credentials'
                ? {
                      token_file: '/managed/application',
                      recipients: Object.fromEntries(
                          Object.keys(
                              (body as { recipients?: Record<string, string> })?.recipients || {}
                          ).map(id => [id, { key_file: `/managed/${id}` }])
                      ),
                  }
                : { title: 'Synthetic preview', message: 'Synthetic message', preview_only: true },
    }));
});
describe('Pushover in the existing tool editor', () => {
    it.each(['pre_call', 'in_call', 'post_call'] as const)(
        'stages a named %s tool with masked secret references',
        async phase => {
            const changed = vi.fn();
            render(
                <HTTPToolForm phase={phase} config={{}} onChange={changed} presentation="page" />
            );
            fireEvent.click(screen.getByRole('button', { name: 'Add Tool' }));
            fireEvent.change(screen.getByLabelText('Tool type'), { target: { value: 'pushover' } });
            expect(screen.queryByLabelText('URL')).not.toBeInTheDocument();
            let defaultBody = 'Caller: {caller_number}\nCall: {call_id}';
            if (phase === 'post_call') defaultBody += '\nOutcome: {outcome}';
            expect(screen.getByLabelText('Message template')).toHaveValue('');
            expect(screen.getByLabelText('Message template')).toHaveAttribute(
                'placeholder',
                defaultBody
            );

            expect(
                screen.getByRole('link', { name: /Official Pushover documentation/ })
            ).toHaveAttribute('href', 'https://pushover.net/api');
            fireEvent.change(screen.getByLabelText('Tool Name'), {
                target: { value: 'notify_sales' },
            });
            fireEvent.change(screen.getByLabelText('Recipient name (recipient_1)'), {
                target: { value: 'Sales' },
            });
            fireEvent.change(screen.getByLabelText('Application token'), {
                target: { value: 'a'.repeat(30) },
            });
            fireEvent.change(screen.getByLabelText('User/group key (recipient_1)'), {
                target: { value: 'b'.repeat(30) },
            });
            if (phase === 'in_call')
                fireEvent.change(screen.getByLabelText('Tool description / when to use'), {
                    target: { value: 'Use before a Sales handoff' },
                });
            expect(vi.mocked(axios.post).mock.calls.filter(([url]) => !String(url).includes('templates/validate'))).toHaveLength(0);
            await waitFor(() => expect(screen.getByRole('button', { name: 'Update tool' })).toBeEnabled());
            fireEvent.click(screen.getByRole('button', { name: 'Update tool' }));
            await waitFor(() => expect(changed).toHaveBeenCalledOnce());
            const doc = changed.mock.calls[0][0].notify_sales;
            expect(doc).toMatchObject({
                kind: 'pushover',
                phase,
                token_file: '/managed/application',
                recipients: {
                    recipient_1: {
                        label: 'Sales',
                        enabled: true,
                        key_file: '/managed/recipient_1',
                    },
                },
            });
            expect(doc).not.toHaveProperty('url');
            expect(doc).not.toHaveProperty('parameters');
            expect(doc).not.toHaveProperty('body');
            expect(doc).not.toHaveProperty('title');
            expect(JSON.stringify(doc)).not.toContain('a'.repeat(30));
            expect(JSON.stringify(doc)).not.toContain('b'.repeat(30));
            expect(vi.mocked(axios.post).mock.calls.filter(([url]) => !String(url).includes('templates/validate')).map(([url]) => url)).toEqual([
                '/api/pushover/credentials',
            ]);
            // The staged document can be saved/applied by the parent and reopened normally.
        }
    );
    it('reopens masked credentials and previews without sending or generating a summary', async () => {
        const changed = vi.fn();
        render(
            <HTTPToolForm
                phase="in_call"
                config={{
                    notify_sales: {
                        kind: 'pushover',
                        phase: 'in_call',
                        enabled: true,
                        is_global: false,
                        token_file: '/managed/application',
                        key_file: '/managed/recipient',
                        recipient_label: 'Sales',
                        description: 'Use for Sales',
                    },
                }}
                onChange={changed}
                presentation="page"
            />
        );
        fireEvent.click(screen.getByRole('button', { name: 'Configure notify_sales' }));
        expect(screen.getByLabelText('Application token')).toHaveValue('');
        expect(screen.getByLabelText('User/group key (legacy)')).toHaveValue('');
        fireEvent.click(screen.getByRole('button', { name: 'Validate & Preview' }));
        await screen.findByLabelText('Synthetic notification preview');
        await waitFor(() => expect(screen.getByLabelText('Synthetic notification preview')).toHaveTextContent('Synthetic message'));
        expect(vi.mocked(axios.post).mock.calls.filter(([url]) => !String(url).includes('templates/validate')).map(([url]) => url)).toEqual([
        ]);
        await waitFor(() => expect(screen.getByRole('button', { name: 'Update tool' })).toBeEnabled());
            fireEvent.click(screen.getByRole('button', { name: 'Update tool' }));
        await waitFor(() => expect(changed).toHaveBeenCalledOnce());
        expect(changed.mock.calls[0][0].notify_sales.token_file).toBe('/managed/application');
        expect(changed.mock.calls[0][0].notify_sales.recipients.legacy.key_file).toBe(
            '/managed/recipient'
        );
    });
});

it('adds, disables and removes recipient/group rows without sending', async () => {
    const changed = vi.fn();
    render(<HTTPToolForm phase="post_call" config={{}} onChange={changed} presentation="page" />);
    fireEvent.click(screen.getByRole('button', { name: 'Add Tool' }));
    fireEvent.change(screen.getByLabelText('Tool type'), { target: { value: 'pushover' } });
    fireEvent.change(screen.getByLabelText('Tool Name'), { target: { value: 'send_recap' } });
    fireEvent.change(screen.getByLabelText('Recipient name (recipient_1)'), {
        target: { value: 'Alice' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add recipient / group' }));
    fireEvent.change(screen.getByLabelText('Recipient name (recipient_2)'), {
        target: { value: 'Sales group' },
    });
    fireEvent.click(screen.getByLabelText('Recipient enabled (recipient_2)'));
    expect(screen.getByRole('button', { name: 'Send test to Sales group' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Add recipient / group' }));
    fireEvent.change(screen.getByLabelText('User/group key (recipient_3)'), {
        target: { value: 'd'.repeat(30) },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Remove recipient_3' }));
    expect(screen.queryByLabelText('User/group key (recipient_3)')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Update tool' })).toBeEnabled());
            fireEvent.click(screen.getByRole('button', { name: 'Update tool' }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(changed.mock.calls[0][0].send_recap.recipients).toEqual({
        recipient_1: { label: 'Alice', enabled: true, key_file: '' },
        recipient_2: { label: 'Sales group', enabled: false, key_file: '' },
    });
    expect(vi.mocked(axios.post).mock.calls.filter(([url]) => !String(url).includes('templates/validate'))).toHaveLength(0);
});

it('updates generic summary hints without replacing a saved custom CRM template', async () => {
    const changed = vi.fn();
    const body = 'Company: {company}\nCRM: {crm_status}\nCaller: {caller_number}';
    render(
        <HTTPToolForm
            phase="in_call"
            config={{
                custom_notice: {
                    kind: 'pushover',
                    phase: 'in_call',
                    body,
                    title: 'Custom {agent}',
                    enabled: true,
                    is_global: false,
                },
            }}
            onChange={changed}
            presentation="page"
        />
    );
    fireEvent.click(screen.getByRole('button', { name: 'Configure custom_notice' }));
    const template = screen.getByLabelText('Message template');
    expect(template).toHaveValue(body);
    expect(template).toHaveAttribute('placeholder', 'Caller: {caller_number}\nCall: {call_id}');
    fireEvent.click(screen.getByLabelText('Generate AI Summary'));
    expect(template).toHaveValue(body);
    expect(template).toHaveAttribute(
        'placeholder',
        'Caller: {caller_number}\nCall: {call_id}\nSummary: {summary}'
    );
    fireEvent.click(screen.getByLabelText('Generate AI Summary'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Update tool' })).toBeEnabled());
            fireEvent.click(screen.getByRole('button', { name: 'Update tool' }));
    await waitFor(() => expect(changed).toHaveBeenCalledOnce());
    expect(changed.mock.calls[0][0].custom_notice).toMatchObject({ body, title: 'Custom {agent}' });
});

it.each(['pre_call', 'in_call', 'post_call'] as const)('reviews %s templates, blocks invalid saves, and preserves saved values on cancel', async phase => {
    const changed = vi.fn();
    render(<HTTPToolForm phase={phase} config={{notify_example:{kind:'pushover', phase, body:'Saved message', title:'Saved title', enabled:true}}} onChange={changed} presentation="page" />);
    fireEvent.click(screen.getByRole('button', {name:'Configure notify_example'}));
    await waitFor(() => expect(screen.getByText('No definite template errors detected.')).toBeInTheDocument());
    expect(screen.getByLabelText('Pushover template guidance')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Application token'), {target:{value:'a'.repeat(30)}});
    fireEvent.change(screen.getByLabelText('Message template'), {target:{value:'{bad}'}});
    await waitFor(() => expect(screen.getByRole('button', {name:'Update tool'})).toBeDisabled());
    expect(await screen.findByText(/Unsupported notification variable/)).toBeInTheDocument();
    const validationCalls = vi.mocked(axios.post).mock.calls.filter(([url]) => url === '/api/pushover/templates/validate');
    expect(validationCalls.length).toBeGreaterThan(0);
    expect(JSON.stringify(validationCalls)).not.toContain('a'.repeat(30));
    fireEvent.click(screen.getByRole('button', {name:'Use example'}));
    expect(screen.getByLabelText('Notification title')).toHaveValue('{agent}: example');
    expect(screen.getByLabelText('Message template')).toHaveValue('Caller: {caller_number}');
    await waitFor(() => expect(screen.getByRole('button', {name:'Update tool'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button', {name:'Cancel'}));
    expect(changed).not.toHaveBeenCalled();
    expect(vi.mocked(axios.post).mock.calls.filter(([url]) => !String(url).includes('templates/validate'))).toHaveLength(0);
});
