// @vitest-environment jsdom

import { act, fireEvent, render, screen } from '@testing-library/react';
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

  it('clears loading when switching away from an active raw-log request', async () => {
    const pendingRequest = new Promise(() => {});
    vi.mocked(axios.get).mockImplementation((url) => {
      if (url === '/api/calls/filters') {
        return Promise.resolve({ data: { providers: [], pipelines: [], contexts: [], outcomes: [] } });
      }
      return pendingRequest as any;
    });

    render(
      <MemoryRouter initialEntries={['/logs?mode=raw']}>
        <LogsPage />
      </MemoryRouter>,
    );

    await act(async () => Promise.resolve());
    expect((screen.getByTitle('Refresh Now') as HTMLButtonElement).disabled).toBe(true);

    await act(async () => {
      fireEvent.change(screen.getByTitle('Logs View'), { target: { value: 'troubleshoot' } });
      await Promise.resolve();
    });

    expect((screen.getByTitle('Refresh Now') as HTMLButtonElement).disabled).toBe(false);
  });

  it('serializes a manual refresh with the scheduled poll', async () => {
    let resolveManual: (value: any) => void = () => {};
    const manualRequest = new Promise((resolve) => {
      resolveManual = resolve;
    });
    vi.mocked(axios.get)
      .mockResolvedValueOnce({ data: { logs: 'automatic' } })
      .mockReturnValueOnce(manualRequest as any)
      .mockResolvedValue({ data: { logs: 'next automatic' } });

    render(
      <MemoryRouter initialEntries={['/logs?mode=raw']}>
        <LogsPage />
      </MemoryRouter>,
    );

    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByTitle('Refresh Now'));
    expect(axios.get).toHaveBeenCalledTimes(2);

    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
    });
    expect(axios.get).toHaveBeenCalledTimes(2);

    await act(async () => {
      resolveManual({ data: { logs: 'manual' } });
      await Promise.resolve();
    });
    await act(async () => {
      vi.advanceTimersByTime(3000);
      await Promise.resolve();
    });
    expect(axios.get).toHaveBeenCalledTimes(3);
  });

  it('offers context-specific exports instead of one unbounded download', async () => {
    vi.mocked(axios.get).mockResolvedValue({ data: { logs: '2026 INFO ready\n' } });
    window.URL.createObjectURL = vi.fn(() => 'blob:test');
    window.URL.revokeObjectURL = vi.fn();
    HTMLAnchorElement.prototype.click = vi.fn();

    render(
      <MemoryRouter initialEntries={['/logs?mode=raw']}>
        <LogsPage />
      </MemoryRouter>,
    );
    await act(async () => Promise.resolve());
    fireEvent.click(screen.getByRole('button', { name: /Export/ }));

    expect(screen.getByText('Download current view')).toBeTruthy();
    expect(screen.getByText('Call support package…')).toBeTruthy();
    fireEvent.click(screen.getByText('System diagnostics…'));
    expect(screen.getByRole('dialog', { name: 'Export system diagnostics' })).toBeTruthy();
    expect(screen.getByText(/can include events from multiple calls/i)).toBeTruthy();
  });
});
