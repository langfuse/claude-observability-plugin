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
