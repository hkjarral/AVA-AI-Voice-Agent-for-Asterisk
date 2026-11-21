// @vitest-environment jsdom
import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmDialogProvider } from '../../../hooks/useConfirmDialog';
import GrokProviderForm from './GrokProviderForm';

vi.mock('axios');

function Editor({ initialVoice }: { initialVoice?: string }) {
    const [config, setConfig] = useState({ voice: initialVoice });
    return (
        <ConfirmDialogProvider>
            <GrokProviderForm config={config} onChange={setConfig} />
        </ConfirmDialogProvider>
    );
}

const voices = ['eve', 'celeste', 'atlas'].map(id => ({ id, label: id }));

describe('Grok default voice', () => {
    it('recognizes a saved built-in regardless of case and selects another catalog entry', async () => {
        vi.mocked(axios.get).mockResolvedValue({ data: { voices } });
        render(<Editor initialVoice="Celeste" />);
        const select = await screen.findByRole('combobox', { name: 'Default Voice' });
        expect(select).toHaveValue('celeste');
        expect(screen.getByRole('group', { name: 'Built-in voices' }).children).toHaveLength(voices.length);
        fireEvent.change(select, { target: { value: 'atlas' } });
        expect(select).toHaveValue('atlas');
        expect(screen.queryByRole('textbox', { name: 'Custom voice ID' })).toBeNull();
    });

    it('reopens a clone and allows entering another whose prefix is a built-in ID', async () => {
        vi.mocked(axios.get).mockResolvedValue({ data: { voices } });
        render(<Editor initialVoice="MyClone_ID" />);
        const custom = await screen.findByRole('textbox', { name: 'Custom voice ID' });
        expect(custom).toHaveValue('MyClone_ID');
        fireEvent.focus(custom);
        fireEvent.change(custom, { target: { value: 'eve' } });
        fireEvent.change(custom, { target: { value: 'eveClone_A' } });
        fireEvent.blur(custom);
        expect(custom).toHaveValue('eveClone_A');
        const select = screen.getByRole('combobox', { name: 'Default Voice' });
        fireEvent.change(select, { target: { value: 'celeste' } });
        fireEvent.change(select, { target: { value: '' } });
        expect(screen.getByRole('textbox', { name: 'Custom voice ID' })).toHaveValue('');
    });

    it('preserves the configured ID when the catalog is unavailable', async () => {
        vi.mocked(axios.get).mockRejectedValue(new Error('offline'));
        render(<Editor initialVoice="MyClone_ID" />);
        await screen.findByText(/voice catalog unavailable/i);
        const input = screen.getByRole('textbox', { name: 'Default Voice' });
        expect(input).toHaveValue('MyClone_ID');
        fireEvent.change(input, { target: { value: 'MyClone_ID2' } });
        expect(input).toHaveValue('MyClone_ID2');
    });
});
