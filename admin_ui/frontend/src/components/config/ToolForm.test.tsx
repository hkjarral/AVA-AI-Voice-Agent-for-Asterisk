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

type TestToolConfig = {
    extensions?: {
        internal?: Record<string, {
            dial_string?: string;
            transfer?: boolean;
            device_states?: { id?: string; status?: string }[];
        }>;
    };
    check_extension_status?: {
        state_mapping?: { free?: string[]; busy?: string[]; unavailable?: string[] };
    };
};

const baseConfig = (): TestToolConfig => ({
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
const Harness = ({ onChange }: { onChange: (c: TestToolConfig) => void }) => {
    const [config, setConfig] = useState<TestToolConfig>(baseConfig());
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

        const statusSelect = screen.getByLabelText('Custom device state 1 status', { exact: true });
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

describe('ToolForm — global device-state value mapping (issue #577)', () => {
    it('shows the default buckets in the state value mapping panel', async () => {
        const user = userEvent.setup();
        render(<ToolForm config={baseConfig()} onChange={vi.fn()} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        await user.click(screen.getByRole('button', { name: /State Value Mapping/i }));

        expect(screen.getByLabelText('Free')).toHaveValue('NOT_INUSE');
        expect(screen.getByLabelText('Busy')).toHaveValue('INUSE BUSY RINGING RINGINUSE ONHOLD');
        expect(screen.getByLabelText('Not available')).toHaveValue('UNAVAILABLE INVALID UNKNOWN');
    });

    it('adds ONHOLD to the free bucket and reports it via onChange', async () => {
        const user = userEvent.setup();
        const onChange = vi.fn();

        render(<Harness onChange={onChange} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        await user.click(screen.getByRole('button', { name: /State Value Mapping/i }));

        const freeInput = screen.getByLabelText('Free');
        await user.type(freeInput, ' ONHOLD');
        await user.tab();

        expect(onChange).toHaveBeenCalled();
        const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        const mapping = lastCall.check_extension_status.state_mapping;
        expect(mapping.free).toContain('ONHOLD');
        expect(mapping.free).toContain('NOT_INUSE');
        expect(mapping.busy).toEqual(['INUSE', 'BUSY', 'RINGING', 'RINGINUSE']);
        expect(mapping.unavailable).toEqual(['UNAVAILABLE', 'INVALID', 'UNKNOWN']);
    });

    it('keeps an explicitly-cleared bucket empty when another bucket is edited afterward (CodeRabbit round 2)', async () => {
        const user = userEvent.setup();
        const onChange = vi.fn();

        render(<Harness onChange={onChange} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        await user.click(screen.getByRole('button', { name: /State Value Mapping/i }));

        // Move NOT_INUSE out of Free by adding it to Busy; the dedup logic removes it
        // from Free, leaving Free explicitly empty.
        const busyInput = screen.getByLabelText('Busy');
        await user.type(busyInput, ' NOT_INUSE');
        await user.tab();

        expect(screen.getByLabelText('Free')).toHaveValue('');
        let lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.check_extension_status.state_mapping.free).toEqual([]);

        // Editing an unrelated bucket must not silently restore Free's default.
        const unavailableInput = screen.getByLabelText('Not available');
        await user.type(unavailableInput, ' FOO');
        await user.tab();

        expect(screen.getByLabelText('Free')).toHaveValue('');
        lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.check_extension_status.state_mapping.free).toEqual([]);
    });

    it('resets the mapping to defaults and omits state_mapping from config', async () => {
        const user = userEvent.setup();
        const onChange = vi.fn();

        render(<Harness onChange={onChange} />);
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });

        await user.click(screen.getByRole('button', { name: /State Value Mapping/i }));

        const freeInput = screen.getByLabelText('Free');
        await user.type(freeInput, ' ONHOLD');
        await user.tab();

        await user.click(screen.getByRole('button', { name: /Reset to defaults/i }));

        expect(screen.getByLabelText('Free')).toHaveValue('NOT_INUSE');
        const lastCall = onChange.mock.calls[onChange.mock.calls.length - 1][0];
        expect(lastCall.check_extension_status?.state_mapping).toBeUndefined();
    });
});
