/**
 * Types for the subset of the Claude Code transcript JSONL we consume.
 *
 * Claude Code persists every session as a newline-delimited JSON file. Each
 * line is a "row" describing one message in the conversation. The shapes vary
 * slightly across Claude Code versions, so only the fields the tracer reads are
 * typed; everything else is left open via index signatures so we never crash on
 * an unknown version.
 *
 * A row is broadly one of:
 * - a user message      (`type: "user"`, or `message.role === "user"`)
 * - an assistant message (`type: "assistant"`, or `message.role === "assistant"`)
 *
 * Tool results are delivered as *user* rows whose `content` array contains
 * `tool_result` blocks. Tool calls are `tool_use` blocks inside assistant rows.
 */

export type ContentBlock = {
  type: string;
  text?: string;
  // tool_use
  id?: string;
  name?: string;
  input?: unknown;
  // tool_result
  tool_use_id?: string;
  content?: unknown;
  [key: string]: unknown;
};

export type TranscriptMessage = {
  role?: string;
  content?: string | ContentBlock[];
  model?: string;
  id?: string;
  usage?: Record<string, unknown>;
  [key: string]: unknown;
};

export type TranscriptRow = {
  type?: string;
  message?: TranscriptMessage;
  content?: string | ContentBlock[];
  timestamp?: string;
  /** True for injected rows (slash-command expansions, skill instructions). */
  isMeta?: boolean;
  /** On isMeta rows: the tool_use id the injected content belongs to. */
  sourceToolUseID?: string;
  cwd?: string;
  gitBranch?: string;
  [key: string]: unknown;
};

/** Payload Claude Code passes to the `Stop` hook on stdin. */
export type HookInput = {
  session_id?: string;
  sessionId?: string;
  transcript_path?: string;
  transcriptPath?: string;
  hook_event_name?: string;
  cwd?: string;
  [key: string]: unknown;
};

/** Anthropic token usage, normalized to Langfuse `usageDetails` keys. */
export type UsageDetails = Record<string, number>;

/** A single tool invocation: a `tool_use` block matched to its `tool_result`. */
export type ToolCall = {
  id: string;
  name: string;
  input: unknown;
  /** When the assistant emitted the tool_use. */
  startTime?: number;
  /** When the tool_result row arrived. */
  endTime?: number;
  output?: unknown;
};

/** A single assistant message within a turn (one LLM response). */
export type AssistantStep = {
  text?: string;
  model: string;
  usage?: UsageDetails;
  toolCalls: ToolCall[];
  timestamp?: number;
};

/** A fully assembled Claude Code turn, ready to convert into observations. */
export type Turn = {
  userText: string;
  userTimestamp?: number;
  finalAssistantText?: string;
  endTimestamp?: number;
  steps: AssistantStep[];
  /**
   * Injected context (e.g. skill instructions) keyed by the tool_use id it
   * belongs to, taken from isMeta rows carrying `sourceToolUseID`.
   */
  injectedByToolId: Map<string, string>;
  /** Project dir and git branch the turn ran in, from the user row. */
  cwd?: string;
  gitBranch?: string;
};
