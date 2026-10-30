// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import yaml from 'js-yaml';
import ToolsPage from './ToolsPage';

// These integration tests intentionally mount every real tool form to verify
// draft/OAuth preservation across navigation. Accessibility queries over that
// DOM take longer on shared CI runners than the default five-second budget.
vi.setConfig({ testTimeout: 30000 });

const mocks = vi.hoisted(() => ({
    load: vi.fn(), confirm: vi.fn(), refetch: vi.fn(),
    state: { restartRequired: false, applyRequired: false, recommendedApplyMethod: 'none', stateStale: false },
}));
vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ token: 'test-token' }) }));
vi.mock('../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: mocks.confirm }) }));
vi.mock('../hooks/useRestartRequired', () => ({ useRestartRequired: () => ({ ...mocks.state, refetch: mocks.refetch }) }));
vi.mock('../utils/configCache', () => ({ getCachedConfig: () => null, loadConfigYaml: mocks.load }));
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), isCancel: () => false } }));
vi.mock('@monaco-editor/react', () => ({
    default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) =>
        <textarea aria-label="Template source" value={value} onChange={event => onChange(event.target.value)} />,
}));

const fixture = () => ({
    default_provider: 'google_live',
    providers: { google_live: { enabled: true, custom_option: 'preserve-provider' } },
    on_provider_failure: 'hangup', provider_failure_prompt: 'sorry-youre-having-problems',
    tools: {
        ai_identity: { name: 'Test agent', number: '6789' },
        transfer: { enabled: true, technology: 'PJSIP', deferred_audio_drain_timeout_sec: 19, handoff: { enabled: true, message: 'Connecting you.', timeout_sec: 7 }, destinations: { reception: { type: 'extension', target: '100', dialplan_context: 'custom-context', handoff: { mode: 'disabled' } } } },
        attended_transfer: { enabled: false, custom_screening: 'preserve' },
        cancel_transfer: { enabled: true, allow_during_ring: false },
        check_extension_status: { restrict_to_configured_extensions: true },
        hangup_call: { enabled: true, farewell_message: 'Goodbye.', policy: { mode: 'strict', markers: { end_call: ['custom marker'] } } },
        leave_voicemail: { enabled: true, mailboxes: { default: { name: 'Reception', extension: '100' } }, default_mailbox: 'default' },
        send_email_summary: { enabled: true, admin_email: 'admin@example.com', html_template: '<p>Existing custom template</p>' },
        request_transcript: { enabled: true, confirm_email: true },
        google_calendar: { enabled: true, calendars: {}, custom_availability: { working_days: [1, 2] } },
        microsoft_calendar: { enabled: true, accounts: {}, allow_caller_invitations: false, max_booking_duration_minutes: 240 },
        private_custom: { kind: 'custom', preserve: { nested: [0, false, 'x'] } },
        before: { kind: 'generic_http_lookup', phase: 'pre_call', enabled: true, url: 'https://example.com/before', method: 'GET', headers: { Authorization: 'Bearer ${CRM_KEY}' }, output_variables: { customer: 'name' } },
        after: { kind: 'generic_webhook', phase: 'post_call', enabled: true, is_global: true, url: 'https://example.com/after', method: 'POST', headers: {}, payload_template: '{"summary":{summary_json}}' },
    },
    in_call_tools: { lookup: { kind: 'in_call_http_lookup', phase: 'in_call', enabled: true, url: 'https://example.com/lookup', method: 'GET', headers: {}, description: 'Check stock', parameters: [], custom_field: 'retain' } },
    contexts: { sales: { tools: ['hangup_call'], pre_call_tools: ['before'], disable_global_post_call_tools: ['after'], tool_overrides: { leave_voicemail: { mailbox: 'default' } } } },
    mcp: { servers: { example: { command: 'existing-server' } } },
});

beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    Object.assign(mocks.state, { restartRequired: false, applyRequired: false, recommendedApplyMethod: 'none', stateStale: false });
    mocks.load.mockResolvedValue({ config: fixture(), yamlError: null });
    mocks.refetch.mockResolvedValue(undefined);
    mocks.confirm.mockResolvedValue(false);
    vi.mocked(axios.get).mockResolvedValue({ data: { tools: [], providers: [] } });
    vi.mocked(axios.post).mockImplementation(async url => ({ data: url === '/api/config/yaml' ? { recommended_apply_method: 'hot_reload' } : { tool_generation: 9 } }));
});

async function open(name: string) {
    await screen.findByRole('heading', { name: 'Tools' });
    fireEvent.click(screen.getByRole('button', { name: `Configure ${name}` }));
}

// HTTP draft validation is read-only; editing/cancelling must still never save
// configuration, apply a generation, or dispatch an actual HTTP test request.
function expectNoConfigurationWrites() {
    expect(vi.mocked(axios.post).mock.calls.filter(([url]) => url !== '/api/tools/http-templates/validate')).toEqual([]);
}
async function editFarewell() {
    await open('Hangup');
    fireEvent.change(screen.getByLabelText('Default Farewell Message'), { target: { value: 'See you soon.' } });
}
function postedConfig() {
    const call = vi.mocked(axios.post).mock.calls.find(([url]) => url === '/api/config/yaml');
    return yaml.load((call?.[1] as { content: string }).content) as ReturnType<typeof fixture>;
}

describe('ARI assisted destination setup', () => {
    async function stageDiscoveredDestination() {
        await open('Transfer & live agents');
        fireEvent.click(screen.getByRole('button', { name: 'Discover endpoints' }));
        fireEvent.click(await screen.findByRole('checkbox', { name: 'Select PJSIP/0012' }));
        fireEvent.click(screen.getByRole('button', { name: 'Review selected (1)' }));
        fireEvent.change(screen.getByLabelText('Description / label'), { target: { value: 'Sales team' } });
        fireEvent.click(screen.getByRole('button', { name: 'Add to configuration' }));
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    }

    beforeEach(() => {
        vi.mocked(axios.get).mockImplementation(async url => ({
            data: url === '/api/system/ari/endpoints'
                ? { endpoints: [{ technology: 'PJSIP', resource: '0012', state: 'offline' }], discovered_at: '2026-10-20T10:00:00Z' }
                : { tools: [], providers: [] },
        }));
    });

    it('discovers, reviews, stages, saves/applies and reopens while preserving unrelated YAML and manual configuration', async () => {
        const { unmount } = render(<ToolsPage />);
        await stageDiscoveredDestination();
        expect(vi.mocked(axios.post).mock.calls.map(([url]) => url)).toEqual(['/api/tools/transfer-destinations/validate']);
        expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        const expected = fixture();
        (expected.tools.transfer.destinations as Record<string, unknown>).ext_0012 = { type: 'extension', target: '0012', description: 'Sales team', dialplan_context: 'from-internal', attended_allowed: false };
        expected.tools.hangup_call.farewell_message = 'See you soon.';
        expect(postedConfig()).toEqual(expected);
        expect(vi.mocked(axios.post).mock.calls.map(([url]) => url)).toEqual([
            '/api/tools/transfer-destinations/validate', '/api/config/yaml', '/api/system/containers/ai_engine/reload',
        ]);
        unmount();
        mocks.load.mockResolvedValue({ config: postedConfig(), yamlError: null });
        render(<ToolsPage />);
        await open('Transfer & live agents');
        expect(screen.getByText('ext_0012')).toBeInTheDocument();
        expect(screen.getByText('reception')).toBeInTheDocument();
        expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument();
    });

    it('supports discovery with transfer disabled and never enables it or creates Live Agents', async () => {
        const config = fixture();
        config.tools.transfer.enabled = false;
        mocks.load.mockResolvedValue({ config, yamlError: null });
        render(<ToolsPage />);
        await stageDiscoveredDestination();
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        expect(postedConfig().tools.transfer.enabled).toBe(false);
        expect((postedConfig().tools as Record<string, unknown>).extensions).toBeUndefined();
        expect(postedConfig().tools.attended_transfer).toEqual(config.tools.attended_transfer);
    });

    it('retains staged destinations after save failure and distinguishes a saved but unapplied configuration', async () => {
        render(<ToolsPage />);
        await stageDiscoveredDestination();
        vi.mocked(axios.post).mockImplementation(async url => {
            if (url === '/api/config/yaml') throw new Error('save failed');
            return { data: {} };
        });
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText('Tools were not saved.');
        expect(screen.getByText('ext_0012')).toBeInTheDocument();
        expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
        vi.mocked(axios.post).mockImplementation(async url => {
            if (url === '/api/system/containers/ai_engine/reload') throw new Error('apply failed');
            return { data: { recommended_apply_method: 'hot_reload' } };
        });
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText('Saved changes could not be applied; the previous tool generation remains active.');
        expect(screen.getByText('ext_0012')).toBeInTheDocument();
    });
});

