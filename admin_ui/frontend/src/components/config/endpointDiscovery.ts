export interface EndpointCandidate {
    technology: string;
    resource: string;
    state: 'online' | 'offline' | 'unknown';
}

export interface DestinationAddition {
    key: string;
    target: string;
    description: string;
    dialplan_context: string;
}

export type DestinationMap = Record<string, unknown>;
export interface TransferConfig {
    destinations?: DestinationMap;
    extension_context?: unknown;
    [key: string]: unknown;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
    value !== null && typeof value === 'object' && !Array.isArray(value);

const hasControlCharacters = (value: string): boolean =>
    Array.from(value).some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);

export const endpointIdentity = (endpoint: EndpointCandidate) =>
    JSON.stringify([endpoint.technology, endpoint.resource]);

export const extensionContext = (transfer: TransferConfig | undefined): string =>
    String(transfer?.extension_context || '').trim() || 'from-internal';

export const effectiveRoute = (destination: unknown, context: string): string | null => {
    if (!isRecord(destination)
        || (destination.type || 'extension') !== 'extension') return null;
    return JSON.stringify([
        String(destination.target || '').trim(),
        String(destination.dialplan_context || destination.context || context).trim(),
    ]);
};

export function proposeAdditions(endpoints: EndpointCandidate[], existing: DestinationMap, context: string): DestinationAddition[] {
    const keys = new Set(Object.keys(existing));
    return endpoints.map(endpoint => {
        const base = ('ext_' + endpoint.resource.replace(/[^A-Za-z0-9_-]/g, '_')).slice(0, 54);
        let key = base;
        let suffix = 2;
        while (keys.has(key)) key = base + '_' + suffix++;
        keys.add(key);
        return {
            key,
            target: /^[0-9]{1,32}$/.test(endpoint.resource) ? endpoint.resource : '',
            description: '',
            dialplan_context: context,
        };
    });
}

export function additionErrors(rows: DestinationAddition[], existing: DestinationMap, context: string): string[] {
    const keys = new Set(Object.keys(existing));
    const routes = new Set(Object.values(existing).map(destination => effectiveRoute(destination, context)).filter(Boolean));
    return rows.map(row => {
        let error = '';
        if (row.key !== row.key.trim() || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(row.key)) error = 'Use a destination key of 1–64 letters, numbers, underscores, or hyphens.';
        else if (row.target !== row.target.trim() || !/^[0-9]{1,32}$/.test(row.target)) error = 'Confirm a dialable extension of 1–32 digits. Use manual setup for custom targets.';
        else if (!row.description.trim() || row.description.length > 200 || hasControlCharacters(row.description)) error = 'Enter a description of 1–200 characters without control characters.';
        else if (row.dialplan_context !== row.dialplan_context.trim() || !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}$/.test(row.dialplan_context)) error = 'Confirm a dialplan context of 1–80 letters, numbers, underscores, dots, or hyphens.';
        const route = JSON.stringify([row.target, row.dialplan_context]);
        if (!error && keys.has(row.key)) error = 'This destination key already exists. Choose a different key.';
        if (!error && routes.has(route)) error = 'An extension destination already uses this number and context. Go back and deselect it, or correct the number/context.';
        keys.add(row.key);
        routes.add(route);
        return error;
    });
}

export function stageAdditions(transfer: TransferConfig | undefined, rows: DestinationAddition[]) {
    const existing = transfer?.destinations || {};
    if (!rows.length || rows.length > 100) throw new Error('Select between 1 and 100 endpoints.');
    const error = additionErrors(rows, existing, extensionContext(transfer)).find(Boolean);
    if (error) throw new Error(error);
    const additions = Object.fromEntries(rows.map(row => [row.key, {
        type: 'extension', target: row.target, description: row.description.trim(),
        dialplan_context: row.dialplan_context, attended_allowed: false,
    }]));
    return { ...transfer, destinations: { ...existing, ...additions } };
}

const discoveryMessages: Record<string, string> = {
    not_configured: 'Configure your Asterisk connection before discovering endpoints.',
    authentication: 'Asterisk authentication failed. Check the configured ARI credentials.',
    forbidden: 'The configured ARI user cannot list endpoints.',
    unsupported: 'Endpoint discovery is unavailable. Check the Asterisk ARI endpoint module.',
    timeout: 'Endpoint discovery timed out. Retry when Asterisk is reachable.',
    connection: 'Could not connect to the configured Asterisk server.',
    tls: 'The configured Asterisk TLS connection could not be verified.',
    invalid_response: 'Asterisk returned an invalid endpoint inventory. No candidates were accepted.',
    too_large: 'The endpoint inventory exceeds the discovery limit. Use manual destination setup.',
    upstream: 'Asterisk could not return the endpoint inventory. Try again.',
};

// Never render arbitrary upstream/axios error text.
export const discoveryMessage = (error: unknown): string => {
    const value = isRecord(error) ? error : {};
    const response = isRecord(value.response) ? value.response : {};
    const data = isRecord(response.data) ? response.data : {};
    const detail = isRecord(data.detail) ? data.detail : {};
    const category = typeof detail.category === 'string' ? detail.category : '';
    return (Object.prototype.hasOwnProperty.call(discoveryMessages, category) ? discoveryMessages[category] : '')
        || (value.code === 'ECONNABORTED' ? discoveryMessages.timeout : 'Endpoint discovery failed. Check your Asterisk connection and retry.');
};

export function parseDiscovery(data: unknown): { endpoints: EndpointCandidate[]; discovered_at: string } {
    if (!isRecord(data) || !Array.isArray(data.endpoints) || data.endpoints.length > 5000
        || typeof data.discovered_at !== 'string' || !Number.isFinite(Date.parse(data.discovered_at))) {
        throw new Error('Invalid discovery response');
    }
    const identities = new Set<string>();
    const endpoints = data.endpoints.map((item: unknown) => {
        if (!isRecord(item) || typeof item.technology !== 'string' || !/^[A-Za-z][A-Za-z0-9_+-]{0,31}$/.test(item.technology)
            || typeof item.resource !== 'string' || !item.resource.length || item.resource.length > 256
            || item.resource !== item.resource.trim() || hasControlCharacters(item.resource)
            || (item.state !== 'online' && item.state !== 'offline' && item.state !== 'unknown')) throw new Error('Invalid endpoint');
        const candidate: EndpointCandidate = { technology: item.technology, resource: item.resource, state: item.state };
        const identity = endpointIdentity(candidate);
        if (identities.has(identity)) throw new Error('Duplicate endpoint');
        identities.add(identity);
        return candidate;
    });
    return { endpoints, discovered_at: data.discovered_at };
}
