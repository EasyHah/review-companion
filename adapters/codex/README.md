# Codex adapter

Install the whole source package through a local Codex marketplace. This adapter is not a standalone plugin. Python 3.9+ and Git must be on PATH; no model credentials belong in this package.

Root `plugin.json` and `mcp.json` are the portable format. `.codex-plugin/plugin.json` supports Codex 0.145.0, which currently reads the compatibility manifest rather than the portable root manifest. It explicitly references `./adapters/codex/mcp.json` so it cannot accidentally consume Claude's root `.mcp.json`.

Codex forwards optional `REVIEW_COMPANION_DATA_DIR`, `REVIEW_COMPANION_CONFIG`, `REVIEW_COMPANION_GIT`, and `REVIEW_COMPANION_EDITOR` through an explicit `env_vars` allowlist. Set these in the environment that starts Codex, or use the shared user configuration file. The portable root format keeps standard transport fields; this environment forwarding is in the verified Codex compatibility configuration.

The MCP command starts in the installed plugin root (`cwd: "."`). Before using review tools, the shared skill calls `connect_session` with the actual repository path and an optional host session ID. Repository selection must never be inferred from the MCP process's working directory.

Codex uses a local browser panel. Follow-up prompts are copied and sent manually by the user. The model remains in Codex. Browser launch requires an explicit user request; `open_review` only returns a loopback URL. Editor launch is a browser action and is not exposed as a model MCP tool. No Codex lifecycle hook is required.

Run `python3 tests/native-codex.py` from the package for an optional native CLI smoke test. It creates a temporary local marketplace, installs a copied package in an isolated `CODEX_HOME`, and uses an ephemeral protocol session and synthetic Git repository. It never requests a model turn, modifies real Codex settings, or opens a browser.
