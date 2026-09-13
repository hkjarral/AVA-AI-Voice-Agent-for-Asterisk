// @vitest-environment jsdom

import { act, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import LogsPage from './LogsPage';

vi.mock('axios');

describe('LogsPage polling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(axios.get).mockReset();
    Element.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('does not overlap automatic log requests', async () => {
    let resolveFirst: (value: any) => void = () => {};
    const firstRequest = new Promise((resolve) => {
      resolveFirst = resolve;
    });
    vi.mocked(axios.get)
      .mockReturnValueOnce(firstRequest as any)
      .mockResolvedValue({ data: { logs: 'ready' } });

    render(
      <MemoryRouter initialEntries={['/logs?mode=raw']}>
        <LogsPage />
      </MemoryRouter>,
    );

    await act(async () => Promise.resolve());
    expect(axios.get).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(12000);
    });
    expect(axios.get).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveFirst({ data: { logs: 'first' } });
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
    });

    expect(axios.get).toHaveBeenCalledTimes(2);
  });
});
