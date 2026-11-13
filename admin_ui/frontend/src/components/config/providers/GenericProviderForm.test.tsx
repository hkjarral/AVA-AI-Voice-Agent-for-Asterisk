// @vitest-environment jsdom
import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';

import GenericProviderForm from './GenericProviderForm';

vi.mock('./ProviderCredentialsCard', () => ({
    default: () => <div data-testid="provider-credentials" />,
}));

const fishAudioConfig = {
    name: 'fishaudio_tts',
    type: 'fishaudio',
    capabilities: ['tts'],
    enabled: true,
    base_url: 'https://api.fish.audio/v1',
    model: 's2.1-pro-free',
    reference_id: 'voice-reference',
    audio_format: 'pcm',
    latency: 'low',
    chunk_length: 200,
    temperature: 0.7,
    top_p: 0.7,
    connect_timeout_sec: 10,
    read_timeout_sec: 30,
    output_resampler: 'inherit',
    normalize: true,
};

describe('GenericProviderForm subtype fields', () => {
    it('shows subtype-owned settings once and keeps only advanced keys below', () => {
        render(<GenericProviderForm config={fishAudioConfig} onChange={vi.fn()} />);

        expect(screen.getByLabelText('Model *')).toHaveValue('s2.1-pro-free');
        expect(screen.getByLabelText('Voice Reference ID *')).toHaveValue('voice-reference');
        expect(screen.getByText('Additional Configuration Fields')).toBeInTheDocument();

        expect(screen.queryByDisplayValue('model')).not.toBeInTheDocument();
        expect(screen.queryByDisplayValue('reference_id')).not.toBeInTheDocument();
        expect(screen.queryByDisplayValue('audio_format')).not.toBeInTheDocument();
        expect(screen.getByDisplayValue('normalize')).toBeInTheDocument();
    });

    it('updates the structured model without a stale custom field overriding it', () => {
        const onChange = vi.fn();
        render(<GenericProviderForm config={fishAudioConfig} onChange={onChange} />);

        fireEvent.change(screen.getByLabelText('Model *'), { target: { value: 's2.1-pro' } });

        expect(onChange).toHaveBeenLastCalledWith({
            ...fishAudioConfig,
            model: 's2.1-pro',
        });
        expect(screen.queryByDisplayValue('model')).not.toBeInTheDocument();
    });

    it('preserves structured values while editing an additional field', () => {
        const onChange = vi.fn();
        render(<GenericProviderForm config={fishAudioConfig} onChange={onChange} />);

        fireEvent.change(screen.getByDisplayValue('true'), { target: { value: 'false' } });

        expect(onChange).toHaveBeenLastCalledWith({
            ...fishAudioConfig,
            normalize: false,
        });
    });
});

// Match ProvidersPage's merge semantics, including credential tombstones.
const FormHarness = ({ initial, isNew = true }: { initial: Record<string, unknown>; isNew?: boolean }) => {
    const [config, setConfig] = useState(initial);
    return <><GenericProviderForm config={config} isNew={isNew} onChange={next => setConfig(current => {
        const merged = { ...current, ...next };
        Object.keys(merged).forEach(key => { if (merged[key] === undefined) delete merged[key]; });
        return merged;
    })} /><output data-testid="draft">{JSON.stringify(config)}</output></>;
};
const draft = () => JSON.parse(screen.getByTestId('draft').textContent || '{}');

