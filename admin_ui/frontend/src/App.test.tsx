// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Link, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axios from 'axios';
import { SetupGuard } from './App';

vi.mock('axios');

const guardedRoutes = (
  <MemoryRouter initialEntries={['/one']}>
    <SetupGuard>
      <Routes>
        <Route path="/one" element={<><span>Page one</span><Link to="/two">Next</Link></>} />
        <Route path="/two" element={<span>Page two</span>} />
      </Routes>
    </SetupGuard>
  </MemoryRouter>
);

describe('SetupGuard', () => {
  beforeEach(() => {
    vi.mocked(axios.get).mockReset();
    vi.mocked(axios.isAxiosError).mockReset();
  });

  it('does not repeat setup detection on ordinary route changes', async () => {
    vi.mocked(axios.get).mockResolvedValue({ data: { configured: true } });
    render(guardedRoutes);

    await screen.findByText('Page one');
    fireEvent.click(screen.getByText('Next'));
    await screen.findByText('Page two');

    expect(axios.get).toHaveBeenCalledTimes(1);
  });

  it('recovers automatically from one transient setup probe failure', async () => {
    vi.mocked(axios.get)
      .mockRejectedValueOnce({ code: 'ERR_CANCELED' })
      .mockResolvedValueOnce({ data: { configured: true } });

    render(guardedRoutes);

    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2), { timeout: 2000 });
    expect(await screen.findByText('Page one')).toBeTruthy();
    expect(screen.queryByText('Backend unavailable')).toBeNull();
  });

  it('distinguishes an HTTP setup failure from an unreachable backend', async () => {
    vi.mocked(axios.isAxiosError).mockReturnValue(true);
    vi.mocked(axios.get).mockRejectedValue({ response: { status: 503 } });

    render(guardedRoutes);

    expect(await screen.findByText('Setup status check failed', {}, { timeout: 2000 })).toBeTruthy();
    expect(screen.getByText(/backend API returned HTTP 503/)).toBeTruthy();
    expect(screen.queryByText('Backend unavailable')).toBeNull();
    expect(screen.queryByText(/running and reachable/)).toBeNull();
  });
});
