// @vitest-environment jsdom
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseSseMessages, useLiveStatus, type LiveStatusSnapshot } from './useLiveStatus';

vi.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ token: 'test-token' }),
}));

const snapshot = (eventId: number, state = 'ready'): LiveStatusSnapshot => ({
  version: 1,
  event_id: eventId,
  generated_at: '2026-06-26T00:00:00Z',
  summary: { state, component_count: 1 },
  components: {
    ai_engine: {
      state,
      freshness: 'fresh',
      summary: 'AI Engine connected',
      source: 'probe',
      updated_at: '2026-06-26T00:00:00Z',
      details: {},
      metrics: {},
      warnings: [],
      errors: [],
    },
  },
});

describe('parseSseMessages', () => {
  it('parses complete messages and returns partial rest', () => {
    const parsed = parseSseMessages(
      'id: 7\nevent: snapshot\ndata: {"ok":true}\n\nid: 8\nevent: snapshot\n'
    );

    expect(parsed.messages).toEqual([
      { id: '7', event: 'snapshot', data: '{"ok":true}' },
    ]);
    expect(parsed.rest).toBe('id: 8\nevent: snapshot\n');
  });

  it('joins multi-line data fields', () => {
    const parsed = parseSseMessages('event: snapshot\ndata: {"a":1,\ndata: "b":2}\n\n');

    expect(parsed.messages[0].data).toBe('{"a":1,\n"b":2}');
  });
});

describe('useLiveStatus', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('fetches the initial snapshot with the bearer token', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => snapshot(1),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
      });
    vi.stubGlobal('fetch', fetchMock);

    const { result, unmount } = renderHook(() => useLiveStatus());

    await waitFor(() => expect(result.current.snapshot?.event_id).toBe(1));
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/system/live-status',
      expect.objectContaining({
        headers: { Authorization: 'Bearer test-token' },
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/system/live-status/stream',
      expect.objectContaining({
        headers: { Authorization: 'Bearer test-token' },
      }),
    );
    expect(result.current.mode).toBe('poll');
    expect(result.current.connected).toBe(false);

    unmount();
  });

  it('applies snapshots received from the SSE stream', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          `id: 2\nevent: snapshot\ndata: ${JSON.stringify(snapshot(2, 'degraded'))}\n\n`
        ));
      },
      cancel() {},
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => snapshot(1),
      })
      .mockResolvedValueOnce({
        ok: true,
        body: stream,
      });
    vi.stubGlobal('fetch', fetchMock);

    const { result, unmount } = renderHook(() => useLiveStatus());

    await waitFor(() => expect(result.current.snapshot?.event_id).toBe(2));
    expect(result.current.snapshot?.summary.state).toBe('degraded');
    expect(result.current.connected).toBe(true);
    expect(result.current.mode).toBe('stream');

    unmount();
  });

  it('retries the SSE stream after falling back to polling', async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(
          `id: 3\nevent: snapshot\ndata: ${JSON.stringify(snapshot(3, 'ready'))}\n\n`
        ));
      },
      cancel() {},
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => snapshot(1),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => snapshot(2, 'degraded'),
      })
      .mockResolvedValueOnce({
        ok: true,
        body: stream,
      });
    vi.stubGlobal('fetch', fetchMock);

    const { result, unmount } = renderHook(() => useLiveStatus(10));

    await waitFor(() => expect(result.current.snapshot?.event_id).toBe(3));
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/system/live-status',
      '/api/system/live-status/stream',
      '/api/system/live-status',
      '/api/system/live-status/stream',
    ]);
    expect(result.current.connected).toBe(true);
    expect(result.current.mode).toBe('stream');

    unmount();
  });
});
