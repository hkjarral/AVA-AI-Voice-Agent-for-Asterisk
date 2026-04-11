import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { RefreshCw, Download, Pause, Play, Search } from 'lucide-react';

const Logs = () => {
    const [logs, setLogs] = useState('');
    const [loading, setLoading] = useState(false);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const [container, setContainer] = useState('ai_engine');
    const [filter, setFilter] = useState('');
    const logsEndRef = useRef<HTMLDivElement>(null);

    const fetchLogs = async () => {
        setLoading(true);
        try {
            const res = await axios.get(`/api/logs/${container}?tail=500`);
            setLogs(res.data.logs);
        } catch (err) {
            console.error("Failed to fetch logs", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchLogs();
        const interval = setInterval(() => {
            if (autoRefresh) {
                fetchLogs();
            }
        }, 3000);
        return () => clearInterval(interval);
    }, [autoRefresh, container]);

    useEffect(() => {
        if (autoRefresh) {
            logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
        }
    }, [logs, autoRefresh]);

    const getFilteredLines = (): string[] => {
        if (!logs) return [];
        return logs.split('\n').filter(line =>
            !filter || line.toLowerCase().includes(filter.toLowerCase())
        );
    };

    const handleDownload = () => {
        const lines = getFilteredLines();
        if (lines.length === 0) {
            return;
        }
        const blob = new Blob([lines.join('\n')], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        const filename = `ava-${container}-${ts}.log`;
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const getColoredLogs = () => {
        if (!logs) return <div className="text-muted-foreground italic">No logs available...</div>;

        return logs.split('\n').map((line, i) => {
            if (filter && !line.toLowerCase().includes(filter.toLowerCase())) return null;

            let className = 'text-green-400'; // Default
            if (line.includes('ERROR') || line.includes('Exception') || line.includes('CRITICAL')) {
                className = 'text-red-500 font-bold';
            } else if (line.includes('WARN') || line.includes('WARNING')) {
                className = 'text-yellow-500';
            } else if (line.includes('INFO')) {
                className = 'text-blue-400';
            } else if (line.includes('DEBUG')) {
                className = 'text-gray-500';
            }

            return <div key={i} className={`${className} hover:bg-white/5 px-1 rounded`}>{line}</div>;
        });
    };

    return (
        <div className="h-full flex flex-col space-y-4">
            <div className="flex justify-between items-center">
                <div className="flex items-center space-x-4">
                    <h1 className="text-2xl font-bold">System Logs</h1>
                    <div className="relative">
                        <Search className="absolute left-2 top-1/2 transform -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                        <input
                            type="text"
                            placeholder="Filter logs..."
                            className="pl-8 pr-4 py-1 text-sm rounded-md border border-input bg-background w-64"
                            value={filter}
                            onChange={e => setFilter(e.target.value)}
                        />
                    </div>
                </div>
                <div className="flex space-x-2 items-center">
                    <select
                        className="p-2 rounded border border-input bg-background text-sm"
                        value={container}
                        onChange={e => setContainer(e.target.value)}
                    >
                        <option value="ai_engine">AI Engine</option>
                        <option value="local_ai_server">Local AI Server</option>
                        <option value="admin_ui">Admin UI</option>
                    </select>

                    <button
                        onClick={() => setAutoRefresh(!autoRefresh)}
                        className={`p-2 rounded border ${autoRefresh ? 'bg-primary text-primary-foreground border-primary' : 'border-input hover:bg-accent'}`}
                        title={autoRefresh ? "Pause Auto-refresh" : "Resume Auto-refresh"}
                    >
                        {autoRefresh ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
                    </button>

                    <button
                        onClick={handleDownload}
                        className="p-2 rounded border border-input hover:bg-accent"
                        title="Download Logs"
                    >
                        <Download className="w-4 h-4" />
                    </button>

                    <button
                        onClick={fetchLogs}
                        className="p-2 rounded border border-input hover:bg-accent"
                        title="Refresh Now"
                    >
                        <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                    </button>
                </div>
            </div>

            <div className="flex-1 bg-black font-mono text-sm p-4 rounded-lg overflow-auto border border-border shadow-inner">
                {getColoredLogs()}
                <div ref={logsEndRef} />
            </div>
        </div>
    );
};

export default Logs;
