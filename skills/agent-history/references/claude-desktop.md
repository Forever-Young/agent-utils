# Claude Desktop cache (macOS)

`scripts/read_claude.py` reads Claude Desktop's Chromium HTTP cache offline. It does
not open databases, modify Claude, read cookies or use the network. Zstd responses
need libzstd (Homebrew's is found).

```sh
python3 <skill-dir>/scripts/read_claude.py status
python3 <skill-dir>/scripts/read_claude.py conversations --query "topic"
python3 <skill-dir>/scripts/read_claude.py projects
python3 <skill-dir>/scripts/read_claude.py conversation UUID
python3 <skill-dir>/scripts/read_claude.py project UUID
python3 <skill-dir>/scripts/read_claude.py search "phrase"
```

Global options go before the subcommand: `--org UUID` limits to one organization;
`--cache PATH` overrides `~/Library/Application Support/Claude/Cache/Cache_Data`.

Listing prints metadata, search prints bounded excerpts, retrieval prints the
original JSON with its source file and cache modification time. When a record is
cached more than once, detail responses win, then the newest cache time, so a detail
record can be older than list metadata. Decode errors are returned, not hidden.

- Conversations: read `data.chat_messages`. Keep sender, order and branches
  (`parent_message_uuid`, `current_leaf_message_uuid`). Text can appear in both
  `text` and structured `content`; do not duplicate it. Flag truncated messages and
  missing attachments. A cached title alone does not mean the messages are cached.
- Project rules: `data.prompt_template` of a project detail record. Tell an empty
  template from an absent field or a missing detail response. Descriptions and
  conversation summaries are context, not rules. Project files and account-level
  preferences are not covered.

Describe results as cached and possibly stale or incomplete. Cache time is
provenance, not when the user edited the chat. If something is missing, suggest
opening that conversation or project in Claude Desktop and retrying; caching is not
guaranteed. Do not claim full account coverage, and do not substitute Claude Code
session logs.
