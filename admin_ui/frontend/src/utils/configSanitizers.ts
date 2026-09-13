const findNonFiniteNumberPaths = (value: unknown, path = ''): string[] => {
  if (typeof value === 'number' && !Number.isFinite(value)) {
    return [path || '<root>'];
  }

  if (Array.isArray(value)) {
    return value.flatMap((child, index) => findNonFiniteNumberPaths(child, `${path}[${index}]`));
  }

  if (value && typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
      findNonFiniteNumberPaths(child, path ? `${path}.${key}` : key),
    );
  }

  return [];
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
