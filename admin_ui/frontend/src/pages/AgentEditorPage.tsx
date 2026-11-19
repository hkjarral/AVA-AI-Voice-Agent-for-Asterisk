import { useEffect, useRef, useState } from 'react';
import { Link, useBeforeUnload, useBlocker, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import axios from 'axios';
import AgentForm, { type Agent } from '../components/agents/AgentForm';
import { useConfirmDialog } from '../hooks/useConfirmDialog';

export default function AgentEditorPage() {
    const { slug } = useParams();
    const [search] = useSearchParams();
    const navigate = useNavigate();
    const { confirm } = useConfirmDialog();
    const [agent, setAgent] = useState<Agent | null>(null);
    const [loading, setLoading] = useState(!!slug);
    const [error, setError] = useState('');
    const [dirty, setDirty] = useState(false);
    const [saving, setSaving] = useState(false);
    const saved = useRef(false);
    const asking = useRef(false);
    const blocker = useBlocker(() => !saved.current && (dirty || saving));
    useBeforeUnload(event => {
        if (!saved.current && (dirty || saving)) { event.preventDefault(); event.returnValue = ''; }
    });
    useEffect(() => {
        if (blocker.state !== 'blocked' || asking.current) return;
        if (saving) { blocker.reset(); return; }
        asking.current = true;
        void confirm({ title: 'Discard unsaved changes?', description: 'Your agent settings and image have not been saved.',
            confirmText: 'Discard changes', cancelText: 'Keep editing', variant: 'destructive' })
            .then(discard => { asking.current = false; if (discard) blocker.proceed(); else blocker.reset(); });
    }, [blocker, confirm, saving]);
    useEffect(() => {
        const controller = new AbortController();
        setError(''); setLoading(!!slug); setAgent(null); setDirty(false); saved.current = false;
        if (slug) axios.get<Agent>(`/api/agents/${encodeURIComponent(slug)}`, { signal: controller.signal })
            .then(result => setAgent(result.data))
            .catch(error => { if (!controller.signal.aborted) setError(error.response?.status === 404 ? 'This agent no longer exists.' : 'Could not load this agent. Please try again.'); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [slug]);
    if (loading) return <p role="status" className="p-8 text-muted-foreground">Loading agent settings…</p>;
    if (error) return <div role="alert" className="space-y-4 p-6"><p>{error}</p><Link to="/agents" className="text-primary underline">Back to agents</Link></div>;
    const requested = search.get('section');
    const section = requested && ['general', 'conversation', 'tools', 'advanced'].includes(requested) ? requested : 'general';
    return <AgentForm key={slug || 'new'} isOpen agent={agent} initialSection={section}
        onDirtyChange={setDirty} onSavingChange={setSaving} onClose={() => navigate('/agents')}
        onSaved={() => { saved.current = true; setDirty(false); navigate('/agents'); }} />;
}
