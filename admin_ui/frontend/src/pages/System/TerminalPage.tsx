import React, { useState, useRef, useEffect } from 'react';
import { Terminal as TerminalIcon, Send } from 'lucide-react';
import axios from 'axios';

const TerminalPage = () => {
    const [history, setHistory] = useState<string[]>(['Welcome to Asterisk AI Admin Terminal', 'Type "help" for available commands.']);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const endRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        endRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [history]);

    const executeCommand = async (cmd: string) => {
        const parts = cmd.trim().split(' ');
        const command = parts[0].toLowerCase();
        const args = parts.slice(1);

        try {
            switch (command) {
                case 'help':
                    return [
                        'Available commands:',
                        '  status       - Check system health',
                        '  restart      - Restart services (usage: restart <service>)',
                        '  logs         - View recent logs (usage: logs <container> <lines>)',
                        '  version      - Show system version',
                        '  clear        - Clear terminal output'
                    ];

                case 'status':
                    const health = await axios.get('/api/system/health');
                    return [
                        'System Health Status:',
                        JSON.stringify(health.data, null, 2)
                    ];

                case 'restart':
                    if (args.length === 0) return ['Usage: restart <service> (e.g., ai_engine, all)'];
                    if (args[0] === 'all') {
                        await axios.post('/api/system/containers/restart-all');
                    } else {
                        await axios.post(`/api/system/containers/${args[0]}/restart`);
                    }
                    return [`Command sent: Restarting ${args[0]}...`];

                case 'logs':
                    const container = args[0] || 'ai_engine';
                    const lines = args[1] || '20';
                    const logRes = await axios.get(`/api/logs/${container}?tail=${lines}`);
                    return [
                        `--- Logs for ${container} (last ${lines} lines) ---`,
                        logRes.data.logs || 'No logs found.'
                    ];

                case 'version':
                    return ['Asterisk AI Agent v1.0.0 (Admin UI)'];

                case 'clear':
                    setHistory([]);
                    return [];

                default:
                    return [`Command not found: ${command}. Type "help" for list.`];
            }
        } catch (err: any) {
            return [`Error executing command: ${err.message || String(err)}`];
        }
    };

    const handleCommand = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!input.trim() || loading) return;

        const cmd = input.trim();
        setHistory(prev => [...prev, `$ ${cmd}`]);
        setInput('');
        setLoading(true);

        // Process locally
        if (cmd === 'clear') {
            setHistory([]);
            setLoading(false);
            return;
        }

        const output = await executeCommand(cmd);
        if (output && output.length > 0) {
            setHistory(prev => [...prev, ...output]);
        }
        setLoading(false);
    };

    return (
        <div className="h-[calc(100vh-140px)] flex flex-col space-y-4">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Web Terminal</h1>
                    <p className="text-muted-foreground mt-1">
                        Direct command-line access to the AI engine APIs.
                    </p>
                </div>
            </div>

            <div className="flex-1 bg-[#09090b] border border-border rounded-lg shadow-inner flex flex-col overflow-hidden font-mono text-sm">
                <div className="flex-1 p-4 overflow-auto space-y-1">
                    {history.map((line, i) => (
                        <div key={i} className={`${line.startsWith('$') ? 'text-blue-400 font-bold' : 'text-gray-300'} whitespace-pre-wrap`}>
                            {line}
                        </div>
                    ))}
                    {loading && <div className="text-yellow-500 animate-pulse">Processing...</div>}
                    <div ref={endRef} />
                </div>

                <form onSubmit={handleCommand} className="p-2 bg-secondary/10 border-t border-border flex items-center gap-2">
                    <TerminalIcon className="w-4 h-4 text-muted-foreground" />
                    <input
                        type="text"
                        className="flex-1 bg-transparent border-none outline-none text-gray-100 placeholder:text-gray-500"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        placeholder="Enter command (e.g., help, status, logs ai_engine)..."
                        autoFocus
                        disabled={loading}
                    />
                    <button type="submit" disabled={loading || !input} className="p-1 hover:text-primary transition-colors">
                        <Send className="w-4 h-4" />
                    </button>
                </form>
            </div>
        </div>
    );
};

export default TerminalPage;
