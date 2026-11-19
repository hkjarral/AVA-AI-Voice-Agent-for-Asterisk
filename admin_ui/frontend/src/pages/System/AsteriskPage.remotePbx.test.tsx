// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
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

        expect(screen.getByText('Connected', { exact: true })).toBeInTheDocument();
        expect(screen.getByText('22.0.0', { exact: true })).toBeInTheDocument();
        const moduleRow = screen.getByText('res_ari.so', { exact: true }).closest('div.flex.items-center.justify-between');
        expect(moduleRow).not.toBeNull();
        expect(within(moduleRow as HTMLElement).getByText('Running', { exact: true })).toBeInTheDocument();
        expect(screen.getByText('Registered', { exact: true })).toBeInTheDocument();

        expect(screen.getByText(/Remote PBX file checks belong on the Asterisk host/)).toBeInTheDocument();
        expect(screen.getByText(/Local Asterisk audit skipped because preflight did not detect a local configuration directory/)).toBeInTheDocument();
        expect(screen.getByText(/Local Asterisk audit skipped on Ava host/)).toBeInTheDocument();
        expect(screen.queryByText(/Preflight checks were not run/)).not.toBeInTheDocument();
        expect(screen.queryByText(/Run .*preflight\.sh.*manifest with checks/)).not.toBeInTheDocument();
    });

    it('shows Ava-host audit results separately from remote PBX guidance', async () => {
        mockedGet.mockResolvedValue({
            data: {
                ...remoteStatus,
                manifest: {
                    ...remoteStatus.manifest,
                    asterisk_found: true,
                    checks: {
                        ari_enabled: { ok: true, detail: 'Loaded' },
                    },
                },
            },
        });

        render(<AsteriskPage />);

        await waitFor(() => expect(screen.getByText('Remote')).toBeInTheDocument());
        expect(screen.getByText(/Remote PBX file checks belong on the Asterisk host/)).toBeInTheDocument();
        expect(screen.getByText('Ava-host local Asterisk audit results')).toBeInTheDocument();
        expect(screen.getByText('ARI Enabled')).toBeInTheDocument();
        expect(screen.getByText('Loaded')).toBeInTheDocument();
        expect(screen.getByText(/Local Asterisk audit completed on Ava host/)).toBeInTheDocument();
    });

    it('distinguishes a missing Ava-host manifest from a skipped audit', async () => {
        mockedGet.mockResolvedValue({ data: { ...remoteStatus, manifest: null } });

        render(<AsteriskPage />);

        await waitFor(() => expect(screen.getByText('Remote')).toBeInTheDocument());
        expect(screen.getByText('Ava-host preflight manifest is unavailable.')).toBeInTheDocument();
        expect(screen.queryByText(/Local Asterisk audit skipped because/)).not.toBeInTheDocument();
    });
});
