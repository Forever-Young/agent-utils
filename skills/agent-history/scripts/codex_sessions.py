#!/usr/bin/env python3
"""Read local OpenAI Codex sessions and rules from ~/.codex (read-only).

Usage:
  codex_sessions.py list [--project PATH] [--limit N] [--archived]
  codex_sessions.py show ID_OR_PREFIX [--tools] [--max-chars N]
  codex_sessions.py search TEXT [--project PATH] [--limit N] [--archived]
  codex_sessions.py rules [PROJECT_PATH]
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

CODEX = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
INJECTED_PREFIXES = ("<environment_context", "<external_", "# AGENTS.md instructions",
                     "<user_instructions", "<turn_aborted", "<permissions")


def open_lines(path):
    """Yield lines from a .jsonl or .jsonl.zst rollout."""
    if path.suffix == ".zst":
        proc = subprocess.Popen(["zstd", "-dcq", str(path)], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True)
        try:
            yield from proc.stdout
        finally:
            proc.kill()
            proc.wait()
    else:
        with open(path, encoding="utf-8", errors="replace") as f:
            yield from f


def session_files(archived=False):
    dirs = [CODEX / "sessions"] + ([CODEX / "archived_sessions"] if archived else [])
    files = []
    for d in dirs:
        if d.exists():
            files += [p for p in d.rglob("rollout-*") if p.name.endswith((".jsonl", ".jsonl.zst"))]
    return sorted(files, key=lambda p: p.name, reverse=True)


def session_id(path):
    name = path.name.split(".")[0]  # rollout-<ts>-<uuid>
    return name[-36:]


def titles():
    out = {}
    idx = CODEX / "session_index.jsonl"
    if idx.exists():
        for line in open(idx, encoding="utf-8"):
            try:
                d = json.loads(line)
                out[d["id"]] = d.get("thread_name")
            except (ValueError, KeyError):
                pass
    return out


def meta(path):
    for line in open_lines(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "session_meta":
            p = d.get("payload", {})
            return {"cwd": p.get("cwd"), "timestamp": p.get("timestamp"),
                    "originator": p.get("originator")}
        break
    return {}


def text_of(content):
    parts = []
    for c in content or []:
        if isinstance(c, dict) and c.get("text"):
            parts.append(c["text"])
    return "\n".join(parts)


def messages(path, tools=False):
    """Yield (role, text) for the real conversation, skipping injected context."""
    for line in open_lines(path):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "response_item":
            continue
        p = d.get("payload", {})
        t = p.get("type")
        if t == "message" and p.get("role") in ("user", "assistant"):
            text = text_of(p.get("content")).strip()
            if not text or (p["role"] == "user" and text.startswith(INJECTED_PREFIXES)):
                continue
            yield p["role"], text
        elif tools and t in ("function_call", "custom_tool_call", "local_shell_call"):
            args = p.get("arguments") or p.get("input") or json.dumps(p.get("action", ""))
            yield "tool_call", f"{p.get('name', t)}: {args}"
        elif tools and t in ("function_call_output", "custom_tool_call_output"):
            out = p.get("output")
            if isinstance(out, dict):
                out = out.get("content") or json.dumps(out)
            yield "tool_output", str(out)


def under(cwd, project):
    if not project:
        return True
    project = str(Path(project).expanduser().resolve())
    return bool(cwd) and (cwd == project or cwd.startswith(project + os.sep))


def cmd_list(a):
    names, n = titles(), 0
    for f in session_files(a.archived):
        m = meta(f)
        if not under(m.get("cwd"), a.project):
            continue
        sid = session_id(f)
        print(f"{sid}  {(m.get('timestamp') or '')[:16]}  {m.get('cwd') or '?'}\n    {names.get(sid) or '(untitled)'}")
        n += 1
        if n >= a.limit:
            break


def find_file(prefix):
    matches = [f for f in session_files(True) if session_id(f).startswith(prefix)]
    if not matches:
        sys.exit(f"No session matching {prefix}")
    if len(matches) > 1:
        sys.exit("Ambiguous id, matches: " + ", ".join(session_id(f) for f in matches[:10]))
    return matches[0]


def cmd_show(a):
    f = find_file(a.id)
    m, sid = meta(f), session_id(f)
    print(f"# {titles().get(sid) or '(untitled)'}\nid: {sid}\ncwd: {m.get('cwd')}\nstarted: {m.get('timestamp')}\nfile: {f}\n")
    for role, text in messages(f, a.tools):
        if a.max_chars and len(text) > a.max_chars:
            text = text[:a.max_chars] + f" …[+{len(text) - a.max_chars} chars]"
        print(f"## {role}\n{text}\n")


def cmd_search(a):
    q, names, n = a.text.lower(), titles(), 0
    for f in session_files(a.archived):
        m = meta(f)
        if not under(m.get("cwd"), a.project):
            continue
        sid = session_id(f)
        hits = []
        if q in (names.get(sid) or "").lower():
            hits.append(("title", names[sid]))
        for role, text in messages(f):
            i = text.lower().find(q)
            if i >= 0:
                hits.append((role, text[max(0, i - 80):i + 120].replace("\n", " ")))
            if len(hits) >= 3:
                break
        if hits:
            print(f"{sid}  {(m.get('timestamp') or '')[:16]}  {m.get('cwd')}\n    {names.get(sid) or '(untitled)'}")
            for role, snip in hits:
                print(f"    [{role}] …{snip}…")
            n += 1
            if n >= a.limit:
                break


def cat(path):
    print(f"===== {path} =====")
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    print(text if text.strip() else "(empty)")
    print()


def cmd_rules(a):
    for name in ("AGENTS.override.md", "AGENTS.md"):
        p = CODEX / name
        if p.exists():
            cat(p)
            break
    rules = CODEX / "rules"
    if rules.exists():
        for p in sorted(rules.glob("*.rules")):
            cat(p)
    if a.project:
        # Codex reads AGENTS.md from the git root down to the project dir.
        proj = Path(a.project).expanduser().resolve()
        chain = [proj, *proj.parents]
        root = next((d for d in chain if (d / ".git").exists()), proj)
        dirs = [d for d in reversed(chain) if d == root or root in d.parents]
        for d in dirs:
            for name in ("AGENTS.override.md", "AGENTS.md"):
                if (d / name).exists():
                    cat(d / name)
                    break
            for p in sorted((d / ".codex").glob("**/*.rules")) if (d / ".codex").exists() else []:
                cat(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--project"); p.add_argument("--limit", type=int, default=20)
    p.add_argument("--archived", action="store_true"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("show"); p.add_argument("id"); p.add_argument("--tools", action="store_true")
    p.add_argument("--max-chars", type=int, default=4000); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("search"); p.add_argument("text"); p.add_argument("--project")
    p.add_argument("--limit", type=int, default=15); p.add_argument("--archived", action="store_true")
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("rules"); p.add_argument("project", nargs="?"); p.set_defaults(fn=cmd_rules)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
