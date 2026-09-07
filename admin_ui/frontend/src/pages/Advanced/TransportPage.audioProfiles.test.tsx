// @vitest-environment jsdom

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import yaml from 'js-yaml';

import TransportPage from './TransportPage';

const mocks = vi.hoisted(() => ({
    config: {
        audio_transport: 'externalmedia',
        asterisk: {
            app_name: 'asterisk-ai-voice-agent',
            ws_ping_interval_sec: 10,
            ws_ping_timeout_sec: 10,
        },
        external_media: {
            rtp_host: '127.0.0.1',
            rtp_port: 18080,
            codec: 'ulaw',
            format: 'slin16',
            sample_rate: 16000,
        },
        llm: { prompt: 'Keep the operator prompt unchanged.' },
        providers: { local: { enabled: true, base_url: 'ws://provider.example.test' } },
        pipelines: {
            voice: { stt: 'local_stt', llm: 'native_llm', tts: 'local_tts' },
        },
        profiles: { default: 'telephony_ulaw_8k' },
        contexts: { sales: { prompt: 'Keep the agent context unchanged.' } },
        websocket_media: {
            connection_name: 'aava_media',
            auth: { password_env: 'ASTERISK_MEDIA_WS_PASSWORD' },
        },
        tools: { lookup: { enabled: true } },
    },
    post: vi.fn().mockResolvedValue({
        data: { recommended_apply_method: 'restart' },
    }),
    toastError: vi.fn(),
    restartRequired: false,
    websocketStatus: {
        config: {},
        secret_reference: 'ASTERISK_MEDIA_WS_PASSWORD',
        secret_present: false,
        secret_source: 'ai_engine env_file (.env)',
    },
    asteriskStatus: {
        live: {
            asterisk_version: null,
            modules: {},
            websocket_media_supported: false,
            websocket_media_reason:
                'Asterisk version is unavailable; WebSocket activation must fail closed.',
        },
    },
    updaterImageStatus: { status: 'idle', phase: 'idle', message: '' },
    systemHealth: {
        ai_engine: {
            status: 'connected',
            details: {
                audio_transport: 'externalmedia',
                websocket_media: {
                    listening: false,
                    modules_ready: false,
                    module_inventory_available: false,
                    missing_modules: [],
                    non_running_modules: [],
                    timing_modules: [],
                    module_reason: 'Asterisk module inventory is unavailable',
                },
            },
        },
    },
}));

vi.mock('sonner', () => ({
    toast: {
        error: mocks.toastError,
        success: vi.fn(),
        warning: vi.fn(),
        info: vi.fn(),
    },
}));
vi.mock('../../hooks/useConfirmDialog', () => ({
    useConfirmDialog: () => ({ confirm: vi.fn() }),
}));
vi.mock('../../hooks/useRestartRequired', () => ({
    useRestartRequired: () => ({ restartRequired: mocks.restartRequired, refetch: vi.fn() }),
}));
vi.mock('axios', () => ({
    default: {
        get: vi.fn((url: string) => {
            if (url === '/api/config/websocket-media-status') {
                return Promise.resolve({ data: mocks.websocketStatus });
            }
            if (url === '/api/system/asterisk-status') {
                return Promise.resolve({ data: mocks.asteriskStatus });
            }
            if (url === '/api/system/health') {
                return Promise.resolve({ data: mocks.systemHealth });
            }
            if (url === '/api/system/updates/updater-image/status') {
                return Promise.resolve({ data: { status: mocks.updaterImageStatus } });
            }
            return Promise.reject(new Error(`Unexpected GET ${url}`));
        }),
        post: mocks.post,
        isAxiosError: vi.fn(
            (error: unknown) =>
                typeof error === 'object' && error !== null && 'isAxiosError' in error
        ),
    },
}));
vi.mock('../../utils/configCache', () => ({
    getCachedConfig: () => ({ config: mocks.config, yamlError: null }),
    loadConfigYaml: vi.fn().mockResolvedValue({ config: mocks.config, yamlError: null }),
}));

