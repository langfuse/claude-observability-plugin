import { describe, expect, it } from "bun:test";

import { buildTurns } from "../src/parse.js";
import type { TranscriptRow } from "../src/types.js";

const rows: TranscriptRow[] = [
  {
    type: "user",
    timestamp: "2026-06-08T10:00:00.000Z",
    message: { role: "user", content: "list files" },
  },
  {
    type: "assistant",
    timestamp: "2026-06-08T10:00:01.000Z",
    message: {
      id: "msg_1",
      role: "assistant",
      model: "claude-opus-4-8",
      usage: { input_tokens: 120, output_tokens: 45, cache_read_input_tokens: 1000 },
      content: [
        { type: "text", text: "Checking." },
        { type: "tool_use", id: "tu_1", name: "Bash", input: { command: "ls" } },
      ],
    },
  },
  {
    type: "user",
    timestamp: "2026-06-08T10:00:02.500Z",
    message: {
      role: "user",
      content: [{ type: "tool_result", tool_use_id: "tu_1", content: "README.md\nsrc" }],
    },
  },
  {
    type: "assistant",
    timestamp: "2026-06-08T10:00:03.000Z",
    message: {
      id: "msg_2",
      role: "assistant",
      model: "claude-opus-4-8",
      usage: { input_tokens: 200, output_tokens: 20 },
      content: [{ type: "text", text: "Two entries." }],
    },
  },
  {
    type: "user",
    timestamp: "2026-06-08T10:01:00.000Z",
    message: { role: "user", content: "thanks" },
  },
  {
    type: "assistant",
    timestamp: "2026-06-08T10:01:01.000Z",
    message: {
      id: "msg_3",
      role: "assistant",
      model: "claude-opus-4-8",
      content: [{ type: "text", text: "Welcome!" }],
    },
  },
];

describe("buildTurns", () => {
  it("groups rows into turns delimited by user messages", () => {
    const turns = buildTurns(rows);
    expect(turns).toHaveLength(2);
    expect(turns[0].userText).toBe("list files");
    expect(turns[1].userText).toBe("thanks");
  });

  it("captures multiple assistant steps within a turn", () => {
    const [turn] = buildTurns(rows);
    expect(turn.steps).toHaveLength(2);
    expect(turn.finalAssistantText).toBe("Two entries.");
  });

  it("matches tool_use blocks to their tool_result", () => {
    const [turn] = buildTurns(rows);
    const tool = turn.steps[0].toolCalls[0];
    expect(tool.name).toBe("Bash");
    expect(tool.input).toEqual({ command: "ls" });
    expect(tool.output).toBe("README.md\nsrc");
    expect(tool.endTime).toBe(Date.parse("2026-06-08T10:00:02.500Z"));
  });

  it("normalizes Anthropic token usage to Langfuse usage keys", () => {
    const [turn] = buildTurns(rows);
    expect(turn.steps[0].usage).toEqual({
      input: 120,
      output: 45,
      cache_read_input_tokens: 1000,
    });
  });

  it("backdates turn timestamps from the transcript", () => {
    const [turn] = buildTurns(rows);
    expect(turn.userTimestamp).toBe(Date.parse("2026-06-08T10:00:00.000Z"));
    expect(turn.endTimestamp).toBe(Date.parse("2026-06-08T10:00:03.000Z"));
  });

  it("dedupes assistant messages by id, keeping the latest copy", () => {
    const streamed: TranscriptRow[] = [
      { type: "user", message: { role: "user", content: "hi" } },
      {
        type: "assistant",
        message: { id: "a", role: "assistant", content: [{ type: "text", text: "par" }] },
      },
      {
        type: "assistant",
        message: { id: "a", role: "assistant", content: [{ type: "text", text: "partial done" }] },
      },
    ];
    const [turn] = buildTurns(streamed);
    expect(turn.steps).toHaveLength(1);
    expect(turn.steps[0].text).toBe("partial done");
  });

  it("ignores assistant rows before any user message", () => {
    const orphan: TranscriptRow[] = [
      {
        type: "assistant",
        message: { id: "x", role: "assistant", content: [{ type: "text", text: "hi" }] },
      },
    ];
    expect(buildTurns(orphan)).toHaveLength(0);
  });
});

describe("buildTurns isMeta handling", () => {
  const skillRows: TranscriptRow[] = [
    {
      type: "user",
      timestamp: "2026-06-08T11:00:00.000Z",
      message: { role: "user", content: "/my-skill do the thing" },
      cwd: "/home/dev/project",
      gitBranch: "feature/x",
    },
    {
      type: "assistant",
      timestamp: "2026-06-08T11:00:01.000Z",
      message: {
        id: "msg_s1",
        role: "assistant",
        content: [
          { type: "tool_use", id: "tu_skill", name: "Skill", input: { skill: "my-skill" } },
        ],
      },
    },
    // Injected skill instructions: isMeta user row linked via sourceToolUseID.
    {
      type: "user",
      isMeta: true,
      sourceToolUseID: "tu_skill",
      timestamp: "2026-06-08T11:00:02.000Z",
      message: { role: "user", content: "You are running my-skill. Follow these steps..." },
    },
    {
      type: "user",
      timestamp: "2026-06-08T11:00:03.000Z",
      message: {
        role: "user",
        content: [{ type: "tool_result", tool_use_id: "tu_skill", content: "Launched skill" }],
      },
    },
    {
      type: "assistant",
      timestamp: "2026-06-08T11:00:04.000Z",
      message: {
        id: "msg_s2",
        role: "assistant",
        content: [{ type: "text", text: "Done." }],
      },
    },
  ];

  it("does not treat isMeta rows as turn boundaries", () => {
    const turns = buildTurns(skillRows);
    expect(turns).toHaveLength(1);
    expect(turns[0].steps).toHaveLength(2);
    expect(turns[0].finalAssistantText).toBe("Done.");
  });

  it("captures injected instructions keyed by sourceToolUseID", () => {
    const [turn] = buildTurns(skillRows);
    expect(turn.injectedByToolId.get("tu_skill")).toBe(
      "You are running my-skill. Follow these steps...",
    );
  });

  it("captures cwd and git branch from the user row", () => {
    const [turn] = buildTurns(skillRows);
    expect(turn.cwd).toBe("/home/dev/project");
    expect(turn.gitBranch).toBe("feature/x");
  });
});
