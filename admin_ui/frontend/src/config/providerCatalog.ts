import catalogData from './providerCatalog.generated.json';

export interface CatalogModel { id: string; label: string; status?: string; api?: string }
export interface CatalogVoice { id: string; label: string; language?: string }
interface CatalogProvider {
  defaults: Record<string, string | number>;
  models?: Record<string, CatalogModel[]>;
  voices?: Record<string, CatalogVoice[]>;
  voice_models?: Record<string, string[]>;
  vertex_regions?: Record<string, string[]>;
}
export const PROVIDER_CATALOG = catalogData as unknown as { verified_at: string; providers: Record<string, CatalogProvider> };
export const providerDefault = (provider: string, field: string): string => String(PROVIDER_CATALOG.providers[provider].defaults[field]);
export const providerModels = (provider: string, role: string): CatalogModel[] => PROVIDER_CATALOG.providers[provider]?.models?.[role] ?? [];
export const modelIds = (provider: string, role: string): string[] => providerModels(provider, role).map(model => model.id);
export const modelOptions = (provider: string, role: string) => providerModels(provider, role).map(model => ({ value: model.id, label: model.label }));
export const providerVoices = (provider: string, role: string): CatalogVoice[] => PROVIDER_CATALOG.providers[provider]?.voices?.[role] ?? [];
export const voiceIdsForModel = (provider: string, model: string): string[] => PROVIDER_CATALOG.providers[provider]?.voice_models?.[model] ?? [];
