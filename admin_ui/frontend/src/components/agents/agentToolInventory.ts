export interface TransferDestination {
    key: string;
    type: string;
    target: string;
    description: string;
    attendedAllowed?: boolean;
    liveAgent?: boolean;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
    !!value && typeof value === 'object' && !Array.isArray(value);

export function transferDestinationsFromConfig(value: unknown): TransferDestination[] {
    if (!isRecord(value)) return [];
    return Object.entries(value)
        .filter((entry): entry is [string, Record<string, unknown>] => isRecord(entry[1]))
        .map(([key, destination]) => ({
            key,
            type: String(destination.type || ''),
            target: String(destination.target || destination.extension || ''),
            description: String(destination.description || destination.name || ''),
            attendedAllowed: destination.attended_allowed === true,
            liveAgent: destination.live_agent === true,
        }));
}
