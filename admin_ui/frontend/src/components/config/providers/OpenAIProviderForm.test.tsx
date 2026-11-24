// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';
import OpenAIProviderForm from './OpenAIProviderForm';

describe('OpenAI model reasoning compatibility', () => {
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