describe('Tools list and dedicated editors', () => {
    it('starts with a compact list and preserves the full config through editing, navigation and Save & Apply', async () => {
        render(<ToolsPage />);
        await screen.findByRole('heading', { name: 'Tools' });
        expect(screen.queryByRole('textbox', { name: 'Default Farewell Message' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Save & Apply' })).not.toBeInTheDocument();
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        fireEvent.click(screen.getByRole('button', { name: 'Pre-Call' }));
        fireEvent.click(screen.getByRole('button', { name: 'In-Call' }));
        await open('Hangup');
        expect(screen.getByLabelText('Default Farewell Message')).toHaveValue('See you soon.');
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        const expected = fixture();
        expected.tools.hangup_call.farewell_message = 'See you soon.';
        expect(postedConfig()).toEqual(expected);
        expect(vi.mocked(axios.post).mock.calls.map(([url]) => url)).toEqual(['/api/config/yaml', '/api/system/containers/ai_engine/reload']);
        expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument();
    });

    it('keeps every built-in editor reachable and makes catalog read-only', async () => {
        render(<ToolsPage />);
        for (const [name, control] of [
            ['Transfer & live agents', 'Transfer Tool'], ['Hangup', 'Hangup Call'], ['Voicemail', 'Leave Voicemail'],
            ['Email summary', 'Send Email Summary'], ['Transcript requests', 'Request Transcript'],
            ['Google Calendar', 'Google Calendar'], ['Microsoft Calendar', 'Microsoft Calendar'],
        ]) {
            await open(name);
            expect(screen.getByRole('checkbox', { name: control })).toBeVisible();
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
            fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        }
        fireEvent.click(screen.getByRole('button', { name: 'Tool defaults' }));
        expect(screen.getByRole('textbox', { name: 'Agent Name' })).toBeVisible();
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        fireEvent.click(screen.getByRole('button', { name: 'Catalog' }));
        expect(screen.getByRole('heading', { name: 'Tool Catalog (Read-only)' })).toBeVisible();
        expect(screen.queryByRole('button', { name: 'Save & Apply' })).not.toBeInTheDocument();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('searches configured tools and does not change hidden settings when opening advanced controls', async () => {
        render(<ToolsPage />);
        await screen.findByRole('heading', { name: 'Tools' });
        fireEvent.change(screen.getByRole('textbox', { name: 'Search tools' }), { target: { value: 'calendar' } });
        expect(screen.getByRole('button', { name: 'Configure Microsoft Calendar' })).toBeVisible();
        expect(screen.queryByRole('button', { name: 'Configure Hangup' })).not.toBeInTheDocument();
        fireEvent.change(screen.getByRole('textbox', { name: 'Search tools' }), { target: { value: '' } });
        await open('Transfer & live agents');
        const disclosure = screen.getByText('Advanced transfer settings — routing and timing').closest('details')!;
        expect(disclosure).not.toHaveAttribute('open');
        fireEvent.click(within(disclosure).getByText('Advanced transfer settings — routing and timing'));
        expect(screen.getByLabelText('Deferred Audio Drain Timeout (seconds)')).toHaveValue(19);
        expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it.each(['', 'example.com/lookup'])('stages inline HTTP edits and restores focus with search %s', async query => {
        const user = userEvent.setup();
        render(<ToolsPage />);
        fireEvent.change(await screen.findByRole('textbox', { name: 'Search tools' }), { target: { value: query } });
        await user.click(await screen.findByRole('button', { name: 'Configure lookup' }));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(screen.getByRole('heading', { name: 'Edit lookup' })).toHaveFocus();
        expect(screen.queryByRole('button', { name: 'Save & Apply' })).not.toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://example.com/new-lookup' } });
        fireEvent.click(screen.getByRole('button', { name: 'Update tool' }));
        expect(screen.getByRole('button', { name: query ? 'Add Tool' : 'Configure lookup' })).toHaveFocus();
        expectNoConfigurationWrites();
        fireEvent.click(await screen.findByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        const saved = postedConfig();
        expect(saved.in_call_tools.lookup.url).toBe('https://example.com/new-lookup');
        expect(saved.in_call_tools.lookup.custom_field).toBe('retain');
        expect(saved.tools).toEqual(fixture().tools);
        expect(saved.contexts).toEqual(fixture().contexts);
    });

    it.each(['Cancel', 'Cancel and return to tools'])('discards an HTTP draft and restores focus with %s', async cancelLabel => {
        const user = userEvent.setup();
        render(<ToolsPage />);
        await user.click(await screen.findByRole('button', { name: 'Configure lookup' }));
        await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/tools/http-templates/validate', expect.objectContaining({ phase: 'in_call' }), expect.anything()));
        fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://example.com/discard' } });
        await user.click(screen.getByRole('button', { name: cancelLabel }));
        expect(screen.getByRole('button', { name: 'Configure lookup' })).toHaveFocus();
        fireEvent.click(screen.getByRole('button', { name: 'Configure lookup' }));
        expect(screen.getByLabelText('URL')).toHaveValue('https://example.com/lookup');
        expectNoConfigurationWrites();
    });

    it('restores focus to Add Tool when a new HTTP draft is cancelled', async () => {
        const user = userEvent.setup();
        render(<ToolsPage />);
        await user.click(await screen.findByRole('button', { name: 'Add Tool' }));
        expect(screen.getByRole('heading', { name: 'Add In-Call HTTP Tool' })).toHaveFocus();
        await user.click(screen.getByRole('button', { name: 'Cancel' }));
        expect(screen.getByRole('button', { name: 'Add Tool' })).toHaveFocus();
        expectNoConfigurationWrites();
    });

    it('commits a blur-based transfer value before saving with the mouse', async () => {
        const user = userEvent.setup();
        render(<ToolsPage />);
        await open('Transfer & live agents');
        await user.click(screen.getByRole('button', { name: 'Show State Value Mapping' }));
        const free = screen.getByLabelText('Free');
        await user.clear(free);
        await user.type(free, 'NOT_INUSE CUSTOM_READY');
        await user.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        expect((postedConfig().tools.check_extension_status as unknown as { state_mapping: { free: string[] } }).state_mapping.free).toEqual(['NOT_INUSE', 'CUSTOM_READY']);
    });

    it.each([
        { scenario: 'discovered calendar', calendars: [{ id: 'calendar', name: 'Appointments', is_default_calendar: true }], expectedCalendar: 'operator-calendar' },
        { scenario: 'no discovered calendar', calendars: [], expectedCalendar: 'operator-calendar' },
    ])('keeps pending Microsoft draft edits across navigation with $scenario', async ({ calendars, expectedCalendar }) => {
        const config = fixture();
        config.tools.microsoft_calendar.accounts = { default: { tenant_id: 'tenant', client_id: 'client', timezone: 'UTC' } };
        mocks.load.mockResolvedValue({ config, yamlError: null });
        let finish!: (value: unknown) => void;
        const pending = new Promise(resolve => { finish = resolve; });
        vi.mocked(axios.post).mockImplementation(async url => ({ data: url.toString().endsWith('/device/start')
            ? { flow_id: 'test-flow', user_code: 'EXAMPLE', verification_uri: 'https://microsoft.com/devicelogin' }
            : url === '/api/config/yaml' ? { recommended_apply_method: 'hot_reload' } : { tool_generation: 9 } }));
        vi.mocked(axios.get).mockImplementation(async url => url.toString().includes('/device/status/') ? pending : { data: { tools: [], providers: [] } });
        render(<ToolsPage />);
        await open('Microsoft Calendar');
        fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
        await screen.findByText('Authorize Microsoft Calendar');
        fireEvent.change(screen.getByLabelText('Timezone'), { target: { value: 'Pacific/Auckland' } });
        fireEvent.change(screen.getByLabelText('Calendar ID'), { target: { value: 'operator-calendar' } });
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        await editFarewell();
        await act(async () => finish({ data: { status: 'success', result: { token_cache_path: '/app/project/secrets/test-cache.json', user_principal_name: 'test@example.com', calendars } } }));
        expect(screen.getByLabelText('Default Farewell Message')).toHaveValue('See you soon.');
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        await open('Microsoft Calendar');
        expect(screen.getByLabelText('Signed-in user')).toHaveValue('test@example.com');
        expect(screen.getByLabelText('Timezone')).toHaveValue('Pacific/Auckland');
        expect(screen.getByLabelText('Calendar ID')).toHaveValue(expectedCalendar);
        expect(vi.mocked(axios.post).mock.calls.filter(([url]) => url.toString().endsWith('/device/start'))).toHaveLength(1);
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        expect(postedConfig().tools.hangup_call.farewell_message).toBe('See you soon.');
        expect(postedConfig().tools.microsoft_calendar.accounts).toMatchObject({ default: { timezone: 'Pacific/Auckland', calendar_id: expectedCalendar } });
    });

    it('merges Google verification autofill into the latest draft after changing editors', async () => {
        const config = fixture();
        config.tools.google_calendar.calendars = { office: { credentials_path: '/app/project/secrets/test.json', calendar_id: 'office@example.com', timezone: '' } };
        mocks.load.mockResolvedValue({ config, yamlError: null });
        let finish!: (value: unknown) => void;
        const pending = new Promise(resolve => { finish = resolve; });
        vi.mocked(axios.post).mockImplementation(async url => url.toString().endsWith('/office/verify') ? pending
            : { data: url === '/api/config/yaml' ? { recommended_apply_method: 'hot_reload' } : { tool_generation: 9 } });
        render(<ToolsPage />);
        await open('Google Calendar');
        fireEvent.click(screen.getByRole('button', { name: /Verify access/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        await editFarewell();
        await act(async () => finish({ data: { calendar_actual_timezone: 'UTC', calendar_summary: 'Office' } }));
        expect(screen.getByLabelText('Default Farewell Message')).toHaveValue('See you soon.');
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        expect(postedConfig().tools.google_calendar.calendars).toEqual({ office: { credentials_path: '/app/project/secrets/test.json', calendar_id: 'office@example.com', timezone: 'UTC' } });
        expect(postedConfig().tools.hangup_call.farewell_message).toBe('See you soon.');
    });
});

describe('Tools persistence and apply outcomes', () => {
    it('preserves the immediate email-template save path and other staged tool edits', async () => {
        render(<ToolsPage />);
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        await open('Email summary');
        fireEvent.click(screen.getByRole('button', { name: 'Show Advanced Email Format' }));
        fireEvent.click(screen.getByRole('button', { name: 'Edit / Preview' }));
        const dialog = screen.getByRole('dialog');
        fireEvent.change(within(dialog).getByLabelText('Template source'), { target: { value: '<p>Updated template</p>' } });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(postedConfig().tools.send_email_summary.html_template).toBe('<p>Updated template</p>');
        expect(postedConfig().tools.hangup_call.farewell_message).toBe('See you soon.');
        expect(mocks.refetch).toHaveBeenCalled();
    });

    it.each(['Pre-Call', 'Post-Call'])('preserves the other phases when updating a %s HTTP tool', async phase => {
        render(<ToolsPage />);
        await screen.findByRole('heading', { name: 'Tools' });
        fireEvent.click(screen.getByRole('button', { name: phase }));
        const key = phase === 'Pre-Call' ? 'before' : 'after';
        fireEvent.click(screen.getByRole('button', { name: `Configure ${key}` }));
        fireEvent.change(screen.getByLabelText('URL'), { target: { value: `https://example.com/new-${key}` } });
        fireEvent.click(screen.getByRole('button', { name: 'Update tool' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        const expected = fixture();
        expected.tools[key].url = `https://example.com/new-${key}`;
        expect(postedConfig()).toEqual(expected);
    });

    it('retains unsaved edits and never reloads after a save failure', async () => {
        vi.mocked(axios.post).mockRejectedValueOnce(new Error('Save failed'));
        render(<ToolsPage />);
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText('Tools were not saved.');
        expect(screen.getByLabelText('Default Farewell Message')).toHaveValue('See you soon.');
        expect(screen.getByRole('button', { name: 'Save & Apply' })).toBeEnabled();
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('distinguishes a persisted draft from a failed apply and refreshes pending status', async () => {
        vi.mocked(axios.post).mockImplementation(async url => {
            if (url === '/api/config/yaml') return { data: { recommended_apply_method: 'hot_reload' } };
            throw new Error('Engine unavailable');
        });
        render(<ToolsPage />);
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText('Saved changes could not be applied; the previous tool generation remains active.');
        expect(mocks.refetch).toHaveBeenCalled();
        expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument();
    });

    it('does not report full success after a partial hot reload', async () => {
        vi.mocked(axios.post).mockImplementation(async url => ({ data: url === '/api/config/yaml' ? { recommended_apply_method: 'hot_reload' } : { restart_required: true } }));
        render(<ToolsPage />);
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText('Saved and partially applied. Restart the AI Engine to finish applying changes.');
        expect(screen.queryByText(/Applied as tool generation/)).not.toBeInTheDocument();
    });

    it.each(['none', 'restart'])('honors the backend %s apply plan without automatically restarting', async method => {
        vi.mocked(axios.post).mockResolvedValue({ data: { recommended_apply_method: method } });
        render(<ToolsPage />);
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(method === 'none' ? 'Saved. No apply action is required.' : 'Saved. A restart is required for these changes.');
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('keeps stale pending state visible and respects an active-call restart warning', async () => {
        Object.assign(mocks.state, { restartRequired: true, recommendedApplyMethod: 'restart', stateStale: true });
        vi.mocked(axios.post).mockResolvedValue({ data: { status: 'warning', message: 'One active call.' } });
        render(<ToolsPage />);
        fireEvent.click(await screen.findByRole('button', { name: 'Restart AI Engine' }));
        await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ title: 'Force Restart?', variant: 'destructive' })));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.post).toHaveBeenCalledWith('/api/system/containers/ai_engine/restart?force=false', {}, expect.anything());
        expect(screen.getByText(/Status refresh failed; showing the last known action/)).toBeVisible();
    });

    it('can retry applying saved changes without saving the config again', async () => {
        Object.assign(mocks.state, { applyRequired: true, recommendedApplyMethod: 'hot_reload' });
        render(<ToolsPage />);
        fireEvent.click(await screen.findByRole('button', { name: 'Apply Changes' }));
        await screen.findByText(/Applied as tool generation 9/);
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.post).toHaveBeenCalledWith('/api/system/containers/ai_engine/reload', {}, expect.anything());
    });

    it('blocks editing after a load failure and supports retry', async () => {
        mocks.load.mockRejectedValueOnce(new Error('Network failure'));
        render(<ToolsPage />);
        fireEvent.click(await screen.findByRole('button', { name: 'Retry loading tools' }));
        await screen.findByRole('heading', { name: 'Tools' });
        expect(mocks.load).toHaveBeenLastCalledWith(true);
        expect(axios.post).not.toHaveBeenCalled();
    });
});
