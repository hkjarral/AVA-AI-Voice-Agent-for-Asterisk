// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';
import OpenAIProviderForm from './OpenAIProviderForm';

describe('OpenAI model reasoning compatibility', () => {
    it.each([
        [{ tts_base_url: 'https://speech-gateway.example/v1/audio/speech' }, 'tts-1'],
        [{ tts_base_url: 'https://speech-gateway.example/v1/audio/speech', tts_model: '' }, 'tts-1'],
        [{ tts_transport: 'speech', tts_model: null }, 'tts-1'],
        [{ tts_transport: 'realtime', tts_base_url: 'https://speech-gateway.example/v1/audio/speech' }, 'gpt-realtime-2.1-mini'],
    ])('mirrors runtime transport fallback for %j', (settings, expected) => {
        const onChange = vi.fn();
        render(<OpenAIProviderForm config={{ name: 'openai_tts', ...settings }} onChange={onChange} />);
        expect(screen.getByRole('option', { name: expected === 'tts-1' ? 'tts-1 (deprecated)' : expected }).closest('select')).toHaveValue(expected);
        const voice = screen.getByRole('option', { name: 'alloy' }).closest('select')!;
        expect(Array.from(voice.options).some(option => option.value === 'fable')).toBe(expected === 'tts-1');
        expect(onChange).not.toHaveBeenCalled();
    });
    it.each([undefined, null, '', '   '])('keeps model and voice controls on the same default for blank model %s', tts_model => {
        const onChange = vi.fn();
        render(<OpenAIProviderForm config={{ name: 'openai_tts', tts_model }} onChange={onChange} />);
        expect(screen.getByRole('option', { name: 'gpt-realtime-2.1-mini' }).closest('select')).toHaveValue('gpt-realtime-2.1-mini');
        const voice = screen.getByRole('option', { name: 'marin' }).closest('select')!;
        expect(voice).toHaveValue('alloy');
        expect(Array.from(voice.options).map(option => option.value)).toContain('cedar');
        expect(Array.from(voice.options).map(option => option.value)).not.toContain('fable');
        expect(onChange).not.toHaveBeenCalled();
    });
    it.each([['none', null], ['high', 'high'], [undefined, undefined]])('selects GPT-6.2 with compatible reasoning from %s', (reasoning, expected) => {
        const onChange = vi.fn();
        const config = { name: 'openai_llm', chat_model: 'gpt-6-luna', reasoning_effort: reasoning };
        render(<OpenAIProviderForm config={config} onChange={onChange} />);
        expect(onChange).not.toHaveBeenCalled();
        fireEvent.change(screen.getByDisplayValue('gpt-6-luna'), { target: { value: 'gpt-6.2-sol' } });
        expect(onChange).toHaveBeenCalledWith({ ...config, chat_model: 'gpt-6.2-sol', reasoning_effort: expected });
    });

    it('preserves an existing incompatible value until the operator changes the model', () => {
        const onChange = vi.fn();
        render(<OpenAIProviderForm config={{ name: 'openai_llm', chat_model: 'gpt-6.2-sol', reasoning_effort: 'none' }} onChange={onChange} />);
        expect(screen.getByRole('option', { name: /^gpt-6.2-sol / }).closest('select')).toHaveValue('gpt-6.2-sol');
        expect(onChange).not.toHaveBeenCalled();
    });
});
