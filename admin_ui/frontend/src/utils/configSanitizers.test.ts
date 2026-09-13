import { describe, expect, it } from 'vitest';
import { sanitizeConfigForSave } from './configSanitizers';

describe('sanitizeConfigForSave', () => {
  it.each([
    ['NaN', Number.NaN],
    ['positive infinity', Number.POSITIVE_INFINITY],
    ['negative infinity', Number.NEGATIVE_INFINITY],
  ])('rejects %s with the exact config path', (_label, value) => {
    const config = {
      providers: {
        google_live: {
          input_gain_max_db: value,
        },
      },
    };

    expect(() => sanitizeConfigForSave(config)).toThrow(
      'providers.google_live.input_gain_max_db',
    );
  });

  it('reports non-finite values nested inside lists', () => {
    expect(() => sanitizeConfigForSave({ values: [1, Number.NaN] })).toThrow('values[1]');
  });

  it('preserves finite numeric values', () => {
    const config = { providers: { google_live: { input_gain_max_db: 6.5 } } };
    expect(sanitizeConfigForSave(config)).toEqual(config);
  });
});
