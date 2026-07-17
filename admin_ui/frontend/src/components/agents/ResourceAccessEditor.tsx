import React, { useMemo } from 'react';
import { AlertTriangle } from 'lucide-react';

// Keep unknown persisted values representable so the editor cannot silently
// turn an invalid/rejected policy into broad global inheritance.
export type ResourcePolicy = string;

export interface AgentResourceOption {
    key: string;
    label: string;
    detail?: string;
}

interface Props {
    title: string;
    description: string;
    resourceName: string;
    options: AgentResourceOption[];
    policy: ResourcePolicy;
    selectedKeys: string[];
    onPolicyChange: (policy: ResourcePolicy) => void;
    onSelectedKeysChange: (keys: string[]) => void;
    single?: boolean;
}

const pluralize = (value: string): string => {
    if (/(s|x|z|ch|sh)$/i.test(value)) return `${value}es`;
    if (/[^aeiou]y$/i.test(value)) return `${value.slice(0, -1)}ies`;
    return `${value}s`;
};

const ResourceAccessEditor: React.FC<Props> = ({
    title,
    description,
    resourceName,
    options,
    policy,
    selectedKeys,
    onPolicyChange,
    onSelectedKeysChange,
    single = false,
}) => {
    const knownKeys = useMemo(() => new Set(options.map(option => option.key)), [options]);
    const staleKeys = selectedKeys.filter(key => !knownKeys.has(key));
    const pluralResourceName = pluralize(resourceName);
    const policyIsValid = ['inherit', 'selected', 'none'].includes(policy);

    const setPolicy = (next: ResourcePolicy) => {
        onPolicyChange(next);
    };

    const toggle = (key: string) => {
        if (single) {
            onSelectedKeysChange([key]);
            return;
        }
        onSelectedKeysChange(
            selectedKeys.includes(key)
                ? selectedKeys.filter(item => item !== key)
                : [...selectedKeys, key],
        );
    };

    return (
        <div className="border-t border-border bg-muted/20 px-4 py-3 space-y-3">
            <div>
                <p className="text-sm font-medium">{title}</p>
                <p className="text-xs text-muted-foreground mt-1">{description}</p>
            </div>
            <select
                aria-label={`${title} policy`}
                value={policy}
                onChange={event => setPolicy(event.target.value as ResourcePolicy)}
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm"
            >
                {!policyIsValid && (
                    <option value={policy}>Invalid saved policy: {policy}</option>
                )}
                <option value="inherit">Inherit globally configured {pluralResourceName}</option>
                <option value="selected">Selected {single ? resourceName : pluralResourceName} only</option>
                <option value="none">No {resourceName} access</option>
            </select>

            {policy === 'selected' && (
                <div className="space-y-2">
                    {options.length === 0 ? (
                        <p className="rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
                            No global {pluralResourceName} exist. Configure them on the Tools page first.
                        </p>
                    ) : (
                        <div className="max-h-48 overflow-y-auto rounded-md border border-border bg-background divide-y divide-border">
                            {options.map(option => (
                                <label
                                    key={option.key}
                                    className="flex cursor-pointer items-start gap-3 px-3 py-2 hover:bg-accent/40"
                                >
                                    <input
                                        type={single ? 'radio' : 'checkbox'}
                                        name={single ? `${title}-resource` : undefined}
                                        className="mt-1 border-input"
                                        checked={selectedKeys.includes(option.key)}
                                        onChange={() => toggle(option.key)}
                                    />
                                    <span className="min-w-0">
                                        <span className="block text-sm font-medium">{option.label}</span>
                                        <span className="block text-xs text-muted-foreground">
                                            {option.key}{option.detail ? ` · ${option.detail}` : ''}
                                        </span>
                                    </span>
                                </label>
                            ))}
                        </div>
                    )}
                    {selectedKeys.length === 0 && (
                        <p className="text-xs text-amber-700">
                            Nothing is selected. This Agent will fail closed for this tool.
                        </p>
                    )}
                    {staleKeys.length > 0 && (
                        <div className="rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
                            <p className="flex items-start gap-1.5">
                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            Missing global {pluralResourceName}: {staleKeys.join(', ')}. They remain denied
                            until recreated or removed here.
                            </p>
                            <div className="mt-2 flex flex-wrap gap-2">
                                {staleKeys.map(key => (
                                    <button
                                        key={key}
                                        type="button"
                                        className="rounded border border-amber-400 px-2 py-1 hover:bg-amber-100"
                                        onClick={() => onSelectedKeysChange(
                                            selectedKeys.filter(item => item !== key)
                                        )}
                                    >
                                        Remove {key}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default ResourceAccessEditor;
