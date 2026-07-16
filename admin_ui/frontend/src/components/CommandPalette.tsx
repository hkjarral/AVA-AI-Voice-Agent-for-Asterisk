import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    LayoutDashboard,
    Server,
    Workflow,
    Wrench,
    Plug,
    Sliders,
    Activity,
    Zap,
    Brain,
    Radio,
    Globe,
    Container,
    FileText,
    Terminal,
    AlertTriangle,
    Code,
    HelpCircle,
    HardDrive,
    ArrowUpCircle,
    Phone,
    CalendarClock,
    Search,
} from 'lucide-react';

type PageEntry = {
    label: string;
    path: string;
    icon: React.ComponentType<{ className?: string }>;
    group: string;
    keywords?: string[];
};

const pages: PageEntry[] = [
    { label: 'Dashboard', path: '/', icon: LayoutDashboard, group: 'Overview' },
    { label: 'Call History', path: '/history', icon: Phone, group: 'Overview', keywords: ['calls', 'phone'] },
    { label: 'Call Scheduling', path: '/scheduling', icon: CalendarClock, group: 'Overview', keywords: ['schedule', 'calendar'] },
    { label: 'Setup Wizard', path: '/wizard', icon: Zap, group: 'Overview', keywords: ['setup', 'onboard'] },
    { label: 'Providers', path: '/providers', icon: Server, group: 'Core Configuration', keywords: ['api', 'openai', 'google'] },
    { label: 'Pipelines', path: '/pipelines', icon: Workflow, group: 'Core Configuration', keywords: ['flow'] },
    { label: 'Audio Profiles', path: '/profiles', icon: Sliders, group: 'Core Configuration', keywords: ['audio', 'voice'] },
    { label: 'Tools', path: '/tools', icon: Wrench, group: 'Core Configuration', keywords: ['function'] },
    { label: 'MCP', path: '/mcp', icon: Plug, group: 'Core Configuration', keywords: ['model context'] },
    { label: 'Voice Activity Detection', path: '/vad', icon: Activity, group: 'Advanced', keywords: ['vad', 'silence'] },
    { label: 'Streaming', path: '/streaming', icon: Zap, group: 'Advanced', keywords: ['stream', 'real-time'] },
    { label: 'LLM Defaults', path: '/llm', icon: Brain, group: 'Advanced', keywords: ['model', 'temperature'] },
    { label: 'Audio Transport', path: '/transport', icon: Radio, group: 'Advanced', keywords: ['rtp', 'codec'] },
    { label: 'Barge-in', path: '/barge-in', icon: AlertTriangle, group: 'Advanced', keywords: ['interrupt'] },
    { label: 'Environment', path: '/env', icon: Globe, group: 'System', keywords: ['env', 'variables', 'config'] },
    { label: 'Docker Services', path: '/docker', icon: Container, group: 'System', keywords: ['container', 'service'] },
    { label: 'Asterisk', path: '/asterisk', icon: Phone, group: 'System', keywords: ['pbx', 'sip'] },
    { label: 'Models', path: '/models', icon: HardDrive, group: 'System', keywords: ['download', 'local'] },
    { label: 'Updates', path: '/updates', icon: ArrowUpCircle, group: 'System', keywords: ['upgrade', 'version'] },
    { label: 'Logs', path: '/logs', icon: FileText, group: 'System', keywords: ['log', 'debug'] },
    { label: 'Terminal', path: '/terminal', icon: Terminal, group: 'System', keywords: ['shell', 'console'] },
    { label: 'Raw YAML', path: '/yaml', icon: Code, group: 'Danger Zone', keywords: ['config', 'edit'] },
    { label: 'Help', path: '/help', icon: HelpCircle, group: 'Support', keywords: ['docs', 'faq'] },
];

