import * as fs from "node:fs/promises";

import type { TranscriptRow } from "./types.js";
import { debugLog } from "./utils.js";

/** Parse newline-delimited JSON transcript text into rows, skipping bad lines. */
export function parseRows(text: string): TranscriptRow[] {
  const rows: TranscriptRow[] = [];
  for (const raw of text.split("\n")) {
    const trimmed = raw.trim();
    if (!trimmed) continue;
    try {
      rows.push(JSON.parse(trimmed) as TranscriptRow);
    } catch {
      // skip malformed lines rather than aborting the whole upload
    }
  }
  return rows;
}

/** Read and parse an entire transcript file (used for subagent transcripts). */
export async function readAllRows(file: string): Promise<TranscriptRow[]> {
  return parseRows(await fs.readFile(file, "utf-8"));
}

/**
 * Per-transcript dedup state.
 *
 * The `Stop` hook fires after every turn and the whole transcript is appended
 * to over the life of a session, so re-reading from the top each time would
 * re-upload every turn. We keep a sidecar file (`<transcript>.langfuse`) next
 * to the transcript recording the byte offset already processed and how many
 * turns have been emitted so far, then on the next invocation read only the
 * bytes appended since.
 */
export type TranscriptState = {
  /** Byte offset into the transcript already processed. */
  offset: number;
  /** Number of turns emitted so far (for stable turn numbering). */
  turnCount: number;
};

const EMPTY_STATE: TranscriptState = { offset: 0, turnCount: 0 };

function sidecarPath(transcriptPath: string): string {
  return `${transcriptPath}.langfuse`;
}

export async function loadState(transcriptPath: string): Promise<TranscriptState> {
  try {
    const raw = JSON.parse(
      await fs.readFile(sidecarPath(transcriptPath), "utf-8"),
    ) as Partial<TranscriptState>;
    return {
      offset: Number.isFinite(raw.offset) ? Number(raw.offset) : 0,
      turnCount: Number.isFinite(raw.turnCount) ? Number(raw.turnCount) : 0,
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
      debugLog("failed to read state sidecar; starting fresh:", error);
    }
    return { ...EMPTY_STATE };
  }
}

export async function saveState(transcriptPath: string, state: TranscriptState): Promise<void> {
  try {
    await fs.writeFile(sidecarPath(transcriptPath), JSON.stringify(state), "utf-8");
  } catch (error) {
    // Best-effort: a failed write only risks re-uploading turns next time.
    debugLog("failed to write state sidecar:", error);
  }
}

/**
 * Read transcript rows appended since `state.offset`.
 *
 * Reads raw bytes from the offset to EOF, then only consumes up to the last
 * complete line (the final newline). A partial trailing line — the transcript
 * may still be mid-write — is left for the next invocation by not advancing the
 * offset past it. If the file shrank (rotation/truncation), we restart from the
 * beginning.
 */
export async function readNewRows(
  transcriptPath: string,
  state: TranscriptState,
): Promise<{ rows: TranscriptRow[]; offset: number }> {
  let offset = state.offset;
  let size: number;
  try {
    size = (await fs.stat(transcriptPath)).size;
  } catch (error) {
    debugLog("failed to stat transcript:", error);
    return { rows: [], offset };
  }

  if (size < offset) {
    debugLog(`transcript shrank (${size} < ${offset}); restarting from the beginning`);
    offset = 0;
  }
  if (size === offset) {
    return { rows: [], offset };
  }

  const length = size - offset;
  const buffer = Buffer.alloc(length);
  let fh: fs.FileHandle | undefined;
  try {
    fh = await fs.open(transcriptPath, "r");
    await fh.read(buffer, 0, length, offset);
  } catch (error) {
    debugLog("failed to read transcript:", error);
    return { rows: [], offset };
  } finally {
    await fh?.close();
  }

  const lastNewline = buffer.lastIndexOf(0x0a);
  if (lastNewline < 0) {
    // No complete line yet — wait for the next invocation.
    return { rows: [], offset };
  }

  const complete = buffer.subarray(0, lastNewline + 1);
  const newOffset = offset + complete.length;

  return { rows: parseRows(complete.toString("utf-8")), offset: newOffset };
}
