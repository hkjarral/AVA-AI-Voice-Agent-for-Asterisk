const findNonFiniteNumberPaths = (
  value: unknown,
  path = '',
  visiting = new WeakSet<object>(),
): string[] => {
  if (typeof value === 'number' && !Number.isFinite(value)) {
    return [path || '<root>'];
  }

  if (!value || typeof value !== 'object') {
    return [];
  }

  if (visiting.has(value)) {
    throw new Error(`Configuration contains a recursive reference at ${path || '<root>'}`);
  }

  visiting.add(value);
  try {
    if (Array.isArray(value)) {
      return value.flatMap((child, index) =>
        findNonFiniteNumberPaths(child, `${path}[${index}]`, visiting),
      );
    }

    return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => {
      const identifierSafe = /^[A-Za-z_$][\w$]*$/.test(key);
      const childPath = identifierSafe
        ? path ? `${path}.${key}` : key
        : `${path}[${JSON.stringify(key)}]`;
      return findNonFiniteNumberPaths(child, childPath, visiting);
    });
  } finally {
    visiting.delete(value);
  }
};

export function sanitizeConfigForSave(config: any): any {
  if (!config || typeof config !== "object") return config;

  const out: any = { ...config };

  // Pipelines: tools are configured per-context only; pipelines.*.tools is deprecated.
  if (out.pipelines && typeof out.pipelines === "object") {
    const nextPipelines: any = Array.isArray(out.pipelines) ? {} : { ...out.pipelines };
    for (const [name, pipeline] of Object.entries(out.pipelines)) {
      if (pipeline && typeof pipeline === "object" && !Array.isArray(pipeline)) {
        const { tools: _legacyTools, ...rest } = pipeline as any;
        nextPipelines[name] = rest;
      } else {
        nextPipelines[name] = pipeline;
      }
    }
    out.pipelines = nextPipelines;
  }

  const nonFinitePaths = findNonFiniteNumberPaths(out);
  if (nonFinitePaths.length) {
    throw new Error(
      `Configuration contains invalid numeric values: ${nonFinitePaths.slice(0, 10).join(', ')}`,
    );
  }

  return out;
}
