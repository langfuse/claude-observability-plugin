import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { z } from "zod";

/**
 * Resolved tracer configuration.
 *
 * Resolution order (lowest → highest precedence):
 *   defaults  →  ~/.claude/langfuse.json  →  <cwd>/.claude/langfuse.json  →  env
 *
 * Claude Code exposes a plugin's `userConfig` values as
 * `CLAUDE_PLUGIN_OPTION_<NAME>` environment variables. For every setting we
 * read the `CLAUDE_PLUGIN_OPTION_<NAME>` form first, then the plain `<NAME>`
 * environment variable, so the plugin works whether configured through the
 * Claude Code install prompt or a shell export.
 */
export const ConfigSchema = z.object({
  // LANGFUSE_PUBLIC_KEY | CC_LANGFUSE_PUBLIC_KEY
  public_key: z.string().optional(),
  // LANGFUSE_SECRET_KEY | CC_LANGFUSE_SECRET_KEY
  secret_key: z.string().optional(),
  // LANGFUSE_BASE_URL | CC_LANGFUSE_BASE_URL
  base_url: z.string(),
  // LANGFUSE_TRACING_ENVIRONMENT | CC_LANGFUSE_ENVIRONMENT
  environment: z.string().optional(),
  // LANGFUSE_USER_ID | CC_LANGFUSE_USER_ID
  user_id: z.string().optional(),
  // CC_LANGFUSE_TAGS (JSON array or comma-separated list)
  tags: z.array(z.string()).optional(),
  // CC_LANGFUSE_METADATA (JSON object; values coerced to strings)
  metadata: z.record(z.string(), z.string()).optional(),
  // CC_LANGFUSE_MAX_CHARS — truncate large inputs/outputs
  max_chars: z.number().int().positive(),
  // CC_LANGFUSE_SKILL_TAGS — tag traces with skill:<name> for invoked skills
  skill_tags: z.boolean(),
  // CC_LANGFUSE_CAPTURE_SKILL_CONTENT — attach injected skill instructions to tool spans
  capture_skill_content: z.boolean(),
  // CC_LANGFUSE_DEBUG
  debug: z.boolean(),
  // CC_LANGFUSE_FAIL_ON_ERROR
  fail_on_error: z.boolean(),
});

export type Config = z.infer<typeof ConfigSchema>;

const PartialConfigSchema = ConfigSchema.partial();

const DEFAULTS: Pick<
  Config,
  "base_url" | "max_chars" | "skill_tags" | "capture_skill_content" | "debug" | "fail_on_error"
> = {
  base_url: "https://us.cloud.langfuse.com",
  max_chars: 20_000,
  skill_tags: true,
  capture_skill_content: false,
  debug: false,
  fail_on_error: false,
};

/**
 * Shape of the parts of `~/.claude.json` we read. Claude Code stores the
 * logged-in account (including the email) under `oauthAccount`.
 */
const ClaudeAccountSchema = z
  .object({
    oauthAccount: z.object({ emailAddress: z.string().optional() }).passthrough().optional(),
  })
  .passthrough();

function parseBoolean(value: unknown): boolean | undefined {
  if (typeof value === "boolean") return value;
  if (typeof value !== "string") return undefined;
  const normalized = value.trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  return undefined;
}

function parseTags(value: unknown): string[] | undefined {
  if (Array.isArray(value)) return value.map(String);
  if (typeof value !== "string" || value.trim().length === 0) return undefined;
  const trimmed = value.trim();
  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) return parsed.map(String);
    } catch {
      // fall through to comma-separated parsing
    }
  }
  return trimmed
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

function parseMetadata(value: unknown): Record<string, string> | undefined {
  let obj: unknown = value;
  if (typeof value === "string") {
    if (value.trim().length === 0) return undefined;
    try {
      obj = JSON.parse(value);
    } catch {
      return undefined;
    }
  }
  if (obj == null || typeof obj !== "object" || Array.isArray(obj)) {
    return undefined;
  }
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(obj as Record<string, unknown>)) {
    out[k] = typeof v === "string" ? v : JSON.stringify(v);
  }
  return out;
}

function parseInteger(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value !== "string") return undefined;
  const parsed = Number.parseInt(value.trim(), 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function stripUndefined<T extends Record<string, unknown>>(value: T): Partial<T> {
  return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined)) as Partial<T>;
}

