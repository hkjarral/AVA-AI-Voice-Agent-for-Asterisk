import catalog from './kokoroVoices.generated.json';

export const KOKORO_VOICES = catalog.voices;
export const KOKORO_LANGUAGE_OPTIONS = Object.entries(catalog.language_codes).map(([value, locale]) => ({ value, label: `${locale} (${value})` }));

/** A stock voice chooses the matching phonemizer; preserve custom voice settings. */
export function kokoroLanguageForVoice(voice: string): string | undefined {
  return KOKORO_VOICES.find(option => option.id === voice)?.lang_code;
}

export function kokoroVoiceOptions(currentVoice: string) {
  const options = KOKORO_VOICES.map(voice => ({ value: voice.id, label: `${voice.id} (${voice.language})` }));
  if (currentVoice && !options.some(option => option.value === currentVoice)) {
    options.unshift({ value: currentVoice, label: `${currentVoice} (custom/current)` });
  }
  return options;
}
