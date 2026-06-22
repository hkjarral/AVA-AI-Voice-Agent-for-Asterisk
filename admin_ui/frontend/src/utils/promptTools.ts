/**
 * Detects references to configured tools inside a prompt and resolves their
 * in-call configuration status, so the prompt editor can colour-code whether a
 * mentioned tool is actually wired up.
 *
 * Detection rule (deliberately precise to avoid false positives): a known tool
 * name is only treated as a reference when it sits directly next to a
 * tool/function keyword — e.g. "the book_appointment tool" or "the tool
 * book_appointment" — so common-word tool names ("transfer the call") don't
 * light up.
 */

export type ToolStatus = 'context' | 'global' | 'unavailable';

export interface ToolReference {
    name: string;
    start: number;
    end: number;
}

export interface ToolLike {
    name: string;
    phase?: string;
    is_global?: boolean;
}

/** Per-entity (agent/context) in-call enablement facts. */
export interface InCallEnablement {
    /** Tool is explicitly added to this entity's in-call tools. */
    explicitlyAdded: (name: string) => boolean;
    /** A global tool that this entity has turned off for in-call. */
    globalDisabledHere: (name: string) => boolean;
    /** Tool is disabled globally in Tools settings. */
    globallyDisabled: (name: string) => boolean;
}

// Legacy tool-name aliases → their canonical catalog name, so an entity that
// stored the old name still resolves against the catalog's canonical entry.
const TOOL_ALIASES: Record<string, string> = { transfer: 'blind_transfer' };

/** Map a stored tool name to its canonical catalog name (identity if none). */
export function canonicalToolName(name: string): string {
    return TOOL_ALIASES[name] || name;
}

const KEYWORD = '(?:tool|tools|function|functions)';

function escapeRegExp(s: string): string {
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function parseToolReferences(text: string, knownNames: string[]): ToolReference[] {
    if (!text) return [];
    const refs: ToolReference[] = [];
    for (const name of knownNames) {
        if (!name) continue;
        const esc = escapeRegExp(name);
        // name immediately followed by the keyword (keyword not consumed); an
        // optional closing quote/backtick may sit between them ("`name` tool").
        const after = new RegExp(`\\b${esc}\\b(?=[\`'"]?\\s+${KEYWORD}\\b)`, 'gi');
        let m: RegExpExecArray | null;
        while ((m = after.exec(text)) !== null) {
            refs.push({ name, start: m.index, end: m.index + name.length });
        }
        // keyword immediately followed by the name (an optional opening
        // quote/backtick may sit between them: "tool `name`").
        const before = new RegExp(`\\b${KEYWORD}\\b\\s+[\`'"]?(${esc})\\b`, 'gi');
        while ((m = before.exec(text)) !== null) {
            const start = m.index + (m[0].length - m[1].length);
            refs.push({ name, start, end: start + m[1].length });
        }
    }
    refs.sort((a, b) => a.start - b.start);
    // drop overlaps (e.g. "tool X tool" matching both directions on the same span)
    const out: ToolReference[] = [];
    let lastEnd = -1;
    for (const r of refs) {
        if (r.start >= lastEnd) {
            out.push(r);
            lastEnd = r.end;
        }
    }
    return out;
}

/** In-call status for a single tool, given the entity's enablement facts. */
export function inCallToolStatus(tool: ToolLike, e: InCallEnablement): ToolStatus {
    const phase = tool.phase || 'in_call'; // catalog default is in-call
    if (phase !== 'in_call') return 'unavailable';
    if (e.globallyDisabled(tool.name)) return 'unavailable';
    if (tool.is_global) return e.globalDisabledHere(tool.name) ? 'unavailable' : 'global';
    return e.explicitlyAdded(tool.name) ? 'context' : 'unavailable';
}

export function buildInCallStatusMap(catalog: ToolLike[], e: InCallEnablement): Record<string, ToolStatus> {
    const map: Record<string, ToolStatus> = {};
    for (const tool of catalog) {
        if (tool && tool.name) map[tool.name] = inCallToolStatus(tool, e);
    }
    return map;
}