async function readConfigFile(file: string): Promise<Partial<Config> | undefined> {
  try {
    const raw = JSON.parse(await fs.readFile(file, "utf-8")) as Record<string, unknown>;
    // Normalize the few fields that need coercion before zod validation.
    return PartialConfigSchema.parse(
      stripUndefined({
        ...raw,
        tags: raw.tags != null ? parseTags(raw.tags) : undefined,
        metadata: raw.metadata != null ? parseMetadata(raw.metadata) : undefined,
        max_chars: raw.max_chars != null ? parseInteger(raw.max_chars) : undefined,
        skill_tags: raw.skill_tags != null ? parseBoolean(raw.skill_tags) : undefined,
        capture_skill_content:
          raw.capture_skill_content != null ? parseBoolean(raw.capture_skill_content) : undefined,
        debug: raw.debug != null ? parseBoolean(raw.debug) : undefined,
        fail_on_error: raw.fail_on_error != null ? parseBoolean(raw.fail_on_error) : undefined,
      }),
    );
  } catch {
    return undefined;
  }
}

/**
 * Read an option, preferring the Claude Code plugin-config form
 * (`CLAUDE_PLUGIN_OPTION_<NAME>`) over the plain environment variable.
 */
function opt(name: string, env: Record<string, string | undefined>): string | undefined {
  return env[`CLAUDE_PLUGIN_OPTION_${name}`] ?? env[name];
}

/** Resolve `LANGFUSE_<SUFFIX>` with a `CC_LANGFUSE_<SUFFIX>` fallback. */
function getVar(suffix: string, env: Record<string, string | undefined>): string | undefined {
  return opt(`LANGFUSE_${suffix}`, env) ?? opt(`CC_LANGFUSE_${suffix}`, env);
}

function readEnvConfig(env: Record<string, string | undefined>): Partial<Config> {
  return PartialConfigSchema.parse(
    stripUndefined({
      public_key: getVar("PUBLIC_KEY", env),
      secret_key: getVar("SECRET_KEY", env),
      base_url: getVar("BASE_URL", env),
      environment: opt("LANGFUSE_TRACING_ENVIRONMENT", env) ?? opt("CC_LANGFUSE_ENVIRONMENT", env),
      user_id: getVar("USER_ID", env),
      tags: parseTags(opt("CC_LANGFUSE_TAGS", env)),
      metadata: parseMetadata(opt("CC_LANGFUSE_METADATA", env)),
      max_chars: parseInteger(opt("CC_LANGFUSE_MAX_CHARS", env)),
      skill_tags: parseBoolean(opt("CC_LANGFUSE_SKILL_TAGS", env)),
      capture_skill_content: parseBoolean(opt("CC_LANGFUSE_CAPTURE_SKILL_CONTENT", env)),
      debug: parseBoolean(opt("CC_LANGFUSE_DEBUG", env)),
      fail_on_error: parseBoolean(opt("CC_LANGFUSE_FAIL_ON_ERROR", env)),
    }),
  );
}

/**
 * Read the logged-in Claude Code user's email from `~/.claude.json`, used as a
 * `user_id` fallback when one isn't explicitly configured. Returns undefined if
 * the file is missing/unreadable or has no account email.
 */
async function readClaudeUserEmail(claudeJsonFile: string): Promise<string | undefined> {
  try {
    const raw = JSON.parse(await fs.readFile(claudeJsonFile, "utf-8")) as unknown;
    const email = ClaudeAccountSchema.parse(raw).oauthAccount?.emailAddress?.trim();
    return email && email.length > 0 ? email : undefined;
  } catch {
    return undefined;
  }
}

const getHomeDir = () => process.env.HOME ?? os.homedir();

export async function getConfig(options?: {
  home?: string;
  cwd?: string;
  env?: Record<string, string | undefined>;
}): Promise<Config> {
  const home = options?.home ?? getHomeDir();
  const cwd = options?.cwd ?? process.cwd();
  const env = options?.env ?? process.env;

  const [globalConfig, localConfig] = await Promise.all([
    readConfigFile(path.join(home, ".claude", "langfuse.json")),
    readConfigFile(path.join(cwd, ".claude", "langfuse.json")),
  ]);
  const envConfig = readEnvConfig(env);

  // Fall back to the logged-in Claude Code account email as user_id, but only
  // when no explicit user_id was provided via config files or environment.
  const explicitUserId = globalConfig?.user_id ?? localConfig?.user_id ?? envConfig.user_id;
  const claudeUserId = explicitUserId
    ? undefined
    : await readClaudeUserEmail(path.join(home, ".claude.json"));

  return ConfigSchema.parse({
    ...DEFAULTS,
    ...(claudeUserId ? { user_id: claudeUserId } : {}),
    ...globalConfig,
    ...localConfig,
    ...envConfig,
  });
}
