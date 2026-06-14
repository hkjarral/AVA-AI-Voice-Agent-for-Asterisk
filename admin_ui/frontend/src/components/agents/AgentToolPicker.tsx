import React, { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { FormLabel } from '../ui/FormComponents';
import HelpTooltip from '../ui/HelpTooltip';
import {
    ToolDef, AgentToolState, phaseOf, isToolChecked, isToolLocked, toggleTool,
} from './agentToolConfig';

interface Props {
    catalog: ToolDef[];
    catalogError: boolean;
    state: AgentToolState;
    onChange: (next: AgentToolState) => void;
}

const PHASES: { key: 'pre_call' | 'in_call' | 'post_call'; label: string; hint: string }[] = [
    { key: 'pre_call', label: 'Pre-call', hint: 'runs after answer, before the agent speaks' },
    { key: 'in_call', label: 'In-call', hint: 'the agent can call these mid-conversation' },
    { key: 'post_call', label: 'Post-call', hint: 'runs after hangup, fire-and-forget' },
];

const SOURCE_BADGE: Record<string, string> = {
    builtin: 'bg-indigo-100 text-indigo-700',
    http: 'bg-green-100 text-green-700',
    mcp: 'bg-amber-100 text-amber-700',
};

const AgentToolPicker: React.FC<Props> = ({ catalog, catalogError, state, onChange }) => {
    const [open, setOpen] = useState<Record<string, boolean>>(
        { pre_call: false, in_call: true, post_call: false });

    if (catalogError) {
        return (
            <div className="space-y-2">
                <FormLabel tooltip="Choose which tools this agent can use in each call phase.">Tools</FormLabel>
                <div className="text-sm text-muted-foreground border border-border rounded-lg p-3 bg-muted/20">
                    Couldn&apos;t load the tool catalog (is the AI engine running?). Existing tool selections are preserved on save.
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-3">
            <FormLabel tooltip="Choose which tools this agent can use in each call phase. Global tools are on for every agent unless you turn them off here.">Tools</FormLabel>
            {PHASES.map(({ key, label, hint }) => {
                const tools = catalog
                    .filter((t) => phaseOf(t) === key)
                    .sort((a, b) => a.name.localeCompare(b.name));
                const selected = tools.filter((t) => isToolChecked(state, t)).length;
                return (
                    <div key={key} className="border border-border rounded-lg overflow-hidden">
                        <button
                            type="button"
                            onClick={() => setOpen((o) => ({ ...o, [key]: !o[key] }))}
                            className="w-full flex items-center justify-between px-4 py-3 bg-muted/30 hover:bg-muted/50 transition-colors"
                        >
                            <span className="flex items-center gap-2">
                                {open[key] ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                                <span className="font-medium text-sm">{label}</span>
                                <span className="text-xs text-muted-foreground">{hint}</span>
                            </span>
                            <span className="text-xs text-muted-foreground">{selected} selected</span>
                        </button>
                        {open[key] && (
                            <div className="divide-y divide-border">
                                {tools.length === 0 && (
                                    <p className="px-4 py-3 text-xs text-muted-foreground">No {label.toLowerCase()} tools available.</p>
                                )}
                                {tools.map((tool) => {
                                    const locked = isToolLocked(state, tool);
                                    const checked = isToolChecked(state, tool);
                                    return (
                                        <label
                                            key={tool.name}
                                            className={`flex items-center gap-3 px-4 py-2.5 ${locked ? 'bg-amber-50/40' : 'cursor-pointer hover:bg-accent/40'}`}
                                        >
                                            <input
                                                type="checkbox"
                                                className="rounded border-input text-primary focus:ring-primary"
                                                checked={checked}
                                                disabled={locked}
                                                onChange={() => onChange(toggleTool(state, tool))}
                                            />
                                            <span className="text-sm font-medium">{tool.name}</span>
                                            {tool.description ? <HelpTooltip content={tool.description} /> : null}
                                            {tool.source ? (
                                                <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold ${SOURCE_BADGE[tool.source] || 'bg-muted text-muted-foreground'}`}>
                                                    {tool.source}
                                                </span>
                                            ) : null}
                                            {tool.is_global ? (
                                                <span className="text-[10px] px-1.5 py-0.5 rounded-full font-semibold bg-muted text-muted-foreground">global</span>
                                            ) : null}
                                            {locked ? (
                                                <span className="text-[11px] text-amber-700 ml-auto">configured inline</span>
                                            ) : tool.is_global ? (
                                                <span className="text-[11px] text-muted-foreground ml-auto">on by default</span>
                                            ) : null}
                                        </label>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
};

export default AgentToolPicker;
