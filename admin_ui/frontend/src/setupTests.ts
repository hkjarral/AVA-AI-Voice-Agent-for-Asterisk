// setupTests.ts — runs in every vitest environment after the environment is set up.
// Node 25+ exposes a stub global.localStorage that lacks .clear() / .setItem() etc.
// When the test environment is jsdom, override the stubs with jsdom's full
// Storage implementations so that localStorage.clear() works as expected.
// jsdom requires a URL to enable localStorage; vitest's default jsdom config
// uses 'http://localhost' but Node 25's storage stub may take precedence.
// We build a fresh jsdom Storage and inject it as the globalThis property.
function makeStorage() {
  const store = new Map<string, string>();
  return {
    get length() { return store.size; },
    key(n: number) { return [...store.keys()][n] ?? null; },
    getItem(k: string) { return store.has(k) ? store.get(k)! : null; },
    setItem(k: string, v: string) { store.set(k, String(v)); },
    removeItem(k: string) { store.delete(k); },
    clear() { store.clear(); },
  };
}

if (typeof window !== 'undefined') {
  // In jsdom environment vitest sets window = the jsdom window, but Node 25's
  // built-in localStorage getter shadowing means window.localStorage is the stub.
  // Directly override with a fresh Map-backed in-memory store.
  const _ls = makeStorage();
  const _ss = makeStorage();
  Object.defineProperty(globalThis, 'localStorage', {
    value: _ls, configurable: true, writable: true,
  });
  Object.defineProperty(globalThis, 'sessionStorage', {
    value: _ss, configurable: true, writable: true,
  });
}