describe('GenericProviderForm initialization and transitions', () => {
    it('allows naming a new preselected provider until its capability is explicitly chosen', () => {
        render(<FormHarness initial={{ name: 'custom_llm', type: 'openai', capabilities: ['llm'] }} />);
        expect(screen.getByLabelText('Provider Name')).toBeEnabled();
        fireEvent.change(screen.getByLabelText('Provider Name'), { target: { value: 'renamed_llm' } });
        fireEvent.click(screen.getByRole('checkbox', { name: 'Text-to-Speech (TTS)' }));
        expect(screen.getByLabelText('Provider Name')).toBeDisabled();
        expect(draft().name).toBe('renamed_tts');
    });
    it('uses TTS fields after an LLM switch and keeps the draft for switching back', () => {
        render(<FormHarness initial={{ name: 'custom_llm', type: 'openai', capabilities: ['llm'], chat_model: 'synthetic-model', custom_flag: false }} />);
        expect(screen.getByLabelText('Chat API Base URL *')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('checkbox', { name: 'Text-to-Speech (TTS)' }));
        expect(screen.queryByLabelText('Chat API Base URL *')).not.toBeInTheDocument();
        expect(screen.getByLabelText('TTS Provider Type')).toHaveValue('openai');
        expect(draft()).toMatchObject({ name: 'custom_tts', capabilities: ['tts'], chat_model: 'synthetic-model', custom_flag: false });
        fireEvent.click(screen.getByRole('checkbox', { name: 'Large Language Model (LLM)' }));
        expect(screen.getByLabelText('Chat API Base URL *')).toBeInTheDocument();
        expect(draft().chat_model).toBe('synthetic-model');
    });
    it('clears incompatible subtypes and allows choosing one for the new capability', () => {
        render(<FormHarness initial={{ name: 'custom_stt', type: 'deepgram', capabilities: ['stt'], stt_model: 'synthetic-stt' }} />);
        fireEvent.click(screen.getByRole('checkbox', { name: 'Large Language Model (LLM)' }));
        expect(screen.getByLabelText('LLM Provider Type')).toHaveValue('');
        fireEvent.change(screen.getByLabelText('LLM Provider Type'), { target: { value: 'openai' } });
        expect(screen.getByLabelText('Chat API Base URL *')).toBeInTheDocument();
        expect(draft()).toMatchObject({ type: 'openai', capabilities: ['llm'], stt_model: 'synthetic-stt' });
    });
    it('keeps explicit same-YAML-type choices through edits without duplicate managed fields', () => {
        render(<FormHarness initial={{ name: 'custom_llm', type: 'openai', capabilities: ['llm'], custom_flag: true }} />);
        fireEvent.change(screen.getByLabelText('LLM Provider Type'), { target: { value: 'deepseek' } });
        fireEvent.change(screen.getByLabelText('Chat API Base URL *'), { target: { value: 'https://example.com/custom' } });
        expect(screen.getByLabelText('LLM Provider Type')).toHaveValue('deepseek');
        expect(screen.queryByDisplayValue('chat_base_url')).not.toBeInTheDocument();
        expect(draft().custom_flag).toBe(true);
    });
    it('initializes a full agent once without dropping nested or custom settings', () => {
        render(<FormHarness initial={{ name: 'custom_agent', type: 'full', capabilities: ['stt', 'llm', 'tts'], model: 'custom-model', custom_flag: 0, nested: { preserve: true } }} isNew={false} />);
        expect(draft()).toMatchObject({ model: 'custom-model', custom_flag: 0, nested: { preserve: true }, continuous_input: true });
        fireEvent.click(screen.getByRole('checkbox', { name: 'Enabled' }));
        expect(draft()).toMatchObject({ enabled: false, model: 'custom-model', custom_flag: 0, nested: { preserve: true }, continuous_input: true });
    });
    it('keeps custom edits on rerender and reopen while existing capability stays locked', () => {
        const { unmount } = render(<FormHarness initial={fishAudioConfig} isNew={false} />);
        fireEvent.change(screen.getByDisplayValue('true'), { target: { value: 'false' } });
        fireEvent.click(screen.getByRole('checkbox', { name: 'Enabled' }));
        const saved = draft();
        unmount();
        render(<FormHarness initial={saved} isNew={false} />);
        expect(screen.getByDisplayValue('false')).toBeInTheDocument();
        expect(screen.getByLabelText('Model *')).toHaveValue('s2.1-pro-free');
        expect(screen.getByRole('checkbox', { name: 'Text-to-Speech (TTS)' })).toBeDisabled();
    });
    it('synchronizes a stale continuous-input field when entering full-agent mode', () => {
        render(<FormHarness initial={{ name: 'custom_llm', type: 'openai', capabilities: ['llm'], continuous_input: false, custom_option: 'keep' }} />);
        fireEvent.change(screen.getByLabelText('Provider Type'), { target: { value: 'full' } });
        expect(screen.queryByDisplayValue('false')).not.toBeInTheDocument();
        expect(screen.getByDisplayValue('true')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('checkbox', { name: 'Enabled' }));
        expect(draft()).toMatchObject({ continuous_input: true, custom_option: 'keep', enabled: false });
    });

    it('clears subtype ownership across full/modular switches and retains values', () => {
        render(<FormHarness initial={{ name: 'custom_llm', type: 'openai', capabilities: ['llm'], chat_model: 'keep-model' }} />);
        fireEvent.change(screen.getByLabelText('Provider Type'), { target: { value: 'full' } });
        expect(screen.queryByLabelText('LLM Provider Type')).not.toBeInTheDocument();
        expect(draft().continuous_input).toBe(true);
        fireEvent.change(screen.getByLabelText('Provider Type'), { target: { value: 'modular' } });
        fireEvent.click(screen.getByRole('checkbox', { name: 'Large Language Model (LLM)' }));
        expect(screen.getByLabelText('LLM Provider Type')).toHaveValue('');
        fireEvent.change(screen.getByLabelText('LLM Provider Type'), { target: { value: 'openai' } });
        expect(draft().chat_model).toBe('keep-model');
    });
});