describe('TransportPage audio profile guidance', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocks.config.audio_transport = 'externalmedia';
        mocks.config.websocket_media = {
            connection_name: 'aava_media',
            auth: { password_env: 'ASTERISK_MEDIA_WS_PASSWORD' },
        };
        mocks.restartRequired = false;
        mocks.updaterImageStatus = { status: 'idle', phase: 'idle', message: '' };
        mocks.websocketStatus = {
            config: {},
            secret_reference: 'ASTERISK_MEDIA_WS_PASSWORD',
            secret_present: false,
            secret_source: 'ai_engine env_file (.env)',
        };
        mocks.asteriskStatus = {
            live: {
                asterisk_version: null,
                modules: {},
                websocket_media_supported: false,
                websocket_media_reason:
                    'Asterisk version is unavailable; WebSocket activation must fail closed.',
            },
        };
        mocks.systemHealth = {
            ai_engine: {
                status: 'connected',
                details: {
                    audio_transport: 'externalmedia',
                    websocket_media: {
                        listening: false,
                        modules_ready: false,
                        module_inventory_available: false,
                        missing_modules: [],
                        non_running_modules: [],
                        timing_modules: [],
                        module_reason: 'Asterisk module inventory is unavailable',
                    },
                },
            },
        };
    });

    it('shows the supported profile boundary for ExternalMedia RTP', async () => {
        render(<TransportPage />);

        expect(await screen.findByText('Supported Audio Profiles')).toBeInTheDocument();
        expect(screen.getByText('telephony_ulaw_8k')).toBeInTheDocument();
        expect(screen.getByText('telephony_enhanced_8k')).toBeInTheDocument();
        expect(screen.getByText('wideband_pcm_16k')).toBeInTheDocument();
        expect(screen.getByText(/AudioSocket-only/i)).toBeInTheDocument();
    });

    it('shows the bounded ARI keepalive controls', async () => {
        render(<TransportPage />);

        const interval = await screen.findByLabelText('ARI Ping Interval (seconds)');
        const timeout = screen.getByLabelText('ARI Ping Timeout (seconds)');

        expect(interval).toHaveValue(10);
        expect(timeout).toHaveValue(10);

        fireEvent.change(interval, { target: { value: '99' } });
        expect(interval).toHaveValue(60);
        fireEvent.change(interval, { target: { value: '1' } });
        expect(interval).toHaveValue(5);
        fireEvent.change(timeout, { target: { value: '' } });
        expect(timeout).toHaveValue(10);
    });

    it('defaults to JSON and offers explicit experimental legacy controls', async () => {
        render(<TransportPage />);

        const selector = await screen.findByLabelText('Transport Method');
        fireEvent.change(selector, { target: { value: 'websocket' } });

        expect(screen.getByText('Asterisk Media WebSocket Settings')).toBeInTheDocument();
        expect(screen.getByLabelText('Fallback Wire Format')).toBeInTheDocument();
        expect(screen.getByLabelText('Control Format')).toHaveValue('json');
        fireEvent.change(screen.getByLabelText('Control Format'), { target: { value: 'auto' } });
        expect(screen.getByLabelText('Control Format')).toHaveValue('auto');
        expect(screen.getByText(/No fallback after connection errors/)).toBeInTheDocument();
        expect(screen.getByText(/existing Stasis dialplan/i)).toBeInTheDocument();
        expect(screen.getByText(/OpenAI Realtime flow with/i)).toBeInTheDocument();
        expect(screen.getByText(/Other providers.*remain unqualified/i)).toBeInTheDocument();
        expect(screen.getByRole('link', { name: 'Environment' })).toHaveAttribute('href', '/env');

        const preStartBuffer = screen.getByLabelText('Pre-start Buffer (ms)');
        fireEvent.change(preStartBuffer, { target: { value: '0' } });
        expect(preStartBuffer).toHaveValue(0);
    });

    it('preserves unrelated operator configuration through all transport switches', async () => {
        render(<TransportPage />);

        const selector = await screen.findByLabelText('Transport Method');
        fireEvent.change(selector, { target: { value: 'audiosocket' } });
        fireEvent.change(selector, { target: { value: 'externalmedia' } });
        fireEvent.change(selector, { target: { value: 'websocket' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));

        await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
        const { content } = mocks.post.mock.calls[0][1] as { content: string };
        const saved = yaml.load(content) as typeof mocks.config;

        expect(saved.audio_transport).toBe('websocket');
        expect(saved.llm.prompt).toBe('Keep the operator prompt unchanged.');
        expect(saved.providers.local.base_url).toBe('ws://provider.example.test');
        expect(saved.pipelines.voice).toEqual({
            stt: 'local_stt',
            llm: 'native_llm',
            tts: 'local_tts',
        });
        expect(saved.profiles).toEqual({ default: 'telephony_ulaw_8k' });
        expect(saved.contexts.sales.prompt).toBe('Keep the agent context unchanged.');
        expect(saved.tools.lookup.enabled).toBe(true);
        expect(saved.websocket_media.auth.password_env).toBe('ASTERISK_MEDIA_WS_PASSWORD');
    });

    it('renders a scalar WebSocket allowlist and saves an edited array', async () => {
        mocks.config.audio_transport = 'websocket';
        Object.assign(mocks.config.websocket_media, { allowed_remote_hosts: '127.0.0.1' });
        render(<TransportPage />);
        const hosts = await screen.findByLabelText('Allowed Asterisk Hosts');
        expect(hosts).toHaveValue('127.0.0.1');
        fireEvent.change(hosts, { target: { value: '127.0.0.1, ::1' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
        const saved = yaml.load(mocks.post.mock.calls[0][1].content) as {
            websocket_media: { allowed_remote_hosts: string[] };
        };
        expect(saved.websocket_media.allowed_remote_hosts).toEqual(['127.0.0.1', '::1']);
    });

    it.each([
        ['Maximum Connections', 'max_connections', 100, 1, 10000],
        ['Handshake Timeout (ms)', 'handshake_timeout_ms', 5000, 100, 60000],
        ['MEDIA_START Timeout (ms)', 'media_start_timeout_ms', 5000, 100, 60000],
        ['Drain Timeout (ms)', 'drain_timeout_ms', 30000, 1000, 120000],
        ['Pre-start Buffer (ms)', 'pre_start_buffer_ms', 200, 0, 5000],
    ] as const)(
        'keeps %s finite and bounded when edited',
        async (label, key, fallback, min, max) => {
            mocks.config.audio_transport = 'websocket';
            render(<TransportPage />);
            const input = await screen.findByLabelText(label);
            fireEvent.change(input, { target: { value: '' } });
            expect(input).toHaveValue(fallback);
            fireEvent.change(input, { target: { value: String(max + 1) } });
            expect(input).toHaveValue(max);
            fireEvent.change(input, { target: { value: String(min - 1) } });
            expect(input).toHaveValue(min);
            fireEvent.change(input, { target: { value: String(min + 1) } });
            expect(input).toHaveValue(min + 1);
            fireEvent.change(input, { target: { value: '' } });
            fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
            await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(1));
            const content = mocks.post.mock.calls[0][1].content;
            expect(content).not.toContain('.nan');
            const saved = yaml.load(content) as { websocket_media: Record<string, number> };
            expect(saved.websocket_media[key]).toBe(fallback);
        }
    );

    it('distinguishes an edited selection from the running transport and leaves unknown capability unverified', async () => {
        render(<TransportPage />);

        fireEvent.change(await screen.findByLabelText('Transport Method'), {
            target: { value: 'websocket' },
        });

        const readiness = screen.getByRole('status', { name: 'WebSocket transport readiness' });
        expect(readiness).toHaveTextContent('Saved: External Media (RTP)');
        expect(readiness).toHaveTextContent('Editor: WebSocket');
        expect(readiness).toHaveTextContent('Running: External Media (RTP)');
        expect(readiness).toHaveTextContent('Not verified');
        expect(readiness).not.toHaveTextContent('Unsupported');
    });

    it('labels the secret check as saved state and rechecks after an edited reference is saved', async () => {
        mocks.websocketStatus.secret_present = true;
        render(<TransportPage />);

        fireEvent.change(await screen.findByLabelText('Transport Method'), {
            target: { value: 'websocket' },
        });
        expect(screen.getByText('Present in saved .env')).toBeInTheDocument();

        fireEvent.change(screen.getByLabelText('Password Environment Variable'), {
            target: { value: 'NEW_MEDIA_SECRET' },
        });
        expect(screen.getByText('Save to check')).toBeInTheDocument();
        expect(
            screen.getByText(/does not prove the running engine loaded it/i)
        ).toBeInTheDocument();
    });

    it('never interpolates invalid values into the Asterisk template', async () => {
        render(<TransportPage />);

        fireEvent.change(await screen.findByLabelText('Transport Method'), {
            target: { value: 'websocket' },
        });
        const validTemplate = screen.getByLabelText('Validated websocket_client.conf template');
        expect(validTemplate).toHaveTextContent('password = REPLACE_WITH_SECRET');
        expect(validTemplate).not.toHaveTextContent('password = <');

        fireEvent.change(screen.getByLabelText('Advertise Host'), {
            target: { value: 'media.example.test\npassword = injected' },
        });

        expect(screen.getByLabelText('Advertise Host')).toHaveAttribute('aria-invalid', 'true');
        expect(
            screen.queryByLabelText('Validated websocket_client.conf template')
        ).not.toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent(
            'Invalid values are never interpolated'
        );

        fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
        expect(mocks.post).not.toHaveBeenCalled();
    });

    it('renders a bracketed IPv6 URI only after validating the bare address', async () => {
        render(<TransportPage />);

        fireEvent.change(await screen.findByLabelText('Transport Method'), {
            target: { value: 'websocket' },
        });
        fireEvent.change(screen.getByLabelText('Advertise Host'), {
            target: { value: '2001:db8::25' },
        });

        expect(screen.getByLabelText('Validated websocket_client.conf template')).toHaveTextContent(
            'uri = ws://[2001:db8::25]:8787/media'
        );
    });

    it('uses force-recreate when applying a saved WebSocket selection', async () => {
        mocks.restartRequired = true;
        mocks.config.audio_transport = 'websocket';
        mocks.systemHealth.ai_engine.details.audio_transport = 'websocket';
        mocks.systemHealth.ai_engine.details.websocket_media.listening = true;
        render(<TransportPage />);

        fireEvent.click(await screen.findByRole('button', { name: 'Recreate AI Engine' }));

        await waitFor(() => {
            expect(mocks.post).toHaveBeenCalledWith(
                '/api/system/containers/ai_engine/restart?force=false&recreate=true'
            );
        });
    });

    it('shows updater image build progress while a WebSocket recreate is in flight', async () => {
        mocks.restartRequired = true;
        mocks.config.audio_transport = 'websocket';
        mocks.systemHealth.ai_engine.details.audio_transport = 'websocket';
        mocks.updaterImageStatus = {
            status: 'running',
            phase: 'building',
            message: 'Building updater image from local source',
        };
        mocks.post.mockImplementationOnce(() => new Promise(() => {}));
        render(<TransportPage />);

        fireEvent.click(await screen.findByRole('button', { name: 'Recreate AI Engine' }));

        expect(
            await screen.findByText('Building updater image from local source')
        ).toBeInTheDocument();
    });

    it('shows the running engine module failure even when the saved-selection probe passes', async () => {
        mocks.config.audio_transport = 'websocket';
        mocks.asteriskStatus.live.modules = {
            chan_websocket: 'Running',
            res_websocket_client: 'Running',
            res_http_websocket: 'Running',
            res_ari_channels: 'Running',
        };
        mocks.systemHealth.ai_engine.details.audio_transport = 'websocket';
        mocks.systemHealth.ai_engine.details.websocket_media.listening = true;
        mocks.systemHealth.ai_engine.details.websocket_media.modules_ready = false;
        mocks.systemHealth.ai_engine.details.websocket_media.module_inventory_available = true;
        mocks.systemHealth.ai_engine.details.websocket_media.module_reason =
            'No running Asterisk timing module was found';
        render(<TransportPage />);

        expect(await screen.findByText('Probe passed')).toBeInTheDocument();
        expect(screen.getByText('Saved-selection PBX probe')).toBeInTheDocument();
        expect(screen.getByRole('alert')).toHaveTextContent(
            'Running WebSocket prerequisites failed'
        );
        expect(screen.getByRole('alert')).toHaveTextContent(
            'No running Asterisk timing module was found'
        );
    });

    it('shows the backend secret preflight detail when recreate is refused', async () => {
        mocks.config.audio_transport = 'websocket';
        mocks.restartRequired = true;
        mocks.post.mockRejectedValueOnce({
            isAxiosError: true,
            message: 'Request failed with status code 409',
            response: {
                data: {
                    detail: 'The referenced WebSocket secret is missing from the saved .env file.',
                },
            },
        });
        render(<TransportPage />);

        fireEvent.click(await screen.findByRole('button', { name: 'Recreate AI Engine' }));

        await waitFor(() => {
            expect(mocks.toastError).toHaveBeenCalledWith('Failed to recreate AI Engine', {
                description: 'The referenced WebSocket secret is missing from the saved .env file.',
            });
        });
    });
});
