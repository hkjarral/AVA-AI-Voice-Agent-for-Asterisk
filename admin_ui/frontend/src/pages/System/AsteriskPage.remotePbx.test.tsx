// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest';
import { render, screen, waitFor } from '@testing-library/react';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AsteriskPage from './AsteriskPage';

vi.mock('axios', () => ({
    default: {
        get: vi.fn(),
    },
}));

const mockedGet = vi.mocked(axios.get);

const remoteStatus = {
    mode: 'remote',
    manifest: {
        timestamp: '2026-08-01T06:07:55Z',
        asterisk_found: false,
        asterisk_version: '',
        config_dir: '',
        freepbx: { detected: false, version: '' },
        checks: {},
    },
    live: {
        ari_reachable: true,
        asterisk_version: '22.0.0',
        uptime: null,
        last_reload: null,
        app_registered: true,
        app_name: 'asterisk-ai-voice-agent',
        modules: { res_ari: 'Running' },
    },
};

describe('AsteriskPage remote PBX checklist', () => {
    beforeEach(() => {
        mockedGet.mockReset();
        mockedGet.mockResolvedValue({ data: remoteStatus });
    });

    it('does not present an empty local manifest as a failed preflight', async () => {
        render(<AsteriskPage />);

        await waitFor(() => expect(screen.getByText('Remote')).toBeInTheDocument());

        expect(screen.getByText(/Local Asterisk configuration checks do not apply/)).toBeInTheDocument();
        expect(screen.getByText(/Local Asterisk audit skipped for remote PBX/)).toBeInTheDocument();
        expect(screen.queryByText(/Preflight checks were not run/)).not.toBeInTheDocument();
        expect(screen.queryByText(/Run .*preflight\.sh.*manifest with checks/)).not.toBeInTheDocument();
    });
});
