import { describe, expect, it } from "bun:test";

import { buildTurns } from "../src/parse.js";
import { collectSkillTags } from "../src/trace.js";
import type { TranscriptRow } from "../src/types.js";

describe("collectSkillTags", () => {
  it("returns a deduped skill:<name> tag per invoked skill", () => {
    const rows: TranscriptRow[] = [
      { type: "user", message: { role: "user", content: "run skills" } },
      {
        type: "assistant",
        message: {
          id: "a1",
          role: "assistant",
          content: [
            { type: "tool_use", id: "t1", name: "Skill", input: { skill: "deep-research" } },
            { type: "tool_use", id: "t2", name: "Skill", input: { skill: "deep-research" } },
            { type: "tool_use", id: "t3", name: "Skill", input: { skill: "verify" } },
            { type: "tool_use", id: "t4", name: "Bash", input: { command: "ls" } },
          ],
        },
      },
    ];
    const [turn] = buildTurns(rows);
    expect(collectSkillTags(turn)).toEqual(["skill:deep-research", "skill:verify"]);
  });

  it("returns no tags when no Skill tool was used", () => {
    const rows: TranscriptRow[] = [
      { type: "user", message: { role: "user", content: "hi" } },
      {
        type: "assistant",
        message: { id: "a1", role: "assistant", content: [{ type: "text", text: "hello" }] },
      },
    ];
    const [turn] = buildTurns(rows);
    expect(collectSkillTags(turn)).toEqual([]);
  });
});
