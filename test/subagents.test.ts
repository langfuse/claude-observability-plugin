import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "bun:test";

import { discoverSubagents } from "../src/subagents.js";

describe("discoverSubagents", () => {
  let dir: string;
  let transcript: string;

  beforeAll(async () => {
    dir = await fs.mkdtemp(path.join(os.tmpdir(), "cc-subagents-"));
    const sessionId = "11111111-2222-3333-4444-555555555555";
    transcript = path.join(dir, `${sessionId}.jsonl`);
    await fs.writeFile(transcript, "", "utf-8");

    const subDir = path.join(dir, sessionId, "subagents");
    await fs.mkdir(subDir, { recursive: true });
    await fs.writeFile(
      path.join(subDir, "agent-abc.meta.json"),
      JSON.stringify({ agentType: "Explore", description: "find things", toolUseId: "toolu_1" }),
      "utf-8",
    );
    await fs.writeFile(path.join(subDir, "agent-abc.jsonl"), "", "utf-8");
    // A meta with no toolUseId should be ignored.
    await fs.writeFile(
      path.join(subDir, "agent-def.meta.json"),
      JSON.stringify({ agentType: "Plan" }),
      "utf-8",
    );
  });

  afterAll(async () => {
    await fs.rm(dir, { recursive: true, force: true });
  });

  it("indexes subagents by their spawning tool_use id", async () => {
    const index = await discoverSubagents(transcript);
    expect(index.size).toBe(1);
    const info = index.get("toolu_1");
    expect(info?.agentType).toBe("Explore");
    expect(info?.description).toBe("find things");
    expect(info?.file).toBe(
      path.join(dir, "11111111-2222-3333-4444-555555555555", "subagents", "agent-abc.jsonl"),
    );
  });

  it("returns an empty index when there is no subagents directory", async () => {
    const index = await discoverSubagents(path.join(dir, "no-such-session.jsonl"));
    expect(index.size).toBe(0);
  });
});
