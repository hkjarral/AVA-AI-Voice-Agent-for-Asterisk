// Keep capability exceptions aligned with GoogleLLMAdapter._ignores_sampling_params.
export function googleLLMManagesTemperature(model: string): boolean {
  const name = model.replace(/^models\//, '');
  // 3.5 Flash routes to 3.6; 3.5 Flash-Lite also ignores sampling.
  const automaticModels = new Set(['gemini-3.5-flash', 'gemini-3.5-flash-lite']);
  if (automaticModels.has(name)) return true;
  const version = name.match(/^gemini-(\d+)(?:\.(\d+))?(?:-|$)/);
  return version !== null && (
    Number(version[1]) > 3 ||
    (Number(version[1]) === 3 && Number(version[2] ?? 0) >= 6)
  );
}
