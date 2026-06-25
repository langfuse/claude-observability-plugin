import * as fs from "node:fs/promises";
import * as path from "node:path";

import { debugLog } from "./utils.js";

/**
 * A subagent transcript discovered alongside the main session transcript.
 *
 * Claude Code records each subagent (spawned via the `Agent`/`Task` tool) as
 * its own transcript next to the main one:
 *
 *   <project>/<sessionId>.jsonl                                  ← main transcript
 *   <project>/<sessionId>/subagents/agent-<id>.jsonl             ← subagent transcript
 *   <project>/<sessionId>/subagents/agent-<id>.meta.json         ← {agentType, description, toolUseId}
 *
 * The `meta.json`'s `toolUseId` is the `id` of the spawning tool_use block in
 * the main transcript, which is how we attach a subagent under the exact tool
 * call that launched it.
 */
export type SubagentInfo = {
  file: string;
  agentType: string;
  description?: string;
  toolUseId: string;
};

/** Map of spawning tool_use id → subagent transcript. */
export type SubagentIndex = Map<string, SubagentInfo>;

/**
 * Discover subagent transcripts for a main transcript by reading the sibling
 * `subagents/*.meta.json` files. Returns an empty index if there are none (or
 * the directory is missing) — subagents are optional.
 */
export async function discoverSubagents(transcriptPath: string): Promise<SubagentIndex> {
  const index: SubagentIndex = new Map();

  // "<dir>/<sessionId>.jsonl" → "<dir>/<sessionId>/subagents"
  const subDir = path.join(transcriptPath.replace(/\.jsonl$/i, ""), "subagents");

  let entries;
  try {
    entries = await fs.readdir(subDir, { withFileTypes: true });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      debugLog("failed to read subagents directory:", error);
    }
    return index;
  }

  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".meta.json")) continue;
    const metaPath = path.join(subDir, entry.name);
    try {
      const meta = JSON.parse(await fs.readFile(metaPath, "utf-8")) as {
        agentType?: unknown;
        description?: unknown;
        toolUseId?: unknown;
      };
      if (typeof meta.toolUseId !== "string" || !meta.toolUseId) continue;
      const file = path.join(subDir, entry.name.replace(/\.meta\.json$/, ".jsonl"));
      index.set(meta.toolUseId, {
        file,
        agentType: typeof meta.agentType === "string" ? meta.agentType : "subagent",
        description: typeof meta.description === "string" ? meta.description : undefined,
        toolUseId: meta.toolUseId,
      });
    } catch (error) {
      debugLog(`failed to parse subagent meta ${entry.name}:`, error);
    }
  }

  if (index.size > 0) debugLog(`discovered ${index.size} subagent transcript(s)`);
  return index;
}
