export type SecretMarker = { set: boolean };
export type EnvValue = string | SecretMarker;

export function isSecretMarker(value: unknown): value is SecretMarker {
    return typeof value === 'object' && value !== null && 'set' in value && typeof value.set === 'boolean';
}

export function envString(value: EnvValue | undefined): string {
    return typeof value === 'string' ? value : '';
}

export function hasEnvValue(value: EnvValue | undefined): boolean {
    return isSecretMarker(value) ? value.set : Boolean(value);
}

export function envStrings(values: Record<string, EnvValue>): Record<string, string> {
    return Object.fromEntries(Object.entries(values).filter((entry): entry is [string, string] => typeof entry[1] === 'string'));
}
