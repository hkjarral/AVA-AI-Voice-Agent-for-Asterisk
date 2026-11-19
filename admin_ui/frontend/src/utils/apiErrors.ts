import axios from 'axios';

export type ApiErrorKind = 'network' | 'http' | 'unknown';

export type ApiErrorInfo = {
    kind: ApiErrorKind;
    endpoint?: string;
    status?: number;
    statusText?: string;
    message: string;
    detail?: string;
};

const coerceDetail = (data: unknown): string | undefined => {
    if (!data) return undefined;
    if (typeof data === 'string') return data;
    if (typeof data !== 'object') return String(data);

    const anyData = data as any;
    const val = anyData?.detail ?? anyData?.error ?? anyData?.message;
    if (val == null) return undefined;
    return typeof val === 'string' ? val : JSON.stringify(val);
};

export const describeApiError = (err: unknown, endpoint?: string): ApiErrorInfo => {
    if (axios.isAxiosError(err)) {
        if (!err.response) {
            return {
                kind: 'network',
                endpoint,
                message: err.message || 'Network error',
            };
        }

        return {
            kind: 'http',
            endpoint,
            status: err.response.status,
            statusText: err.response.statusText,
            message: err.message || `Request failed with status ${err.response.status}`,
            detail: coerceDetail(err.response.data),
        };
    }

    const message = err instanceof Error ? err.message : String(err);
    return { kind: 'unknown', endpoint, message };
};

export const apiErrorMessage = (err: unknown, fallback: string): string => {
    const info = describeApiError(err);
    return info.detail || info.message || fallback;
};

export const buildDockerAccessHints = (info: ApiErrorInfo): string[] => {
    const raw = `${info.detail || ''} ${info.message || ''}`.toLowerCase();

    const hints: string[] = [];

    if (info.kind === 'network') {
        hints.push('Admin UI backend may be down or still starting. Check admin_ui container logs.');
    }

    if (raw.includes('docker.sock') && raw.includes('permission denied')) {
        hints.push('Docker socket permission denied: DOCKER_GID likely does not match the socket GID (or admin_ui needs recreate).');
    } else if (raw.includes('docker.sock') && (raw.includes('no such file') || raw.includes('not found'))) {
        hints.push('Docker socket not found: Docker may be stopped, or DOCKER_SOCK is set to the wrong path (rootless vs rootful).');
    } else if (raw.includes('error while fetching server api version')) {
        hints.push('Docker SDK could not reach the daemon. This is usually a socket mount or permission issue inside admin_ui.');
    }

    hints.push('Verify Docker is running on the host and that admin_ui mounts the correct Docker socket.');
    hints.push('If you ran preflight or changed .env, recreate the container: docker compose -p asterisk-ai-voice-agent up -d --force-recreate admin_ui');
    return hints;
};

