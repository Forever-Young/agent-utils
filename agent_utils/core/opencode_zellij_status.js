// agent-utils Zellij tab status for OpenCode v2 (installed by scripts/configure-zellij-status.py).
//
// A TUI plugin: it runs in the terminal client, so it knows this pane. It follows the selected session
// and its child sessions and writes the OSC 7501 program status to the pane's terminal:
// blocked (permission or form waiting), working (a turn runs), done or error (the turn ended), idle.
// Zellij shows it on the tab and in its inbox. A no-op outside a Zellij pane.
import { closeSync, openSync, writeSync } from "node:fs";

function emit(state) {
  try {
    const fd = openSync("/dev/tty", "w");
    writeSync(fd, `\x1b]7501;state=${state}${state === "blocked" ? ":kind=permission" : ""}:app=opencode\x1b\\`);
    closeSync(fd);
  } catch {
    // Status is advisory and must never break OpenCode.
  }
}

export default {
  id: "agent-utils.zellij-status",
  setup(ctx) {
    if (process.env.ZELLIJ === undefined || !process.env.ZELLIJ_PANE_ID) return;
    let selected = null;
    const family = new Set();
    const waiting = new Set();
    let running = false;
    let outcome = null;
    let last;

    const publish = () => {
      const state = waiting.size ? "blocked" : running ? "working" : outcome || "idle";
      if (state === last) return;
      last = state;
      emit(state);
    };

    const route = () => {
      const current = ctx.ui.router.current();
      const id = current.type === "session" ? current.sessionID : null;
      if (id === selected) return;
      selected = id;
      family.clear();
      if (id) family.add(id);
      waiting.clear();
      running = false;
      outcome = null;
      publish();
    };

    const stop = ctx.data.listen(({ details: event }) => {
      route();
      const data = event.data ?? {};
      if (event.type === "session.created" && family.has(data.parentID)) family.add(data.sessionID);
      const id = data.sessionID ?? data.form?.sessionID;
      if (!family.has(id)) return;
      switch (event.type) {
        case "session.execution.started":
          running = true;
          outcome = null;
          break;
        case "session.execution.succeeded":
        case "session.execution.failed":
        case "session.execution.interrupted":
          if (id !== selected) break; // a child's turn ending does not end the parent's
          running = false;
          waiting.clear();
          outcome = event.type === "session.execution.succeeded" ? "done"
            : event.type === "session.execution.failed" ? "error" : null;
          break;
        case "permission.asked":
          waiting.add(data.id);
          break;
        case "permission.replied":
          waiting.delete(data.requestID);
          break;
        case "form.created":
          waiting.add(data.form.id);
          break;
        case "form.replied":
        case "form.cancelled":
          waiting.delete(data.id);
          break;
        default:
          return;
      }
      publish();
    });
    // The router has no subscription; check the selected session locally (no RPC).
    const timer = setInterval(route, 250);
    route();
    return async () => {
      stop();
      clearInterval(timer);
      emit("clear");
    };
  },
};
