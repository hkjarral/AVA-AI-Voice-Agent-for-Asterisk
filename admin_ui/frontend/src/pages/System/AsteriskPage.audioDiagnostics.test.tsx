// @vitest-environment jsdom

import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AsteriskPage from './AsteriskPage';

vi.mock('axios');

describe('AsteriskPage optional diagnostics', () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it('does not report optional recording prerequisites as required-module issues', async () => {
        vi.mocked(axios.get).mockResolvedValue({
            data: {
                mode: 'local',
                live: {
                    ari_reachable: true,
                    asterisk_version: '20.0.0',
                    uptime: null,
                    last_reload: null,
                    app_registered: true,
                    app_name: 'asterisk-ai-voice-agent',
                    modules: {
                        app_audiosocket: 'Running',
                        res_ari: 'Running',
                    },
                },
                manifest: {
                    timestamp: '2026-07-22T00:00:00Z',
                    asterisk_found: true,
                    asterisk_version: '20.0.0',
                    config_dir: '/etc/asterisk',
                    freepbx: { detected: false, version: '' },
                    checks: {
                        module_format_wav: { ok: false, detail: 'Not loaded' },
                        recording_spool: { ok: false, detail: '/var/spool/asterisk/recording is not writable' },
                    },
                },
            },
        });

        render(<AsteriskPage />);

        expect(await screen.findByText('Optional Diagnostics')).toBeInTheDocument();
        expect(screen.getByText('WAV Recording Module')).toBeInTheDocument();
        expect(screen.getByText('Diagnostic Recording Spool')).toBeInTheDocument();
        expect(screen.getAllByText('Unavailable')).toHaveLength(2);

        const requiredModules = screen.getByText('Required Modules').closest('.mb-6');
        expect(requiredModules).not.toHaveTextContent('format_wav.so');
        expect(requiredModules).not.toHaveTextContent('Issue');
    });
});
