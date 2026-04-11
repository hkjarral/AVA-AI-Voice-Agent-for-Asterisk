import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import {
    LayoutDashboard, Server, Workflow, MessageSquare, Wrench, Plug,
    Sliders, Activity, Zap, Brain, Radio, Globe, Container, FileText,
    Terminal, AlertTriangle, Code, HelpCircle, HardDrive, ArrowUpCircle,
    Phone, CalendarClock, Search, Command
} from 'lucide-react';

interface PageEntry {
    path: string;
    label: string;
    group: string;
    icon: React.ElementType;
    keywords?: string[];
}

const PAGES: PageEntry[] = [
    { path: '/', label: 'Dashboard', group: 'Overview', icon: LayoutDashboard, keywords: ['home', 'main'] },
    { path: '/history', label: 'Call History', group: 'Overview', icon: Phone, keywords: ['calls', 'log'] },
    { path: '/scheduling', label: 'Call Scheduling', group: 'Overview', icon: CalendarClock, keywords: ['schedule', 'cron'] },
    { path: '/wizard', label: 'Setup Wizard', group: 'Overview', icon: Zap, keywords: ['setup', 'configure'] },

    { path: '/providers', label: 'Providers', group: 'Core Configuration', icon: Server, keywords: ['stt', 'tts', 'llm', 'api'] },
    { path: '/pipelines', label: 'Pipelines', group: 'Core Configuration', icon: Workflow, keywords: ['flow', 'chain'] },
    { path: '/contexts', label: 'Contexts', group: 'Core Configuration', icon: MessageSquare, keywords: ['prompt', 'system'] },
    { path: '/profiles', label: 'Audio Profiles', group: 'Core Configuration', icon: Sliders, keywords: ['audio', 'sound'] },
    { path: '/tools', label: 'Tools', group: 'Core Configuration', icon: Wrench, keywords: ['function', 'action'] },
    { path: '/mcp', label: 'MCP', group: 'Core Configuration', icon: Plug, keywords: ['model', 'context', 'protocol'] },

    { path: '/vad', label: 'Voice Activity Detection', group: 'Advanced', icon: Activity, keywords: ['vad', 'voice', 'silence'] },
    { path: '/streaming', label: 'Streaming', group: 'Advanced', icon: Zap, keywords: ['stream', 'realtime'] },
    { path: '/llm', label: 'LLM Defaults', group: 'Advanced', icon: Brain, keywords: ['model', 'ai', 'temperature'] },
    { path: '/transport', label: 'Audio Transport', group: 'Advanced', icon: Radio, keywords: ['rtp', 'codec'] },
    { path: '/barge-in', label: 'Barge-in', group: 'Advanced', icon: AlertTriangle, keywords: ['interrupt', 'cutoff'] },

    { path: '/env', label: 'Environment', group: 'System', icon: Globe, keywords: ['env', 'variables', 'config'] },
    { path: '/docker', label: 'Docker Services', group: 'System', icon: Container, keywords: ['container', 'service'] },
    { path: '/asterisk', label: 'Asterisk', group: 'System', icon: Phone, keywords: ['pbx', 'sip', 'dialplan'] },
    { path: '/models', label: 'Models', group: 'System', icon: HardDrive, keywords: ['download', 'whisper'] },
    { path: '/updates', label: 'Updates', group: 'System', icon: ArrowUpCircle, keywords: ['upgrade', 'version'] },
    { path: '/logs', label: 'Logs', group: 'System', icon: FileText, keywords: ['log', 'debug', 'error'] },
    { path: '/terminal', label: 'Terminal', group: 'System', icon: Terminal, keywords: ['shell', 'bash', 'console'] },

    { path: '/yaml', label: 'Raw YAML', group: 'Danger Zone', icon: Code, keywords: ['config', 'edit', 'raw'] },
    { path: '/help', label: 'Help', group: 'Support', icon: HelpCircle, keywords: ['docs', 'faq', 'support'] },
];

function fuzzyMatch(text: string, query: string): boolean {
    const lower = text.toLowerCase();
    const q = query.toLowerCase();
    let qi = 0;
    for (let i = 0; i < lower.length && qi < q.length; i++) {
        if (lower[i] === q[qi]) qi++;
    }
    return qi === q.length;
}

