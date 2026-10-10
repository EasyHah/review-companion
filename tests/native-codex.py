#!/usr/bin/env python3
"""Isolated native Codex install + actual MCP smoke test; no model turn or credentials."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import urllib.request

PACKAGE = Path(__file__).resolve().parents[1]


def main():
    if not shutil.which('codex'):
        raise SystemExit('Codex CLI is required for this optional native smoke test')
    with tempfile.TemporaryDirectory(prefix='review-companion-codex-') as tmp:
        root = Path(tmp)
        home, market, repository = root / 'home', root / 'market', root / 'repository'
        home.mkdir()
        repository.mkdir()
        subprocess.run(['git', 'init', '-q', str(repository)], check=True)
        subprocess.run(['git', '-C', str(repository), 'config', 'user.name', 'Synthetic Test'], check=True)
        subprocess.run(['git', '-C', str(repository), 'config', 'user.email', 'synthetic@example.invalid'], check=True)
        (repository / 'example.txt').write_text('one\n')
        (repository / 'helper.py').write_text('def helper():\n    return True\n')
        subprocess.run(['git', '-C', str(repository), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repository), 'commit', '-qm', 'synthetic initial'], check=True)
        (repository / 'example.txt').write_text('two\n')
        subprocess.run(['git', '-C', str(repository), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repository), 'commit', '-qm', 'synthetic change'], check=True)
        package = market / 'plugins' / 'review-companion'
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns('__pycache__'))
        catalog = market / '.agents' / 'plugins' / 'marketplace.json'
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps({'name': 'rc-native-test', 'plugins': [{
            'name': 'review-companion', 'source': {'source': 'local', 'path': './plugins/review-companion'},
            'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}, 'category': 'Productivity'}]}))
        # A closed local endpoint prevents app-server's eager websocket warmup from
        # contacting an inference service. No turn/start request is ever sent.
        (home / 'config.toml').write_text('''model_provider = "native_test"
[model_providers.native_test]
name = "Isolated native test"
base_url = "http://127.0.0.1:9/v1"
wire_api = "responses"
requires_openai_auth = false
''')
        env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ('API_KEY', 'ACCESS_TOKEN', 'AUTH_TOKEN'))}
        config = root / 'review-config.json'
        config.write_text('{}\n')
        env.update(CODEX_HOME=str(home), REVIEW_COMPANION_DATA_DIR=str(root / 'data'), REVIEW_COMPANION_CONFIG=str(config))
        installs = []
        for args in (['plugin', 'marketplace', 'add', str(market), '--json'], ['plugin', 'add', 'review-companion@rc-native-test', '--json']):
            result = subprocess.run(['codex'] + args, env=env, cwd=root, capture_output=True, text=True, check=True)
            installs.append(json.loads(result.stdout))
        output = queue.Queue()
        with (root / 'stderr.log').open('w') as errors:
            server = subprocess.Popen(['codex', 'app-server', '--stdio'], cwd=root, env=env,
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True, bufsize=1)
            def read():
                for line in server.stdout:
                    output.put(json.loads(line))
            threading.Thread(target=read, daemon=True).start()
            counter = 0
            def call(method, params):
                nonlocal counter
                counter += 1
                server.stdin.write(json.dumps({'id': counter, 'method': method, 'params': params}) + '\n')
                server.stdin.flush()
                while True:
                    message = output.get(timeout=30)
                    if message.get('id') == counter:
                        assert 'error' not in message, message
                        return message['result']
            try:
                init = call('initialize', {'clientInfo': {'name': 'review_companion_native_test', 'version': '1'}, 'capabilities': {'experimentalApi': True}})
                server.stdin.write('{"method":"initialized"}\n')
                server.stdin.flush()
                plugin = call('plugin/read', {'pluginName': 'review-companion', 'marketplacePath': str(catalog)})['plugin']
                assert plugin['mcpServers'] == ['review_companion'], plugin
                assert plugin['skills'], 'Native Codex did not load the shared skill'
                thread = call('thread/start', {'cwd': str(repository), 'ephemeral': True, 'approvalPolicy': 'never', 'sandbox': 'workspace-write'})['thread']['id']
                status = call('mcpServerStatus/list', {'threadId': thread})
                inventory = next(s for s in status['data'] if s['name'] == 'review_companion')
                tools = inventory['tools']
                assert 'connect_session' in tools and 'open_review' in tools, tools
                assert 'open_editor' not in tools, tools
                def invoke(tool, arguments):
                    result = call('mcpServer/tool/call', {'threadId': thread, 'server': 'review_companion', 'tool': tool, 'arguments': arguments})
                    assert not result.get('isError'), result
                    return result
                connected = invoke('connect_session', {'repository_path': str(repository), 'session_id': 'synthetic-codex'})
                assert Path(connected['structuredContent']['data_directory']) == (root / 'data').resolve(), connected
                review = invoke('start_review', {'repository_path': str(repository), 'main_ref': 'HEAD~1', 'target_ref': 'HEAD'})
                data=review['structuredContent'];rid=data['review_id'];fid=data['next_file']['id']
                assert 'set_file_risks' in tools and not {'set_risk_overview','set_behavior_path','read_context_evidence'} & set(tools)
                assert len(plugin['skills']) == 3
                saved=invoke('set_file_risks',{'review_id':rid,'expected_version':data['version'],'updates':[{'file_id':fid,'notes':['合成风险线索：待核实']} ]})['structuredContent']
                assert saved['progress']==data['progress']
                items=invoke('list_review_files',{'review_id':rid})['structuredContent']['items']
                assert items[0]['risk_notes']==['合成风险线索：待核实']
                opened = invoke('open_review', {})['structuredContent']
                assert opened['browser_url'].startswith('http://127.0.0.1:'), opened
                assert opened['input_mode'] == 'copy', opened
                with urllib.request.urlopen(opened['browser_url'].split('#')[0], timeout=5) as response:
                    assert response.status == 200
                assert (root / 'data' / 'reviews.json').is_file()
                print(json.dumps({'codex': init['userAgent'], 'install': installs[-1], 'skillCount': len(plugin['skills']), 'tools': sorted(tools), 'connectSession': connected, 'browserHttpStatus': 200, 'inputMode': opened['input_mode']}, indent=2))
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == '__main__':
    main()
