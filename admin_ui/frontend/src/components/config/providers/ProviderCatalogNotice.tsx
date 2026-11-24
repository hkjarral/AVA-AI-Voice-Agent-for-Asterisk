import { providerModels } from '../../../config/providerCatalog';

/** Lifecycle guidance only; never rewrites an operator's explicit selection. */
export default function ProviderCatalogNotice({ provider, model }: { provider: string; model: unknown }) {
    const selected = String(model ?? '');
    const roles = ['realtime', 'llm', 'stt', 'tts', 'live'];
    const entry = roles.flatMap(role => providerModels(provider, role)).find(item => item.id === selected);
    if (entry?.status !== 'deprecated') return null;
    const date = (entry as typeof entry & { shutdown_date?: string }).shutdown_date;
    return <p className="text-xs text-amber-600" role="status">
        {selected} is deprecated{date ? ` and is scheduled to retire on ${date}` : ''}. Your configured value is preserved; select and test a replacement before rollout.
    </p>;
}