const CommandPalette: React.FC = () => {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [selectedIndex, setSelectedIndex] = useState(0);
    const inputRef = useRef<HTMLInputElement>(null);
    const listRef = useRef<HTMLDivElement>(null);
    const navigate = useNavigate();
    const location = useLocation();

    const filtered = useMemo(() => {
        if (!query.trim()) return PAGES;
        return PAGES.filter(p =>
            fuzzyMatch(p.label, query) ||
            fuzzyMatch(p.group, query) ||
            p.keywords?.some(k => fuzzyMatch(k, query))
        );
    }, [query]);

    useEffect(() => {
        const handler = (e: KeyboardEvent) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                e.preventDefault();
                setOpen(prev => !prev);
            }
        };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, []);

    useEffect(() => {
        if (open) {
            setQuery('');
            setSelectedIndex(0);
            setTimeout(() => inputRef.current?.focus(), 0);
        }
    }, [open]);

    useEffect(() => {
        setSelectedIndex(0);
    }, [query]);

    useEffect(() => {
        if (!listRef.current) return;
        const selected = listRef.current.querySelector('[data-selected="true"]');
        selected?.scrollIntoView({ block: 'nearest' });
    }, [selectedIndex]);

    const handleSelect = (path: string) => {
        setOpen(false);
        if (location.pathname !== path) {
            navigate(path);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setSelectedIndex(i => Math.min(i + 1, filtered.length - 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setSelectedIndex(i => Math.max(i - 1, 0));
        } else if (e.key === 'Enter' && filtered[selectedIndex]) {
            e.preventDefault();
            handleSelect(filtered[selectedIndex].path);
        } else if (e.key === 'Escape') {
            e.preventDefault();
            setOpen(false);
        }
    };

    if (!open) return null;

    let lastGroup = '';

    return (
        <div
            className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]"
            onClick={() => setOpen(false)}
        >
            <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" />
            <div
                className="relative w-full max-w-lg bg-card border border-border rounded-xl shadow-2xl overflow-hidden"
                onClick={e => e.stopPropagation()}
            >
                <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
                    <Search className="w-4 h-4 text-muted-foreground shrink-0" />
                    <input
                        ref={inputRef}
                        type="text"
                        placeholder="Search pages..."
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                        onKeyDown={handleKeyDown}
                        className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none"
                    />
                    <kbd className="hidden sm:inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground bg-muted rounded border border-border">
                        ESC
                    </kbd>
                </div>

                <div ref={listRef} className="max-h-[50vh] overflow-y-auto p-2">
                    {filtered.length === 0 ? (
                        <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                            No pages found
                        </p>
                    ) : (
                        filtered.map((page, idx) => {
                            const showGroup = page.group !== lastGroup;
                            lastGroup = page.group;
                            const Icon = page.icon;
                            const isActive = location.pathname === page.path;

                            return (
                                <React.Fragment key={page.path}>
                                    {showGroup && (
                                        <div className="px-3 pt-3 pb-1 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                                            {page.group}
                                        </div>
                                    )}
                                    <button
                                        data-selected={idx === selectedIndex}
                                        onClick={() => handleSelect(page.path)}
                                        className={`w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                                            idx === selectedIndex
                                                ? 'bg-primary/10 text-primary'
                                                : 'text-foreground hover:bg-accent'
                                        }`}
                                    >
                                        <Icon className="w-4 h-4 shrink-0" />
                                        <span className="flex-1 text-left">{page.label}</span>
                                        {isActive && (
                                            <span className="text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                                                current
                                            </span>
                                        )}
                                    </button>
                                </React.Fragment>
                            );
                        })
                    )}
                </div>

                <div className="flex items-center justify-between px-4 py-2 border-t border-border text-[10px] text-muted-foreground">
                    <div className="flex items-center gap-3">
                        <span className="flex items-center gap-1"><kbd className="px-1 py-0.5 bg-muted rounded border border-border">↑↓</kbd> navigate</span>
                        <span className="flex items-center gap-1"><kbd className="px-1 py-0.5 bg-muted rounded border border-border">↵</kbd> open</span>
                        <span className="flex items-center gap-1"><kbd className="px-1 py-0.5 bg-muted rounded border border-border">esc</kbd> close</span>
                    </div>
                    <div className="flex items-center gap-1">
                        <Command className="w-3 h-3" />
                        <span>K</span>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default CommandPalette;
