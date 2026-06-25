import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "bun:test";

import { getConfig } from "../src/config.js";

const NONEXISTENT = "/nonexistent-dir-for-tests";

function baseOpts(env: Record<string, string | undefined>) {
  // Point home/cwd at a path with no langfuse.json so only env is exercised.
  return { home: NONEXISTENT, cwd: NONEXISTENT, env };
}

describe("getConfig", () => {
  it("applies defaults when nothing is set", async () => {
    const config = await getConfig(baseOpts({}));
    expect(config.base_url).toBe("https://us.cloud.langfuse.com");
    expect(config.max_chars).toBe(20_000);
    expect(config.debug).toBe(false);
    expect(config.public_key).toBeUndefined();
  });

  it("reads plain LANGFUSE_* environment variables", async () => {
    const config = await getConfig(
      baseOpts({ LANGFUSE_PUBLIC_KEY: "pk", LANGFUSE_SECRET_KEY: "sk" }),
    );
    expect(config.public_key).toBe("pk");
    expect(config.secret_key).toBe("sk");
  });

  it("prefers the CLAUDE_PLUGIN_OPTION_* form over the plain variable", async () => {
    const config = await getConfig(
      baseOpts({
        LANGFUSE_PUBLIC_KEY: "plain",
        CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY: "fromPlugin",
      }),
    );
    expect(config.public_key).toBe("fromPlugin");
  });

  it("falls back to CC_LANGFUSE_* aliases", async () => {
    const config = await getConfig(baseOpts({ CC_LANGFUSE_SECRET_KEY: "sk" }));
    expect(config.secret_key).toBe("sk");
  });

  it("coerces booleans, integers, and tags", async () => {
    const config = await getConfig(
      baseOpts({
        CC_LANGFUSE_DEBUG: "true",
        CC_LANGFUSE_MAX_CHARS: "500",
        CC_LANGFUSE_TAGS: "a, b ,c",
      }),
    );
    expect(config.debug).toBe(true);
    expect(config.max_chars).toBe(500);
    expect(config.tags).toEqual(["a", "b", "c"]);
  });
});

describe("getConfig — Claude Code user email fallback", () => {
  let home: string;

  beforeAll(async () => {
    home = await fs.mkdtemp(path.join(os.tmpdir(), "cc-langfuse-test-"));
    await fs.writeFile(
      path.join(home, ".claude.json"),
      JSON.stringify({ oauthAccount: { emailAddress: "dev@example.com" } }),
      "utf-8",
    );
  });

  afterAll(async () => {
    await fs.rm(home, { recursive: true, force: true });
  });

  it("uses the account email as user_id when none is configured", async () => {
    const config = await getConfig({ home, cwd: NONEXISTENT, env: {} });
    expect(config.user_id).toBe("dev@example.com");
  });

  it("lets an explicit user_id override the account email", async () => {
    const config = await getConfig({
      home,
      cwd: NONEXISTENT,
      env: { CC_LANGFUSE_USER_ID: "explicit-user" },
    });
    expect(config.user_id).toBe("explicit-user");
  });
});

describe("getConfig ported main options", () => {
  it("reads LANGFUSE_USER_ID with CC_LANGFUSE_USER_ID as fallback", async () => {
    const viaPlain = await getConfig(baseOpts({ LANGFUSE_USER_ID: "user-a" }));
    expect(viaPlain.user_id).toBe("user-a");
    const viaCc = await getConfig(baseOpts({ CC_LANGFUSE_USER_ID: "user-b" }));
    expect(viaCc.user_id).toBe("user-b");
    const both = await getConfig(
      baseOpts({ LANGFUSE_USER_ID: "user-a", CC_LANGFUSE_USER_ID: "user-b" }),
    );
    expect(both.user_id).toBe("user-a");
  });

  it("defaults skill_tags on and capture_skill_content off", async () => {
    const config = await getConfig(baseOpts({}));
    expect(config.skill_tags).toBe(true);
    expect(config.capture_skill_content).toBe(false);
  });

  it("reads the skill option environment variables", async () => {
    const config = await getConfig(
      baseOpts({ CC_LANGFUSE_SKILL_TAGS: "false", CC_LANGFUSE_CAPTURE_SKILL_CONTENT: "true" }),
    );
    expect(config.skill_tags).toBe(false);
    expect(config.capture_skill_content).toBe(true);
  });
});
