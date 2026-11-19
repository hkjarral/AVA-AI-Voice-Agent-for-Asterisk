import { ChevronRight } from 'lucide-react';
import { ToolSection, toolSections } from './toolSections';

export function ToolList({ config, query, onSelect }: { config: Record<string, { enabled?: boolean } | undefined>; query: string; onSelect: (section: ToolSection) => void }) {
    const items = toolSections.filter(tool => `${tool.title} ${tool.description} ${tool.key}`.toLowerCase().includes(query.trim().toLowerCase()));
    return (
        <div className="space-y-7">
            {['Call handling', 'Business', 'Optional providers'].map(group => {
                const entries = items.filter(tool => tool.group === group);
                if (!entries.length) return null;
                return (
                    <section key={group} aria-label={group}>
                        <h2 className="text-xs uppercase tracking-wider text-muted-foreground mb-2">{group}</h2>
                        <div className="divide-y divide-border">
                            {entries.map(tool => {
                                // Match the form's defaults; calendars are opt-in.
                                const enabled = config[tool.key]?.enabled ?? !['google_calendar', 'microsoft_calendar', 'calendly_calendar'].includes(tool.key);
                                const Icon = tool.icon;
                                return (
                                    <div key={tool.key} className="flex items-center gap-3 py-4">
                                        <Icon className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                                        <div className="min-w-0 flex-1">
                                            <h3 className="font-medium text-sm">{tool.title}</h3>
                                            <p className="text-xs text-muted-foreground mt-1">{tool.description}</p>
                                            <span className="text-xs text-muted-foreground sm:hidden">{enabled ? 'Enabled' : 'Disabled'}</span>
                                        </div>
                                        <span className="hidden sm:inline text-xs text-muted-foreground">{enabled ? 'Enabled' : 'Disabled'}</span>
                                        <button type="button" onClick={() => onSelect(tool.key)} aria-label={`Configure ${tool.title}`} className="inline-flex items-center gap-1 rounded-md px-2 py-2 text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring">
                                            Configure <ChevronRight className="h-4 w-4" aria-hidden="true" />
                                        </button>
                                    </div>
                                );
                            })}
                        </div>
                    </section>
                );
            })}
            {items.length === 0 && <p className="text-sm text-muted-foreground">No built-in tools match this search.</p>}
        </div>
    );
}
