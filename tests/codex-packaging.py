#!/usr/bin/env python3
"""Codex host manifest contracts, independent of optional installed native CLI."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
portable = json.loads((root / 'plugin.json').read_text())
legacy = json.loads((root / '.codex-plugin/plugin.json').read_text())
assert portable['$schema'].endswith('/plugin.schema.json')
assert portable['name'] == legacy['name'] == 'review-companion'
assert portable['version'] == legacy['version']
assert legacy['skills'] == './skills/'
assert legacy['mcpServers'] == './adapters/codex/mcp.json'
assert portable['extensions']['com.openai']['interface'] == legacy['interface']
canonical = json.loads((root / 'mcp.json').read_text())
compatibility = json.loads((root / 'adapters/codex/mcp.json').read_text())
a = canonical['mcpServers']['review_companion']
b = compatibility['mcpServers']['review_companion']
assert a['type'] == 'stdio'
assert {k: v for k, v in a.items() if k != 'type'} == {k: v for k, v in b.items() if k != 'env_vars'}
assert {k: v for k, v in b.items() if k != 'env_vars'} == {'command': 'python3', 'args': ['./backend/host_mcp.py', '--host', 'codex'], 'cwd': '.'}
assert b['env_vars'] == ['REVIEW_COMPANION_DATA_DIR', 'REVIEW_COMPANION_CONFIG', 'REVIEW_COMPANION_GIT', 'REVIEW_COMPANION_EDITOR']
assert 'CLAUDE_PLUGIN_ROOT' not in json.dumps(compatibility)
assert (root / 'backend/host_mcp.py').is_file()
assert (root / 'skills/review-companion/SKILL.md').is_file()
print('Codex packaging contracts passed')
