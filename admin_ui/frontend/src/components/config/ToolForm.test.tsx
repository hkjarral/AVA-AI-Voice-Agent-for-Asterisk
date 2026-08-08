// @vitest-environment jsdom
import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';

import ToolForm from './ToolForm';

// ToolForm fetches email-template defaults on mount; stub axios so the test
// doesn't attempt a real network call.
vi.mock('axios', () => ({
    default: {
        get: vi.fn(() => Promise.reject(new Error('not mocked'))),
        post: vi.fn(() => Promise.reject(new Error('not mocked'))),
        isCancel: vi.fn(() => false),
    },
}));

const baseConfig = () => ({
    extensions: {
        internal: {
            '102': {
                dial_string: 'PJSIP/102',
                transfer: true,
            },
        },
    },
});

// ToolForm is a fully controlled component (config in, onChange out). This
// harness mirrors what the real config page does — feed onChange's result
// back in as the next config — so DOM assertions after an interaction see
// the update, the same way they would in the app.
const Harness = ({ onChange }: { onChange: (c: any) => void }) => {
    const [config, setConfig] = useState<any>(baseConfig());
    return (
        <ToolForm
            config={config}
            onChange={(next) => {
                onChange(next);
                setConfig(next);
            }}
        />
    );
};

describe('ToolForm — per-extension availability signals (issue #577)', () => {
    it('adds a custom device state and reports it via onChange', async () => {
        const user = userEvent.setup();
        const onChange = vi.fn();

        render(<Harness onChange={onChange} />);
        // Let the mocked (rejecting) email-defaults fetch settle before interacting,
        // so its state update doesn't land outside of act() mid-test.
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        // Expert settings are gated behind the "Live Agent Expert Settings" switch.
        await user.click(screen.getByRole('checkbox', { name: /Live Agent Expert Settings/i }));

        await user.click(screen.getByRole('button', { name: /Add custom state/i }));

        const idInput = screen.getByPlaceholderText(/Custom:DND102/i);
        await user.type(idInput, 'Custom:DND102');

        const statusSelect = screen.getByLabelText('Status', { exact: true });
        await user.selectOptions(statusSelect, 'dnd');

        expect(onChange).toHaveBeenCalled();
        const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.extensions.internal['102'].device_states).toContainEqual({
            id: 'Custom:DND102',
            status: 'dnd',
        });
    });

    it('shows the auto-detection note for each internal extension', async () => {
        const user = userEvent.setup();
        render(<ToolForm config={baseConfig()} onChange={vi.fn()} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        await user.click(screen.getByRole('checkbox', { name: /Live Agent Expert Settings/i }));

        expect(screen.getByText(/Auto \(over ARI\)/i)).toBeInTheDocument();
    });
});
