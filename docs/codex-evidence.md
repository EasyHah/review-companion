# Codex packaging evidence

Test environment: macOS arm64, installed Codex CLI 0.145.0. All installs used temporary local marketplaces and isolated temporary `CODEX_HOME` directories. The source package and existing real installations were not modified by installation tests.

## Manifest decision

The [official package documentation](https://developers.openai.com/plugins/build/plugins) fetched on 2026-10-09 identifies root `plugin.json` plus `mcp.json` as the portable format and retains `.codex-plugin/plugin.json` compatibility. This package includes both. The compatibility manifest points directly to `./adapters/codex/mcp.json`; Claude's root `.mcp.json` is deliberately separate.

Actual 0.145.0 behavior with **portable files alone**: `codex plugin marketplace add <local-path> --json` and `codex plugin add review-companion@rc-test --json` succeeded, but app-server `plugin/read` returned no description, skill, MCP server, or display name, and `mcpServerStatus/list` returned an empty inventory. The installed runtime therefore requires the compatibility manifest for this package. Future portable runtime support is not established by this test.

With the compatibility manifest, native installation returned version `0.3.0`, `plugin/read` reported the expected description, display name, and `review_companion` MCP server. A minimal test-only MCP fixture successfully initialized, listed its probe tool, and answered a tool call. The probe returned the exact installed plugin cache root as its working directory, despite app-server and ephemeral thread starting in an unrelated temporary directory. This verifies `cwd: "."` resolves to the installed plugin root, and the relative Python entry is valid. The fixture was generated only in a temporary copied package.

## Actual shared-backend result

The final native run passed on Codex 0.145.0 using the actual `backend/host_mcp.py --host codex`: version `0.3.0` installed, one shared skill loaded, and 11 MCP tools were inventoried. `open_editor` was absent. `connect_session` bound the synthetic repository with host `codex` and `input_mode: copy`. `start_review` created the two-commit review; `open_review` returned a private `http://127.0.0.1` URL, and its page responded HTTP 200. The test verified `reviews.json` stayed under the exact temporary data override.

Codex filters the MCP subprocess environment. The compatibility MCP config therefore uses the supported `env_vars` allowlist for `REVIEW_COMPANION_DATA_DIR`, `REVIEW_COMPANION_CONFIG`, `REVIEW_COMPANION_GIT`, and `REVIEW_COMPANION_EDITOR`. An initial connect-only run without that allowlist returned the default data path; it created no records or directory. The final test asserts the temporary override before any mutation. The portable root format retains only standard transport fields; its behavior on future native portable-format runtimes remains untested.

The sandbox denied loopback socket bind in the first browser test; the same isolated test passed with approved sandbox escalation. This was an environment restriction, not an MCP/backend defect.

## Reproduction and limits

`python3 tests/codex-packaging.py` verifies portable/fallback identity, separate host configurations, shared entry paths, and metadata parity. `python3 tests/native-codex.py` tests the actual package using the installed CLI: local install, native skill and MCP inventory, hidden editor tool, repository connection, review creation, and loopback HTTP against synthetic Git data. Its model provider points to a closed loopback endpoint, and it sends no model turn. The native app-server may independently try to warm marketplace metadata; restricted network failures do not affect local MCP verification.

Desktop visible UI acceptance is not claimed. No CUA, user-owned desktop UI, real settings, real credentials, real repositories, browser launch, or composer injection is part of these tests. Follow-up prompts use browser copy/manual send. No hook is installed.
