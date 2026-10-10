"""Actual Claude Code + unified plugin MCP, using only synthetic loopback inference."""
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PACKAGE = Path(__file__).resolve().parents[1]
CLAUDE = Path(os.environ.get('REVIEW_TEST_CLAUDE') or shutil.which('claude') or '/missing-claude')
assert CLAUDE.is_file(), 'Set REVIEW_TEST_CLAUDE to an installed absolute Claude executable'

with tempfile.TemporaryDirectory(prefix='native-claude-') as temporary:
    folder = Path(temporary)
    repo = folder / 'repository'; repo.mkdir()
    profile = folder / 'profile'; profile.mkdir()
    records = folder / 'records'
    subprocess.run(['git', '-C', str(repo), 'init', '-b', 'main'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '--allow-empty', '-m', 'Synthetic fixture'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'checkout', '-b', 'feature'], check=True, capture_output=True)
    (repo / 'sample.txt').write_text('Synthetic changed file\n')
    subprocess.run(['git', '-C', str(repo), 'add', 'sample.txt'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', 'commit', '-m', 'Synthetic change'], check=True, capture_output=True)
    observed = {'tools': set(), 'results': [], 'paths': [], 'requests': 0, 'errors': []}

    class Fixture(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            endpoint = self.path.split('?')[0]
            observed['paths'].append(endpoint)
            if endpoint.endswith('/count_tokens'):
                data = json.dumps({'input_tokens': 100}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
                return
            if not endpoint.endswith('/messages'):
                observed['errors'].append('unexpected local endpoint ' + endpoint)
                self.send_error(400); return
            observed['requests'] += 1
            names = [tool['name'] for tool in body.get('tools', [])]
            observed['tools'].update(names)
            results = [block for message in body.get('messages', []) if isinstance(message.get('content'), list) for block in message['content'] if block.get('type') == 'tool_result']
            observed['results'] = results
            completed = len(results)
            if completed < 3:
                suffix = ('connect_session','start_review','set_file_risks')[completed]
                candidates = [name for name in names if name.endswith('__' + suffix)]
                if len(candidates) != 1:
                    observed['errors'].append('missing or ambiguous MCP tool ' + suffix)
                    self.send_error(400); return
                arguments = {'repository_path':str(repo)} if completed == 0 else {'repository_path':str(repo),'main_ref':'main','target_ref':'HEAD'}
                if completed == 2:
                    result_content = results[1].get('content', [])
                    result_text = result_content if isinstance(result_content,str) else next(b['text'] for b in result_content if b.get('type')=='text')
                    review = json.JSONDecoder().raw_decode(result_text.strip())[0]
                    arguments = {'review_id':review['review_id'],'expected_version':review['version'],'updates':[{'file_id':review['first_files']['items'][0]['id'],'notes':['合成风险线索，待核实']} ]}
                content = [{'type':'tool_use','id':'synthetic_'+suffix,'name':candidates[0],'input':arguments}]
                stop = 'tool_use'
            else:
                content = [{'type': 'text', 'text': 'Synthetic MCP roundtrip complete.'}]
                stop = 'end_turn'
            message = {'id': 'msg_synthetic_' + str(observed['requests']), 'type': 'message', 'role': 'assistant', 'model': body['model'], 'content': content, 'stop_reason': stop, 'stop_sequence': None, 'usage': {'input_tokens': 100, 'output_tokens': 1}}
            if not body.get('stream'):
                data = json.dumps(message).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
            events = [('message_start', {'type': 'message_start', 'message': {**message, 'content': [], 'stop_reason': None, 'usage': {'input_tokens': 100, 'output_tokens': 0}}})]
            for index, block in enumerate(content):
                start = {**block, **({'input': {}} if block['type'] == 'tool_use' else {'text': ''})}
                events.append(('content_block_start', {'type': 'content_block_start', 'index': index, 'content_block': start}))
                delta = {'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])} if block['type'] == 'tool_use' else {'type': 'text_delta', 'text': block['text']}
                events.append(('content_block_delta', {'type': 'content_block_delta', 'index': index, 'delta': delta}))
                events.append(('content_block_stop', {'type': 'content_block_stop', 'index': index}))
            events += [('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None}, 'usage': {'output_tokens': 1}}), ('message_stop', {'type': 'message_stop'})]
            data = ''.join('event: ' + event + '\ndata: ' + json.dumps(payload) + '\n\n' for event, payload in events).encode()
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    env = {key: value for key, value in os.environ.items() if not key.startswith(('ANTHROPIC_', 'CLAUDE_CODE_USE_', 'CLAUDE_CODE_OAUTH_', 'REVIEW_COMPANION_', 'REVIEW_CLAUDE_', 'MENSHEN_', 'CLAUDE_PLUGIN_ROOT'))}
    env.update(CLAUDE_CONFIG_DIR=str(profile), ANTHROPIC_BASE_URL='http://127.0.0.1:' + str(server.server_port), ANTHROPIC_API_KEY='synthetic-local-only', ANTHROPIC_AUTH_TOKEN='synthetic-local-only', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', DISABLE_AUTOUPDATER='1', ENABLE_TOOL_SEARCH='false', REVIEW_COMPANION_DATA_DIR=str(records), PYTHONDONTWRITEBYTECODE='1')
    argv = [str(CLAUDE), '-p', '--verbose', '--output-format', 'stream-json', '--no-session-persistence', '--setting-sources', '', '--model', 'claude-sonnet-4-6', '--plugin-dir', str(PACKAGE), '--tools', '', '--allowedTools', 'mcp__plugin_review-companion_review-companion__*', '--system-prompt', 'Synthetic compatibility test. Use only provided review companion tools. No browser, editor or external access.', 'Connect this synthetic repository, start its review against main, save a synthetic file risk, then stop.']
    try:
        result = subprocess.run(argv, cwd=repo, env=env, text=True, capture_output=True, timeout=45)
        events = []
        for line in result.stdout.splitlines():
            try: events.append(json.loads(line))
            except ValueError: pass
        init = next((event for event in events if event.get('type') == 'system' and event.get('subtype') == 'init'), {})
        final = next((event for event in reversed(events) if event.get('type') == 'result'), {})
        assert result.returncode == 0, {'exit': result.returncode, 'error': result.stderr[:1000], 'result': final, 'fixture_errors': observed['errors'], 'announced_tools': sorted(observed['tools']), 'mcp': init.get('mcp_servers'), 'plugins': init.get('plugins')}
        assert any(item.get('status') == 'connected' and (item.get('source') == 'plugin' or item.get('name', '').startswith('plugin:review-companion:')) and 'review-companion' in item.get('name', '') for item in init.get('mcp_servers', [])), init.get('mcp_servers')
        assert not observed['errors'], observed['errors']
        plugin_tools = [name for name in observed['tools'] if name.startswith('mcp__plugin_review-companion_review-companion__')]
        assert len(plugin_tools) == 12, plugin_tools
        assert any(name.endswith('__read_fixed_evidence') for name in plugin_tools), plugin_tools
        assert not any(name.endswith('__open_editor') for name in plugin_tools), plugin_tools
        assert len(observed['results']) == 3, {'requests': observed['requests'], 'results': observed['results'], 'final': final}
        assert all(not item.get('is_error') for item in observed['results']), observed['results']
        def result_json(block):
            content = block.get('content', [])
            if isinstance(content, str): return json.JSONDecoder().raw_decode(content.strip())[0]
            return json.JSONDecoder().raw_decode(next(item['text'] for item in content if item.get('type') == 'text').strip())[0]
        connected, restored, flagged = map(result_json, observed['results'])
        assert connected.get('session_bound') and connected.get('host') == 'claude', connected
        assert connected['data_directory'] == str(records.resolve()), connected
        assert restored.get('review_id'), restored
        assert flagged['features'][0]['risk_count']==1 and flagged['progress']==restored['progress']
        assert final.get('result') == 'Synthetic MCP roundtrip complete.' and not final.get('is_error'), final
        print(json.dumps({'status': 'PASS', 'claude': subprocess.check_output([str(CLAUDE), '--version'], text=True).strip(), 'mcp': init.get('mcp_servers'), 'announced_review_tools': len(plugin_tools), 'tool_result_roundtrips': len(observed['results']), 'fixture_requests': observed['requests'], 'inference': 'synthetic loopback only'}, ensure_ascii=False))
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=2)
