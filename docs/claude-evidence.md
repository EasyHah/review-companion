# Claude Code native evidence

`python3 tests/native-claude.py` passed with the installed Claude Code **2.1.295** executable and the unified Review Companion package **0.3.0**.

The test runs normal `--plugin-dir` loading, with an isolated temporary `CLAUDE_CONFIG_DIR` and `--setting-sources ''`. It sets explicit synthetic Anthropic credentials and a `127.0.0.1` fixture base URL, clears alternative provider/OAuth and inherited Review Companion/MenShen configuration, and disables nonessential traffic and updates. It creates a temporary Git repository and record directory. No installer, global config write, GUI operation or real model inference is involved.

Observed native results:

- MCP server `plugin:review-companion:review-companion` reports `status: connected`, `source: plugin`.
- Claude announces 11 plugin tools: the 10 shared model tools plus `connect_session`; `read_fixed_evidence` is included and `open_editor` is absent.
- The controlled Anthropic-compatible SSE fixture asks for `connect_session` with the synthetic repository, then `start_review` against its synthetic `main` commit. Both successful MCP results return to the fixture on subsequent model requests.
- The binding reports `host: claude`, uses the temporary record directory, and the review result contains a review ID.
- Three synthetic inference requests complete with the final text `Synthetic MCP roundtrip complete.`

The test requires permission to bind a local socket. The initial sandbox run failed at `127.0.0.1` socket binding; the scoped rerun passed. `--bare` was evaluated first, but it does not automatically load plugin MCP servers in this host version; the delivered test uses normal plugin auto loading. The fixture parser accepts Claude's system reminders appended after the JSON tool result.

This validates native plugin discovery, MCP connection and successful tool/result transport. It does not claim real-provider inference compatibility or user-owned GUI acceptance.

Final ZIP also passed normal --plugin-dir loading: MCP connected, 11 tools, two tool-result roundtrips, three local fixture requests. No external inference.
