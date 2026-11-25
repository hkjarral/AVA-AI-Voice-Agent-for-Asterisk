// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import axios from 'axios';
import { toast } from 'sonner';
import { loadConfigYaml } from '../../utils/configCache';
import RawYamlPage from './RawYamlPage';

vi.mock('axios', () => ({ default: { post: vi.fn() } }));
vi.mock('sonner', () => ({toast: {success: vi.fn(), error: vi.fn()}}));
vi.mock('@monaco-editor/react', () => ({
    default: ({value, onChange}: {value: string; onChange: (value: string) => void}) =>
        <textarea aria-label="YAML editor" value={value} onChange={event => onChange(event.target.value)} />,
}));
vi.mock('../../hooks/useConfirmDialog', () => ({useConfirmDialog: () => ({confirm: async () => true})}));
vi.mock('../../utils/configCache', () => ({
    getCachedConfig: () => ({content: 'stale: cached'}),
    loadConfigYaml: vi.fn(),
}));

const valid = { content: 'settings: {}', config: {settings: {}}, yamlError: null };
const read = vi.mocked(loadConfigYaml);
const saveButton = () => screen.getByRole('button', {name: /save changes/i}) as HTMLButtonElement;

beforeEach(() => vi.clearAllMocks());
afterEach(() => {cleanup(); vi.restoreAllMocks();});

it('blocks stale or blank saves after a failed read and retries after on-disk repair', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => {});
    const canary = crypto.randomUUID();
    read.mockRejectedValueOnce({response: {status: 400, data: {detail: 'Invalid YAML at line 7, column 9. Check the configuration file.'}},
        config: {headers: {Authorization: canary}}}).mockResolvedValueOnce(valid);
    render(<RawYamlPage />);
    expect((await screen.findByRole('alert')).textContent).toMatch(/line 7, column 9/i);
    expect(screen.queryByRole('textbox', {name: 'YAML editor'})).toBeNull();
    expect(saveButton().disabled).toBe(true);
    fireEvent.click(saveButton());
    expect(axios.post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name: /reload/i}));
    const editor = await screen.findByRole('textbox', {name: 'YAML editor'});
    expect((editor as HTMLTextAreaElement).value).toBe('settings: {}');
    expect(saveButton().disabled).toBe(true);
    fireEvent.change(editor, {target: {value: '   '}});
    expect(saveButton().disabled).toBe(true);
    expect(read.mock.calls).toEqual([[true], [true]]);
    expect(log).not.toHaveBeenCalled();
});

it('preserves a failed replacement draft without logging request or import data', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => {});
    const value = crypto.randomUUID();
    const draft = 'api_key: ' + value;
    read.mockResolvedValueOnce(valid);
    vi.mocked(axios.post).mockRejectedValue({config: {data: draft, headers: {Authorization: value}}, message: value});
    const {container} = render(<RawYamlPage />);
    const editor = await screen.findByRole('textbox', {name: 'YAML editor'});
    fireEvent.change(editor, {target: {value: draft}});
    fireEvent.click(saveButton());
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect((editor as HTMLTextAreaElement).value).toBe(draft);
    expect(saveButton().disabled).toBe(false);
    const file = new File(['archive'], 'backup.zip', {type:'application/zip'});
    fireEvent.change(container.querySelector('input[type=file]')!, {target: {files:[file]}});
    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(2));
    expect(JSON.stringify(vi.mocked(toast.error).mock.calls)).not.toContain(value);
    expect(log).not.toHaveBeenCalled();
});

it('opens a deliberate override repair, keeps revision on save, and offers reload after a concurrent repair', async () => {
    const revision = crypto.randomUUID();
    read.mockRejectedValueOnce({response: {status: 400, data: {detail: {
        message: 'Invalid YAML at line 2, column 4.', repair: {revision, partial: false, content: ''},
    }}}}).mockResolvedValueOnce(valid);
    vi.mocked(axios.post).mockRejectedValueOnce({response: {status: 409, data: {detail: 'Configuration changed. Reload before repairing.'}}});
    render(<RawYamlPage />);
    fireEvent.click(await screen.findByRole('button', {name: /repair override/i}));
    const editor = await screen.findByRole('textbox', {name: 'YAML editor'});
    expect(saveButton().disabled).toBe(true);
    fireEvent.change(editor, {target: {value: 'logging: {}'}});
    fireEvent.click(saveButton());
    await waitFor(() => expect(axios.post).toHaveBeenCalledWith('/api/config/yaml', {content: 'logging: {}', repair_revision: revision}));
    expect((await screen.findByRole('alert')).textContent).toMatch(/reload/i);
    expect((editor as HTMLTextAreaElement).value).toBe('logging: {}');
    fireEvent.click(screen.getByRole('button', {name: /reload/i}));
    await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(valid.content));
});