const CommandPalette: React.FC = () => {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [selectedIndex, setSelectedIndex] = useState(0);
    const inputRef = useRef<HTMLInputElement>(null);
    const listRef = useRef<HTMLDivElement>(null);
    const navigate = useNavigate();

    const isFuzzyMatch = (text: string, query: string): boolean => {
        let queryIdx = 0;
        const queryLower = query.toLowerCase();
        const textLower = text.toLowerCase();
        for (const char of textLower) {
            if (char === queryLower[queryIdx]) queryIdx++;
        }
        return queryIdx === queryLower.length;
    };

    const filtered = useMemo(() => {
        if (!query) return pages;
        return pages.filter(p =>
            isFuzzyMatch(p.label, query) ||
            isFuzzyMatch(p.group, query) ||
            isFuzzyMatch(p.path, query) ||
            (p.keywords || []).some(k => isFuzzyMatch(k, query))
        );
    }, [query]);

    const close = useCallback(() => {
        setOpen(false);
        setQuery('');
        setSelectedIndex(0);
    }, []);

    const selectItem = useCallback((index: number) => {
        const item = filtered[index];
        if (item) {
            navigate(item.path);
            close();
        }
    }, [filtered, navigate, close]);

    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                setOpen(prev => !prev);
            }
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, []);

    useEffect(() => {
        if (open) {
            inputRef.current?.focus();
        }
    }, [open]);

    useEffect(() => {
        setSelectedIndex(0);
    }, [query]);

    useEffect(() => {
        const el = listRef.current?.children[selectedIndex] as HTMLElement | undefined;
        el?.scrollIntoView({ block: 'nearest' });
    }, [selectedIndex]);

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setSelectedIndex(i => Math.min(i + 1, filtered.length - 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setSelectedIndex(i => Math.max(i - 1, 0));
        } else if (e.key === 'Enter') {
            e.preventDefault();
            selectItem(selectedIndex);
        } else if (e.key === 'Escape') {
            close();
        }
    };

    if (!open) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh]" onClick={close}>
            <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" />
            <div
                role="dialog"
                aria-modal="true"
                aria-labelledby="command-palette-title"
                className="relative w-full max-w-lg bg-card border border-border rounded-xl shadow-2xl overflow-hidden"
                onClick={e => e.stopPropagation()}
            >
                <h2 id="command-palette-title" className="sr-only">Command Palette</h2>
                <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
                    <Search className="w-5 h-5 text-muted-foreground shrink-0" />
                    <input
                        ref={inputRef}
                        type="text"
                        placeholder="Search pages..."
                        className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                        onKeyDown={handleKeyDown}
                    />
                    <kbd className="hidden sm:inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground bg-muted rounded border border-border">
                        ESC
                    </kbd>
                </div>

                <div ref={listRef} className="max-h-80 overflow-y-auto py-2">
                    {filtered.length === 0 ? (
                        <div className="px-4 py-8 text-center text-sm text-muted-foreground">
                            No pages found
                        </div>
                    ) : (
                        filtered.map((page, i) => {
                            const Icon = page.icon;
                            return (
                                <button
                                    key={page.path}
                                    className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm text-left transition-colors ${
                                        i === selectedIndex
                                            ? 'bg-accent text-accent-foreground'
                                            : 'text-foreground hover:bg-accent/50'
                                    }`}
                                    onClick={() => selectItem(i)}
                                    onMouseEnter={() => setSelectedIndex(i)}
                                >
                                    <Icon className="w-4 h-4 text-muted-foreground shrink-0" />
                                    <span className="flex-1">{page.label}</span>
                                    <span className="text-xs text-muted-foreground">{page.group}</span>
                                </button>
                            );
                        })
                    )}
                </div>

                <div className="flex items-center gap-4 px-4 py-2 border-t border-border text-[11px] text-muted-foreground">
                    <span><kbd className="px-1 py-0.5 rounded bg-muted border border-border">&uarr;&darr;</kbd> Navigate</span>
                    <span><kbd className="px-1 py-0.5 rounded bg-muted border border-border">Enter</kbd> Open</span>
                    <span><kbd className="px-1 py-0.5 rounded bg-muted border border-border">Esc</kbd> Close</span>
                </div>
            </div>
        </div>
    );
};

export default CommandPalette;
