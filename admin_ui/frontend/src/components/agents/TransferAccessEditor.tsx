import React, { useMemo, useState } from 'react';
import { AlertTriangle, Search } from 'lucide-react';

import type { AgentToolState } from './agentToolConfig';

export interface TransferDestination {
    key: string;
    type: string;
    target: string;
    description: string;
    attendedAllowed?: boolean;
    liveAgent?: boolean;
}

interface Props {
    destinations: TransferDestination[];
    state: AgentToolState;
    onChange: (next: AgentToolState) => void;
}

const TransferAccessEditor: React.FC<Props> = ({ destinations, state, onChange }) => {
    const [query, setQuery] = useState('');
    const knownKeys = useMemo(() => new Set(destinations.map(item => item.key)), [destinations]);
    const staleKeys = state.transferDestinationKeys.filter(key => !knownKeys.has(key));
    const filtered = destinations.filter(item => {
        const needle = query.trim().toLowerCase();
        if (!needle) return true;
        return [item.key, item.type, item.target, item.description].some(value =>
            value.toLowerCase().includes(needle)
        );
    });

    const setPolicy = (policy: AgentToolState['transferDestinationPolicy']) => {
        onChange({
            ...state,
            transferDestinationPolicy: policy,
            transferDestinationKeys: policy === 'selected' ? state.transferDestinationKeys : [],
        });
    };

    const toggleDestination = (key: string) => {
        const selected = state.transferDestinationKeys.includes(key);
        onChange({
            ...state,
            transferDestinationKeys: selected
                ? state.transferDestinationKeys.filter(item => item !== key)
                : [...state.transferDestinationKeys, key],
        });
    };

    return (
        <div className="border-t border-border bg-muted/20 px-4 py-3 space-y-3">
            <div>
                <p className="text-sm font-medium">Transfer destination access</p>
                <p className="text-xs text-muted-foreground mt-1">
                    This policy is shared by blind transfer, attended transfer, live-agent transfer,
                    and extension-status checks. Global tool disable always wins.
                </p>
            </div>
            <select
                aria-label="Transfer destination access policy"
                value={state.transferDestinationPolicy}
                onChange={event =>
                    setPolicy(event.target.value as AgentToolState['transferDestinationPolicy'])
                }
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm"
            >
                <option value="inherit">Inherit all global destinations</option>
                <option value="selected">Selected destinations only</option>
                <option value="none">No transfer destinations</option>
            </select>

            {state.transferDestinationPolicy === 'selected' && (
                <div className="space-y-2">
                    <div className="relative">
                        <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                        <input
                            aria-label="Search transfer destinations"
                            value={query}
                            onChange={event => setQuery(event.target.value)}
                            placeholder="Search destinations"
                            className="flex h-9 w-full rounded-md border border-input bg-background pl-9 pr-3 text-sm"
                        />
                    </div>
                    {destinations.length === 0 ? (
                        <p className="rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
                            No global destinations exist. Add routes on the Tools page before
                            assigning them.
                        </p>
                    ) : (
                        <div className="max-h-48 overflow-y-auto rounded-md border border-border bg-background divide-y divide-border">
                            {filtered.map(destination => (
                                <label
                                    key={destination.key}
                                    className="flex cursor-pointer items-start gap-3 px-3 py-2 hover:bg-accent/40"
                                >
                                    <input
                                        type="checkbox"
                                        className="mt-1 rounded border-input"
                                        checked={state.transferDestinationKeys.includes(
                                            destination.key
                                        )}
                                        onChange={() => toggleDestination(destination.key)}
                                    />
                                    <span className="min-w-0">
                                        <span className="block text-sm font-medium">
                                            {destination.key}
                                        </span>
                                        <span className="block text-xs text-muted-foreground">
                                            {destination.type} · {destination.target}
                                            {destination.description
                                                ? ` · ${destination.description}`
                                                : ''}
                                        </span>
                                    </span>
                                </label>
                            ))}
                        </div>
                    )}
                    {state.transferDestinationKeys.length === 0 && (
                        <p className="text-xs text-amber-700">
                            Nothing is selected. This agent will fail closed and cannot transfer.
                        </p>
                    )}
                    {staleKeys.length > 0 && (
                        <p className="flex items-start gap-1.5 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-800">
                            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            Missing global destinations: {staleKeys.join(', ')}. They remain denied
                            until recreated or removed here.
                        </p>
                    )}
                </div>
            )}
        </div>
    );
};

export default TransferAccessEditor;
