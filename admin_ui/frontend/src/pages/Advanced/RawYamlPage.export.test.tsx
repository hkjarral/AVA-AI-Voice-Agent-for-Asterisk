// @vitest-environment jsdom
import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import axios from 'axios';
import RawYamlPage from './RawYamlPage';

vi.mock('axios', () => ({ default: { post: vi.fn() } }));
vi.mock('@monaco-editor/react', () => ({ default: () => null }));
vi.mock('../../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: vi.fn() }) }));
vi.mock('../../utils/configCache', () => ({
    getCachedConfig: () => ({content: 'providers: {}'}),
    loadConfigYaml: async () => ({content: 'providers: {}'}),
}));

it('asks for the current password, shows a rejected entry inline and retries without losing the editor', async () => {
    const temporaryValue = crypto.randomUUID();
    const create = vi.fn(() => 'blob:fixture');
    vi.stubGlobal('URL', Object.assign(class {}, {createObjectURL: create, revokeObjectURL: vi.fn()}));
    const download = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    try {
        vi.mocked(axios.post).mockRejectedValueOnce({response:{status:401}})
            .mockResolvedValueOnce({data: new Blob()});
        render(<RawYamlPage />);
        fireEvent.click(screen.getByRole('button', {name: 'Export'}));
        const modal = within(await screen.findByRole('dialog'));
        const input = modal.getByLabelText(/current password/i);
        expect(input.getAttribute('type')).toBe('password');
        expect(axios.post).not.toHaveBeenCalled();
        fireEvent.change(input, {target:{value: temporaryValue}});
        fireEvent.click(modal.getByRole('button', {name:'Download backup'}));
        expect((await modal.findByRole('alert')).textContent).toMatch(/password/i);
        expect(create).not.toHaveBeenCalled();
        fireEvent.change(input, {target:{value: temporaryValue}});
        fireEvent.click(modal.getByLabelText(/include.*env/i));
        fireEvent.click(modal.getByRole('button', {name:'Download backup'}));
        await vi.waitFor(() => expect(download).toHaveBeenCalledOnce());
        expect(axios.post).toHaveBeenLastCalledWith('/api/config/export', {password:temporaryValue},
            {params:{include_secrets:true}, responseType:'blob'});
        expect(screen.queryByRole('dialog')).toBeNull();
    } finally {
        download.mockRestore();
        vi.unstubAllGlobals();
    }
});
