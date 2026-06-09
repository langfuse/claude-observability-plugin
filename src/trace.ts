import { propagateAttributes, startObservation } from "@langfuse/tracing";

import type { Config } from "./config.js";
import { buildTurns } from "./parse.js";
import { loadState, readNewRows, saveState } from "./state.js";
import type { AssistantStep, ToolCall, Turn } from "./types.js";
import { debugLog, makeClip, toText, type Clip } from "./utils.js";

/** A Date is required to backdate a span; fall back to `undefined` (= now). */
function asDate(ts: number | undefined): Date | undefined {
  return ts !== undefined ? new Date(ts) : undefined;
}

function buildGenerationOutput(
  step: AssistantStep,
  clip: Clip,
): Record<string, unknown> | undefined {
  const output: Record<string, unknown> = { role: "assistant" };
  if (step.text) output.content = clip(step.text);
  if (step.toolCalls.length > 0) {
    output.tool_calls = step.toolCalls.map((tc) => ({
      id: tc.id,
      name: tc.name,
      input: clip(tc.input),
    }));
  }
  // Only "role" present → nothing meaningful to show.
  return Object.keys(output).length > 1 ? output : undefined;
}

function emitToolCall(
  tc: ToolCall,
  parent: ReturnType<typeof startObservation>,
  clip: Clip,
  fallbackEnd: number | undefined,
): void {
  const tool = startObservation(
    `Tool: ${tc.name}`,
    {
      input: clip(tc.input),
      output: tc.output != null ? clip(toText(tc.output)) : undefined,
      metadata: { "claude.tool_id": tc.id, "claude.tool_name": tc.name },
    },
    {
      asType: "tool",
      startTime: asDate(tc.startTime),
      parentSpanContext: parent.otelSpan.spanContext(),
    },
  );
  tool.end(asDate(tc.endTime ?? tc.startTime ?? fallbackEnd));
}

/** Emit a single turn as a Langfuse observation tree. */
function emitTurn(turn: Turn, turnNum: number, transcriptPath: string, config: Config): void {
  const clip = makeClip(config.max_chars);

  const root = startObservation(
    `Claude Code - Turn ${turnNum}`,
    {
      input: { role: "user", content: clip(turn.userText) },
      output:
        turn.finalAssistantText != null
          ? { role: "assistant", content: clip(turn.finalAssistantText) }
          : undefined,
      metadata: {
        "claude.source": "claude-code",
        "claude.turn_number": turnNum,
        "claude.transcript_path": transcriptPath,
        "claude.assistant_message_count": turn.steps.length,
      },
    },
    {
      asType: "span",
      startTime: asDate(turn.userTimestamp),
    },
  );

  // The moment the next generation could have started: the original user
  // message, or when the previous batch of tool results all returned.
  let prevTs = turn.userTimestamp;
  let prevToolResults: Array<Record<string, unknown>> | undefined;

  turn.steps.forEach((step, idx) => {
    const input =
      idx === 0
        ? { role: "user", content: clip(turn.userText) }
        : prevToolResults
          ? { role: "tool", tool_results: prevToolResults }
          : undefined;

    const generation = startObservation(
      "Claude Generation",
      {
        input,
        output: buildGenerationOutput(step, clip),
        model: step.model,
        usageDetails: step.usage,
        metadata: { "claude.step_index": idx, "claude.tool_count": step.toolCalls.length },
      },
      {
        asType: "generation",
        startTime: asDate(prevTs ?? step.timestamp),
        parentSpanContext: root.otelSpan.spanContext(),
      },
    );

    const resultTimes: number[] = [];
    for (const tc of step.toolCalls) {
      emitToolCall(tc, generation, clip, step.timestamp);
      if (tc.endTime !== undefined) resultTimes.push(tc.endTime);
    }

    // End the generation after its tools so the timeline cleanly contains them.
    const genEnd = resultTimes.length > 0 ? Math.max(...resultTimes) : (step.timestamp ?? prevTs);
    generation.end(asDate(genEnd));

    // Carry this batch's results into the next generation's input.
    prevToolResults =
      step.toolCalls.length > 0
        ? step.toolCalls.map((tc) => ({
            tool_use_id: tc.id,
            tool_name: tc.name,
            output: tc.output != null ? clip(toText(tc.output)) : undefined,
          }))
        : undefined;

    // The next generation can only start once this batch's results returned.
    if (resultTimes.length > 0) prevTs = Math.max(...resultTimes);
    else if (step.timestamp !== undefined) prevTs = step.timestamp;
  });

  root.end(asDate(turn.endTimestamp ?? prevTs ?? turn.userTimestamp));
}

/**
 * Convert the newly appended part of a Claude Code transcript into Langfuse
 * traces. Each turn becomes its own trace, grouped into a Langfuse session via
 * the Claude Code session id. State is tracked in a sidecar so each turn is
 * uploaded exactly once.
 */
export async function convertTranscript(
  transcriptPath: string,
  sessionId: string,
  config: Config,
): Promise<void> {
  const state = await loadState(transcriptPath);
  const { rows, offset } = await readNewRows(transcriptPath, state);

  if (rows.length === 0) {
    debugLog("no new transcript rows to process");
    await saveState(transcriptPath, { ...state, offset });
    return;
  }

  const turns = buildTurns(rows);
  debugLog(`parsed ${turns.length} new turn(s) from ${transcriptPath}`);

  let emitted = 0;
  for (const turn of turns) {
    const turnNum = state.turnCount + emitted + 1;
    try {
      await propagateAttributes(
        {
          sessionId,
          traceName: `Claude Code - Turn ${turnNum}`,
          tags: ["claude-code", ...(config.tags ?? [])],
          ...(config.user_id ? { userId: config.user_id } : {}),
          ...(config.metadata ? { metadata: config.metadata } : {}),
        },
        async () => {
          emitTurn(turn, turnNum, transcriptPath, config);
        },
      );
      emitted += 1;
    } catch (error) {
      debugLog(`failed to emit turn ${turnNum}:`, error);
      if (config.fail_on_error) throw error;
    }
  }

  await saveState(transcriptPath, { offset, turnCount: state.turnCount + emitted });
}
