// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { HTTPTemplateEditor, HTTPTemplateGuidance, useHTTPTemplateReview, type TemplateReview } from './HTTPTemplateGuidance';

vi.mock('axios', () => ({ default: { post: vi.fn() } }));

const review: TemplateReview = {
    valid: true, issues: [{ field: 'body_template', variable: 'crm_name', severity: 'warning', error_code: 'runtime_value_required', message: 'Runtime enrichment required.' }],
    references: [{ field: 'body_template', name: 'transcript_json', start: 0, end: 17, status: 'available', message: 'Available' }],
    variables: [{ name: 'transcript_json', type: 'array', source: 'call', description: 'Conversation so far', example: '[]' }],
    example_body: '{"transcript":{transcript_json}}',
    preview: { url: 'https://example.invalid', headers: {}, query_params: {}, body: '{"transcript":[]}' },
};

describe('HTTP template configuration guidance', () => {
    it('previews synthetic data without sending a test request and applies an example only on click', () => {
        const useExample = vi.fn();
        const refresh = vi.fn();
        render(<HTTPTemplateGuidance phase="in_call" review={review} error="" refresh={refresh} onUseExample={useExample} />);
        expect(useExample).not.toHaveBeenCalled();
        expect(screen.getByText(/Runtime enrichment required/)).toHaveClass('text-amber-700');
        fireEvent.click(screen.getByRole('button', { name: 'Validate & Preview' }));
        expect(refresh).toHaveBeenCalledOnce();
        expect(screen.getByLabelText('Synthetic request preview')).toHaveTextContent('https://example.invalid');
        expect(axios.post).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Use example' }));
        expect(useExample).toHaveBeenCalledWith(review.example_body);
    });

    it('shows definite errors and keeps editing possible', () => {
        render(<HTTPTemplateGuidance phase="in_call" review={{ ...review, valid: false, preview: null,
            issues: [{ field: 'body_template', variable: 'call_outcome', severity: 'error', error_code: 'unavailable_template_variable', message: 'Final outcome unavailable in-call.' }] }} error="" refresh={vi.fn()} onUseExample={vi.fn()} />);
        expect(screen.getByText(/Final outcome unavailable/)).toHaveClass('text-red-700');
        expect(screen.getByText(/Fix template errors/)).toBeInTheDocument();
        const change = vi.fn();
        render(<HTTPTemplateEditor aria-label="Body template" value="{transcript_json}" onChange={change} references={review.references} />);
        fireEvent.change(screen.getByRole('textbox', { name: 'Body template' }), { target: { value: '{}' } });
        expect(change).toHaveBeenCalled();
    });

    it('uses the backend validator rather than the live HTTP test endpoint', async () => {
        vi.mocked(axios.post).mockResolvedValue({ data: review });
        function Harness() {
            const state = useHTTPTemplateReview({ url: 'https://example.invalid', body_template: review.example_body }, 'in_call', 'example-token');
            return <span>{state.review?.valid ? 'validated' : 'waiting'}</span>;
        }
        render(<Harness />);
        await waitFor(() => expect(screen.getByText('validated')).toBeInTheDocument());
        expect(axios.post).toHaveBeenCalledWith('/api/tools/http-templates/validate', expect.objectContaining({ phase: 'in_call' }), expect.anything());
    });
});
