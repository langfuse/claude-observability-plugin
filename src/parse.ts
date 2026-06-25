import type {
  AssistantStep,
  ContentBlock,
  ToolCall,
  TranscriptMessage,
  TranscriptRow,
  Turn,
  UsageDetails,
} from "./types.js";
import { parseTimestamp } from "./utils.js";

function getMessage(row: TranscriptRow): TranscriptMessage | undefined {
  return row.message && typeof row.message === "object" ? row.message : undefined;
}

function getContent(row: TranscriptRow): string | ContentBlock[] | undefined {
  const msg = getMessage(row);
  if (msg && msg.content !== undefined) return msg.content;
  return row.content;
}

/** Resolve a row's role from `type` or `message.role`. */
function getRole(row: TranscriptRow): "user" | "assistant" | undefined {
  if (row.type === "user" || row.type === "assistant") return row.type;
  const role = getMessage(row)?.role;
  if (role === "user" || role === "assistant") return role;
  return undefined;
}

function blocks(content: string | ContentBlock[] | undefined): ContentBlock[] {
  return Array.isArray(content) ? content : [];
}

/** Concatenate the text blocks (or a plain string) of a message's content. */
function extractText(content: string | ContentBlock[] | undefined): string {
  if (typeof content === "string") return content;
  return blocks(content)
    .filter((b) => b.type === "text" && typeof b.text === "string")
    .map((b) => b.text as string)
    .filter(Boolean)
    .join("\n");
}

function getToolUses(content: string | ContentBlock[] | undefined): ContentBlock[] {
  return blocks(content).filter((b) => b.type === "tool_use");
}

function getToolResults(content: string | ContentBlock[] | undefined): ContentBlock[] {
  return blocks(content).filter((b) => b.type === "tool_result");
}

/** A user row is a tool-result carrier when its content holds tool_result blocks. */
function isToolResultRow(row: TranscriptRow): boolean {
  return getRole(row) === "user" && getToolResults(getContent(row)).length > 0;
}

function getModel(row: TranscriptRow): string {
  const model = getMessage(row)?.model;
  return typeof model === "string" && model ? model : "claude";
}

function getMessageId(row: TranscriptRow): string | undefined {
  const id = getMessage(row)?.id;
  return typeof id === "string" && id ? id : undefined;
}

/** Normalize Anthropic token usage to Langfuse `usageDetails` keys. */
function getUsage(row: TranscriptRow): UsageDetails | undefined {
  const usage = getMessage(row)?.usage;
  if (!usage || typeof usage !== "object") return undefined;
  const u = usage as Record<string, unknown>;
  const details: UsageDetails = {};
  const map: Array<[string, string]> = [
    ["input_tokens", "input"],
    ["output_tokens", "output"],
    ["cache_read_input_tokens", "cache_read_input_tokens"],
    ["cache_creation_input_tokens", "cache_creation_input_tokens"],
  ];
  for (const [src, dst] of map) {
    const v = u[src];
    if (typeof v === "number" && v > 0) details[dst] = v;
  }
  return Object.keys(details).length > 0 ? details : undefined;
}

/**
 * Group transcript rows into turns.
 *
 * A turn is: one user message (not a tool-result carrier), followed by the
 * assistant messages it produced, plus the tool_result rows those tool calls
 * resolved to (which arrive as later `user` rows). Assistant messages are
 * deduped by `message.id` — Claude Code can rewrite a streaming row in place,
 * and the latest copy wins — while preserving first-appearance order.
 */
export function buildTurns(rows: TranscriptRow[]): Turn[] {
  const turns: Turn[] = [];

  let currentUser: TranscriptRow | null = null;
  let assistantOrder: string[] = [];
  let assistantLatest = new Map<string, TranscriptRow>();
  let toolResultsById = new Map<string, { content: unknown; timestamp?: number }>();
  let injectedByToolId = new Map<string, string>();

  const flush = () => {
    if (currentUser === null || assistantLatest.size === 0) return;

    const userContent = getContent(currentUser);
    const userText = extractText(userContent);
    const userTimestamp = parseTimestamp(currentUser.timestamp);

    const assistants = assistantOrder
      .map((id) => assistantLatest.get(id))
      .filter((m): m is TranscriptRow => m !== undefined);

    const steps: AssistantStep[] = assistants.map((am) => {
      const amContent = getContent(am);
      const amTs = parseTimestamp(am.timestamp);
      const toolCalls: ToolCall[] = getToolUses(amContent).map((tu) => {
        const id = String(tu.id ?? "");
        const result = id ? toolResultsById.get(id) : undefined;
        return {
          id,
          name: typeof tu.name === "string" ? tu.name : "unknown",
          input: tu.input,
          startTime: amTs,
          endTime: result?.timestamp,
          output: result?.content,
        };
      });
      return {
        text: extractText(amContent) || undefined,
        model: getModel(am),
        usage: getUsage(am),
        toolCalls,
        timestamp: amTs,
      };
    });

    const lastAssistant = assistants[assistants.length - 1];
    const finalAssistantText = extractText(getContent(lastAssistant)) || undefined;

    // Turn ends at the latest of the final assistant message or any tool result.
    const candidateEnds = [parseTimestamp(lastAssistant.timestamp)];
    for (const tr of toolResultsById.values()) {
      if (tr.timestamp !== undefined) candidateEnds.push(tr.timestamp);
    }
    const endTimestamp = candidateEnds
      .filter((t): t is number => t !== undefined)
      .reduce<number | undefined>((max, t) => (max === undefined || t > max ? t : max), undefined);

    turns.push({
      userText,
      userTimestamp,
      finalAssistantText,
      endTimestamp,
      steps,
      injectedByToolId: new Map(injectedByToolId),
      cwd: typeof currentUser.cwd === "string" && currentUser.cwd ? currentUser.cwd : undefined,
      gitBranch:
        typeof currentUser.gitBranch === "string" && currentUser.gitBranch
          ? currentUser.gitBranch
          : undefined,
    });
  };

  for (const row of rows) {
    // Injected user rows (slash-command expansions, caveats, skill instructions)
    // carry isMeta=true. They are not real prompts — treating them as turn starts
    // creates phantom turns and prematurely flushes the real one.
    if (row.isMeta) {
      // Skill invocations link their injected instructions to the originating
      // tool_use via sourceToolUseID; keep the text so emit can optionally
      // attach it to that tool span.
      if (row.sourceToolUseID) {
        const text = extractText(getContent(row));
        if (text) injectedByToolId.set(String(row.sourceToolUseID), text);
      }
      continue;
    }

    if (isToolResultRow(row)) {
      const ts = parseTimestamp(row.timestamp);
      for (const tr of getToolResults(getContent(row))) {
        if (tr.tool_use_id) {
          toolResultsById.set(String(tr.tool_use_id), { content: tr.content, timestamp: ts });
        }
      }
      continue;
    }

    const role = getRole(row);

    if (role === "user") {
      // New user message → finalize the previous turn and start a new one.
      flush();
      currentUser = row;
      assistantOrder = [];
      assistantLatest = new Map();
      toolResultsById = new Map();
      injectedByToolId = new Map();
      continue;
    }

    if (role === "assistant") {
      if (currentUser === null) continue; // ignore assistant rows before any user message
      const id = getMessageId(row) ?? `noid:${assistantOrder.length}`;
      if (!assistantLatest.has(id)) assistantOrder.push(id);
      assistantLatest.set(id, row);
      continue;
    }
    // ignore unknown rows
  }

  flush();
  return turns;
}
