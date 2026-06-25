/** Read and JSON-parse the hook payload Claude Code writes to stdin. */
export function readStdin<T>(): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let buffer = "";
    process.stdin.setEncoding("utf-8");
    process.stdin.on("data", (chunk) => (buffer += chunk));
    process.stdin.on("end", () => {
      const trimmed = buffer.trim();
      if (!trimmed) {
        reject(new Error("empty hook stdin"));
        return;
      }
      try {
        resolve(JSON.parse(trimmed) as T);
      } catch (error) {
        reject(
          new Error(
            `failed to parse hook stdin: ${error instanceof Error ? error.message : String(error)}`,
          ),
        );
      }
    });
    process.stdin.once("error", reject);
  });
}

export function isPrimitive(value: unknown): value is string | number | boolean {
  const t = typeof value;
  return t === "string" || t === "number" || t === "boolean";
}

/** Stringify a value for display, leaving strings untouched. */
export function toText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (isPrimitive(value)) return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Parse a Claude Code ISO 8601 timestamp (with trailing `Z`) into ms epoch. */
export function parseTimestamp(value: unknown): number | undefined {
  if (typeof value !== "string" || !value) return undefined;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : undefined;
}

/**
 * Truncate large text to keep traces lightweight. Returns the (possibly
 * shortened) value plus metadata describing what was dropped, or `undefined`
 * metadata when nothing was truncated.
 */
export function truncate(
  value: string,
  maxChars: number,
): { text: string; meta?: { truncated: true; originalLength: number } } {
  if (value.length <= maxChars) return { text: value };
  return {
    text: value.slice(0, maxChars),
    meta: { truncated: true, originalLength: value.length },
  };
}

export type Clip = {
  (value: string): string;
  (value: unknown): unknown;
};

/** Build a clip() that truncates long strings to `maxChars`. */
export function makeClip(maxChars: number): Clip {
  function clip(value: string): string;
  function clip(value: unknown): unknown;
  function clip(value: unknown): unknown {
    if (typeof value !== "string") return value;
    const { text, meta } = truncate(value, maxChars);
    return meta ? `${text}\n…[truncated ${meta.originalLength - text.length} chars]` : text;
  }
  return clip;
}

let debugEnabled = false;
export function setDebug(enabled: boolean): void {
  debugEnabled = enabled;
}
export function debugLog(...args: unknown[]): void {
  if (!debugEnabled) return;
  // eslint-disable-next-line no-console
  console.error("[langfuse-claude-code]", ...args);
}
