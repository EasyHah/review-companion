# Pi adapter validation

The unified package keeps Pi integration under `adapters/pi/`, with the native CLI in `bin/review-pi.mjs`. The adapter reads shared tool schemas and starts the shared browser service with `--host pi`. Python owns default repository record resolution. An explicit `REVIEW_COMPANION_DATA_DIR` or existing Pi `review-companion.json` `data_dir` remains an exact override.

The CLI uses the current Pi profile by default. Optional `--isolated` selects a separate profile without disabling that profile's extensions, skills or themes. The adapter never selects a model, restricts active tools, submits composer content or scans unrelated provider credentials. MenShen routing remains an optional `/model` choice; authentication references remain in the machine's Pi profile.

## Automated evidence

- `node --test tests/pi*.test.mjs`: 17 tests passed (the real bridge test rerun with local socket permission). Includes auth precedence/privacy, manual router behavior, image preservation, explicit directory compatibility, package root and child environment forwarding.
- `node tests/native-pi-limits.mjs`: passed with installed Pi 1.1.0. DeepSeek uses Pi's `opencode` catalog limits of 1,000,000 context / 384,000 output; GLM uses `zai` limits of 1,000,000 / 131,072. Actual compaction budgets are 8,192 and 13,107, preserving the earlier fix for the 4K cap.
- `review-pi doctor` prints dependency status and effective physical/virtual model limits without installing anything or making model calls.

- `node --test tests/pi-bridge.test.mjs`: passed against the shared backend. Ten model tool specs; stale session rejection; parent-pipe shutdown; idle empty TUI composer-only draft fill.
- `python3 tests/native-pi-session.py`: passed using installed Pi 1.1.0. Existing model, extension and settings bytes preserved; `/new` keeps the browser URL and rejects stale drafts.
- `python3 tests/native-pi-reload.py`: passed using a temporary package copy. Changes to imported `.ts` limits reload into physical, virtual and current models; browser bridge survives with a fresh session binding.

The loopback-backed tests initially hit sandbox `PermissionError` on `127.0.0.1` binding. They passed after narrowly scoped execution permission; no adapter/core defect remained. Native tests use temporary profiles, synthetic credentials and records, and make no model network calls. Native UI acceptance is user owned.
