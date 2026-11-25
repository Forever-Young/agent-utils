// agent-utils session names for Pi (installed by scripts/configure-session-names.py).
//
// - After each tool call, apply a name left by `agents agent rename` for this
//   session (state dir session-names/pending/pi-<session id>).
// - On session start/resume and on every rename, copy Pi's session name to the
//   agterm session. A no-op outside agterm.
import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  const state = join(process.env.XDG_STATE_HOME || join(homedir(), ".local", "state"), "agent-utils", "session-names");
  const key = (id: string) => "pi-" + id.replace(/[^A-Za-z0-9_.-]/g, "_");
  let last: string | undefined;

  async function toAgterm(name: string | undefined): Promise<void> {
    const target = process.env.AGTERM_SESSION_ID;
    if (!target || !name || name === last) return;
    last = name;
    const args = ["session", "rename", name, "--target", target];
    if (process.env.AGTERM_SOCKET) args.push("--socket", process.env.AGTERM_SOCKET);
    try {
      await pi.exec(process.env.AGTERMCTL || "agtermctl", args, { timeout: 5_000 });
    } catch {
      // Naming is advisory and must never interrupt Pi's agent loop.
    }
  }

  pi.on("session_start", async (_event, ctx) => {
    last = undefined;
    await toAgterm(pi.getSessionName() ?? ctx.sessionManager.getSessionName());
  });

  pi.on("session_info_changed", async (event) => {
    await toAgterm(event.name);
  });

  pi.on("tool_execution_end", async (_event, ctx) => {
    const id = ctx.sessionManager.getSessionId();
    if (!id) return;
    const path = join(state, "pending", key(id));
    if (!existsSync(path)) return;
    try {
      const name = readFileSync(path, "utf8").trim();
      unlinkSync(path);
      if (name) {
        last = name; // agterm already has it
        pi.setSessionName(name);
      }
    } catch {
      // Ignore a file removed by a concurrent reader.
    }
  });
}
