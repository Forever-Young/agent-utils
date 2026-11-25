// agent-utils Zellij tab status for Pi (installed by scripts/configure-zellij-status.py).
//
// Writes the OSC 7501 program status to the pane's terminal: working when Pi starts a turn, done when
// it settles. Zellij shows it on the tab and in its inbox. A no-op outside a Zellij pane.
import { closeSync, openSync, writeSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

function emit(state: string): void {
  if (process.env.ZELLIJ === undefined || !process.env.ZELLIJ_PANE_ID) return;
  try {
    const fd = openSync("/dev/tty", "w");
    writeSync(fd, `\x1b]7501;state=${state}:app=pi\x1b\\`);
    closeSync(fd);
  } catch {
    // Status is advisory and must never interrupt Pi's agent loop.
  }
}

export default function (pi: ExtensionAPI) {
  pi.on("agent_start", async () => emit("working"));
  // `agent_settled` waits for automatic retries, compaction retries and queued continuations.
  pi.on("agent_settled", async () => emit("done"));
}
