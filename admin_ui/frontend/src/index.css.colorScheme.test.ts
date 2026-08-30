// @ts-expect-error - no @types/node in this project; vitest runs this file in node
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/**
 * Dark-mode dropdown contrast regression (WCAG 1.4.3).
 *
 * Tailwind Preflight sets `color: inherit` on `select`, overriding the
 * browser default `select { color: FieldText }`. The select resolves to
 * `--foreground`, and options inherit that, landing near-white in dark mode.
 * `background-color` does not inherit into `option`, and a select's own
 * background does not paint the popup listbox, so the popup falls back to
 * the user-agent canvas. With no `color-scheme` declared, that canvas is
 * white: near-white text on white, measured at 1.04:1.
 *
 * jsdom resolves neither system colours nor `color-scheme`, so this guards
 * the declarations rather than the rendering. Rendered contrast after the
 * fix was measured in Chromium and Firefox: 19.06:1 dark, 19.90:1 light.
 */

const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8');

/** Returns the declaration body of the first rule whose selector matches `head`. */
const declarations = (head: RegExp): string => {
    const match = css.match(new RegExp(`${head.source}\\s*\\{([^}]*)\\}`));
    if (!match) throw new Error(`index.css has no rule matching ${head}`);
    return match[1];
};

describe('index.css — native control colour scheme', () => {
    it('opts the light theme into the light colour scheme', () => {
        expect(declarations(/:root/)).toMatch(/color-scheme:\s*light/);
    });

    it('opts the dark theme into the dark colour scheme', () => {
        expect(declarations(/\.dark/)).toMatch(/color-scheme:\s*dark/);
    });

    it('paints option and optgroup from the popover tokens', () => {
        const body = declarations(/option,\s*optgroup/);
        expect(body).toMatch(/background-color:\s*hsl\(var\(--popover\)\)/);
        expect(body).toMatch(/color:\s*hsl\(var\(--popover-foreground\)\)/);
    });

    it('keeps disabled options visually distinct', () => {
        expect(declarations(/option:disabled/)).toMatch(
            /color:\s*hsl\(var\(--muted-foreground\)\)/
        );
    });
});
