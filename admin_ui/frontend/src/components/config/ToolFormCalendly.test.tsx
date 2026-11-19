// @vitest-environment jsdom
import { act, render } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { expect, it, vi } from 'vitest';
import ToolForm from './ToolForm';
import type CalendlyToolForm from './CalendlyToolForm';

type ChildProps = ComponentProps<typeof CalendlyToolForm>;
const child = vi.hoisted(() => ({ props: null as ChildProps | null }));
vi.mock('./CalendlyToolForm', () => ({ default: (props: ChildProps) => { child.props = props; return null; } }));
vi.mock('axios', () => ({ default: { get: vi.fn(() => Promise.reject(new Error('no defaults'))), isCancel: () => false } }));

it('merges late Calendly save callbacks into the latest unrelated tool draft', async () => {
    const onChange = vi.fn();
    const onSaveNow = vi.fn();
    const original = { calendly_calendar: {}, transfer: { enabled: false } };
    const form = render(<ToolForm section="calendly_calendar" config={original} onChange={onChange} onSaveNow={onSaveNow} />);
    const pending = child.props!;
    const edited = { ...original, transfer: { enabled: true } };
    form.rerender(<ToolForm section="calendly_calendar" config={edited} onChange={onChange} onSaveNow={onSaveNow} />);
    await act(async () => {
        pending.onChange({ enabled: false, appointments: {} });
        await pending.onSave({ enabled: false, appointments: {} });
    });
    expect(onChange).toHaveBeenCalledWith({ ...edited, calendly_calendar: { enabled: false, appointments: {} } });
    expect(onSaveNow).toHaveBeenCalledWith({ ...edited, calendly_calendar: { enabled: false, appointments: {} } });
});
