import { describe, expect, it } from 'vitest';
import { KOKORO_VOICES, KOKORO_LANGUAGE_OPTIONS, kokoroLanguageForVoice, kokoroVoiceOptions } from './kokoroVoices';

describe('Kokoro voice/language selection', () => {
  it('provides every verified voice and all nine language codes', () => {
    expect(KOKORO_VOICES).toHaveLength(54);
    expect(KOKORO_LANGUAGE_OPTIONS).toHaveLength(9);
    for (const voice of KOKORO_VOICES) expect(kokoroLanguageForVoice(voice.id)).toBe(voice.lang_code);
  });
  it('selects the phonemizer matching the chosen language', () => {
    expect(kokoroLanguageForVoice('jf_alpha')).toBe('j');
    expect(kokoroLanguageForVoice('pf_dora')).toBe('p');
    expect(kokoroLanguageForVoice('af_heart')).toBe('a');
  });
  it('preserves custom selections without guessing a language', () => {
    expect(kokoroLanguageForVoice('custom-id')).toBeUndefined();
    expect(kokoroVoiceOptions('custom-id')[0]).toEqual({ value: 'custom-id', label: 'custom-id (custom/current)' });
  });
});
