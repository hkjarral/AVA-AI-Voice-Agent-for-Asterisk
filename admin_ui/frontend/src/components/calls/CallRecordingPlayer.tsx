import { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Download, Loader2, Pause, Play, RefreshCw } from 'lucide-react';

type RecordingInfo = {
    has_recording: boolean;
    filename: string | null;
    file_size_bytes: number;
    duration_hint: string | null;
};

const time = (seconds: number) => Number.isFinite(seconds)
    ? `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, '0')}` : '0:00';

async function errorMessage(error: unknown): Promise<string> {
    const response = (error as { response?: { data?: unknown } })?.response;
    let data = response?.data;
    if (data instanceof Blob) {
        try { data = JSON.parse(await data.text()); } catch { data = null; }
    }
    return typeof (data as { detail?: unknown })?.detail === 'string'
        ? (data as { detail: string }).detail : 'Recording request failed. Please retry.';
}

/** Mount per call ID. Every async continuation is scoped to its request lifetime. */
export function CallRecordingPlayer({ recordId }: { recordId: string }) {
    const [info, setInfo] = useState<RecordingInfo | null>(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState<'play' | 'download' | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [playing, setPlaying] = useState(false);
    const [position, setPosition] = useState(0);
    const [duration, setDuration] = useState(0);
    const [attempt, setAttempt] = useState(0);
    const lifetime = useRef<AbortController | null>(null);
    const audio = useRef<HTMLAudioElement | null>(null);
    const urls = useRef(new Set<string>());
    const pending = useRef(false);

    const cleanupAudio = useCallback(() => {
        if (audio.current) {
            audio.current.pause();
            audio.current.onended = null;
            audio.current.ontimeupdate = null;
            audio.current.onloadedmetadata = null;
            audio.current.onerror = null;
            audio.current.src = '';
            audio.current = null;
        }
        urls.current.forEach(url => URL.revokeObjectURL(url));
        urls.current.clear();
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        lifetime.current = controller;
        pending.current = false;
        setInfo(null);
        setError(null);
        setLoading(true);
        setBusy(null);
        setPlaying(false);
        setPosition(0);
        setDuration(0);
        axios.get(`/api/calls/${encodeURIComponent(recordId)}/recording`, { signal: controller.signal })
            .then(res => { if (!controller.signal.aborted) setInfo(res.data); })
            .catch(async err => {
                const message = await errorMessage(err);
                if (!controller.signal.aborted) setError(message);
            })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => {
            controller.abort();
            cleanupAudio();
        };
    }, [recordId, attempt, cleanupAudio]);

    const access = async (download: boolean) => {
        const controller = lifetime.current;
        if (!controller || controller.signal.aborted || pending.current) return;
        pending.current = true;
        setBusy(download ? 'download' : 'play');
        setError(null);
        try {
            if (!download && audio.current) {
                if (playing) { audio.current.pause(); setPlaying(false); }
                else { await audio.current.play(); if (!controller.signal.aborted) setPlaying(true); }
                return;
            }
            const res = await axios.get(`/api/calls/${encodeURIComponent(recordId)}/recording/${download ? 'download' : 'audio'}`, {
                responseType: 'blob', signal: controller.signal,
            });
            if (controller.signal.aborted) return;
            const url = URL.createObjectURL(res.data);
            urls.current.add(url);
            if (download) {
                const link = document.createElement('a');
                link.href = url;
                // Server and UI both expose the browser-playable WAV representation.
                link.download = (info?.filename || 'recording.wav').replace(/\.[^.]+$/, '.wav');
                document.body.appendChild(link);
                link.click();
                link.remove();
                // Allow the browser to consume the URL; unmount also revokes it.
                window.setTimeout(() => {
                    if (urls.current.delete(url)) URL.revokeObjectURL(url);
                }, 1000);
            } else {
                const player = new Audio(url);
                audio.current = player;
                player.onended = () => { setPlaying(false); setPosition(0); };
                player.ontimeupdate = () => setPosition(player.currentTime);
                player.onloadedmetadata = () => setDuration(player.duration);
                player.onerror = () => {
                    cleanupAudio(); setPlaying(false); setError('Recording could not be played. Please retry.');
                };
                await player.play();
                if (!controller.signal.aborted) setPlaying(true);
            }
        } catch (err) {
            const message = await errorMessage(err);
            if (!controller.signal.aborted) {
                if (!download) { cleanupAudio(); setPlaying(false); }
                setError(message);
            }
        } finally {
            if (!controller.signal.aborted) { pending.current = false; setBusy(null); }
        }
    };

    return <div className="px-4 py-3 border-b bg-muted/30 space-y-2" aria-label="Call recording">
        {loading ? <div role="status" className="flex items-center gap-2 text-sm"><Loader2 className="w-4 h-4 animate-spin" />Checking for recording…</div>
            : <>
                {info?.has_recording && info.duration_hint !== 'empty' ? <div className="flex flex-wrap items-center gap-3">
                    <button type="button" disabled={busy !== null} onClick={() => void access(false)}
                        aria-label={playing ? 'Pause' : 'Play recording'} title={playing ? 'Pause' : 'Play recording'}
                        className="p-2 rounded-full bg-primary text-primary-foreground disabled:opacity-50">
                        {busy === 'play' ? <Loader2 className="w-4 h-4 animate-spin" /> : playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
                    </button>
                    <div className="flex-1 min-w-32">
                        <div className="text-xs truncate">{info.filename} ({Math.ceil(info.file_size_bytes / 1024)} KB)</div>
                        <input aria-label="Recording position" type="range" min={0} max={Number.isFinite(duration) ? duration : 0}
                            step={0.1} value={position} disabled={!duration || !Number.isFinite(duration)} className="w-full"
                            onChange={event => { if (audio.current) { audio.current.currentTime = Number(event.target.value); setPosition(Number(event.target.value)); } }} />
                        <span className="text-xs text-muted-foreground">{time(position)} / {time(duration)}</span>
                    </div>
                    <button type="button" disabled={busy !== null} onClick={() => void access(true)} className="flex items-center gap-2 text-sm disabled:opacity-50">
                        {busy === 'download' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}Download recording
                    </button>
                </div> : info ? <p className="text-sm text-muted-foreground">{info.duration_hint === 'empty' ? 'Recording exists but contains no audio' : 'No recording available'}</p> : null}
                {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
                <button type="button" disabled={busy !== null} onClick={() => setAttempt(n => n + 1)} className="flex items-center gap-1 text-xs disabled:opacity-50">
                    <RefreshCw className="w-3 h-3" />Refresh recording
                </button>
            </>}
    </div>;
}
