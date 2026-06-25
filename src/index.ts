import { getConfig } from "./config.js";
import { setupInstrumentation } from "./instrumentation.js";
import { convertTranscript } from "./trace.js";
import type { HookInput } from "./types.js";
import { debugLog, readStdin, setDebug } from "./utils.js";

let failOnError = process.env.CC_LANGFUSE_FAIL_ON_ERROR === "true";

/**
 * Entry point for the Claude Code `Stop` hook.
 *
 * Claude Code pipes a JSON payload to stdin after every turn. We resolve
 * config, bail out unless Langfuse credentials are present, then convert the
 * newly appended transcript rows into Langfuse traces.
 *
 * The hook fails open: any error is logged (in debug mode) and swallowed so a
 * tracing problem never blocks the Claude Code session. Set
 * `CC_LANGFUSE_FAIL_ON_ERROR=true` while testing if you want Claude Code to
 * report hook failures instead.
 */
export async function runHook(): Promise<void> {
  let hookInput: HookInput;
  try {
    hookInput = await readStdin<HookInput>();
  } catch {
    // No usable payload — nothing we can do.
    return;
  }

  const cwd = hookInput.cwd;
  const config = await getConfig(cwd ? { cwd } : undefined);
  setDebug(config.debug);
  failOnError = config.fail_on_error;

  if (!config.public_key || !config.secret_key) {
    debugLog("missing LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY; skipping");
    return;
  }

  const sessionId = hookInput.session_id ?? hookInput.sessionId;
  const transcriptPath = hookInput.transcript_path ?? hookInput.transcriptPath;
  if (!sessionId || !transcriptPath) {
    debugLog("hook payload missing session_id or transcript_path; skipping");
    return;
  }

  const instrumentation = setupInstrumentation(config);
  try {
    await convertTranscript(transcriptPath, sessionId, config);
  } catch (error) {
    debugLog("failed to convert transcript:", error);
    if (config.fail_on_error) throw error;
  } finally {
    try {
      await instrumentation.shutdown();
    } catch (error) {
      debugLog("error during flush/shutdown:", error);
      if (config.fail_on_error) throw error;
    }
  }
}

runHook().catch((error) => {
  // Last-resort guard: fail open unless explicitly requested for testing.
  if (process.env.CC_LANGFUSE_DEBUG === "true") {
    // eslint-disable-next-line no-console
    console.error("[langfuse-claude-code] fatal:", error);
  }
  if (failOnError) {
    process.exitCode = 1;
  }
});
