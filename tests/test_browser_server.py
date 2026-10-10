import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from browser_server import Bridge, create_server, embed_origin
from companion_store import StoreError
from test_companion import snapshot


class BrowserBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bridge = Bridge(self.root / 'data', self.root)
        self.session = self.bridge.switch('session-a')['session']

    def tearDown(self):
        self.temp.cleanup()

    def test_draft_is_claimed_once_and_switch_invalidates_it(self):
        draft = self.bridge.route('/api/draft', {'session': self.session, 'text': 'explain'})
        self.assertEqual(self.bridge.route('/agent/poll', {'session': self.session}, True)['draft']['id'], draft['id'])
        self.assertIsNone(self.bridge.route('/agent/poll', {'session': self.session}, True)['draft'])
        self.bridge.switch('session-b')
        with self.assertRaises(StoreError):
            self.bridge.route('/agent/ack', {'session': self.session, 'id': draft['id'], 'status': 'filled'}, True)
        self.assertIsNone(self.bridge.draft)

    def test_expired_draft_cannot_be_delivered(self):
        draft = self.bridge.route('/api/draft', {'session': self.session, 'text': 'explain'})
        self.bridge.draft['deadline'] = 0
        self.assertIsNone(self.bridge.route('/agent/poll', {'session': self.session}, True)['draft'])
        self.assertEqual(self.bridge.route('/api/draft-status', {'session': self.session, 'id': draft['id']})['status'], 'expired')

    def test_browser_cannot_create_review_or_write_conclusions(self):
        for name, args in [('start_review', {'repository_path': str(self.root)}), ('set_file_review', {'conclusion': 'overwrite'}), ('set_file_conclusion', {})]:
            with self.assertRaises(StoreError): self.bridge.dispatch(name, args, browser=True)

    def test_pi_web_is_copy_only_and_rejects_draft_delivery(self):
        bridge = Bridge(self.root / 'web-data', self.root, 'pi_web')
        session = bridge.switch('web-session')
        self.assertEqual(session['input_mode'], 'copy')
        for route in ('/api/draft', '/api/draft-status', '/agent/poll', '/agent/ack'):
            with self.assertRaises(StoreError):
                bridge.route(route, {'session': session['session'], 'text': 'question'}, agent=route.startswith('/agent'))

    def test_embed_origin_is_exact_loopback_and_opt_in(self):
        for origin in ('http://127.0.0.1:30141', 'http://localhost:30141', 'http://[::1]:30141'):
            self.assertEqual(embed_origin(origin), origin)
        for origin in ('*', 'https://example.com', 'http://0.0.0.0:30141', 'http://127.0.0.1:30141/', 'http://127.0.0.1:30141?x=1', 'http://user@127.0.0.1:30141', "http://127.0.0.1:30141; frame-src *", 'http://127.0.0.1:30141\n'):
            with self.subTest(origin=origin), self.assertRaises(StoreError): embed_origin(origin)
        self.assertIsNone(Bridge(self.root/'other', self.root, 'pi', 'http://127.0.0.1:30141').frame_origin)

    def test_embedded_page_preserves_auth_and_limits_frame_ancestors(self):
        origin = 'http://127.0.0.1:30141'
        bridge = Bridge(self.root/'web', self.root, 'pi_web', origin)
        server = create_server(bridge)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            conn.request('GET', '/')
            response = conn.getresponse(); response.read()
            self.assertIn('frame-ancestors '+origin+';', response.getheader('Content-Security-Policy'))
            self.assertNotIn('*', response.getheader('Content-Security-Policy'))
            conn.request('GET', '/api/session')
            response = conn.getresponse(); response.read(); self.assertEqual(response.status, 401)
            bridge.frame_origin = None
            conn.request('GET', '/')
            response = conn.getresponse(); response.read()
            self.assertIn("frame-ancestors 'none'", response.getheader('Content-Security-Policy'))
            conn.close()
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_other_repository_records_cannot_be_opened(self):
        record = self.bridge.service.store.create(snapshot(self.root / 'other'))
        with self.assertRaises(StoreError) as error:
            self.bridge.dispatch('read_review', {'review_id': record['review_id']})
        self.assertEqual(error.exception.code, 'wrong_repository')

    def test_http_auth_origin_and_host_boundaries(self):
        server = create_server(self.bridge)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def request(path, token=None, origin=None, host=None, body=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            headers = {'Host': host or server.address, 'Content-Type': 'application/json'}
            if token: headers['Authorization'] = 'Bearer ' + token
            if origin: headers['Origin'] = origin
            connection.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), headers)
            response = connection.getresponse(); data = response.read(); status = response.status; connection.close()
            return status, data
        try:
            self.assertEqual(request('/api/session')[0], 401)
            self.assertEqual(request('/agent/specs', self.bridge.browser_token)[0], 401)
            self.assertEqual(request('/api/session', self.bridge.browser_token, host='evil.invalid')[0], 403)
            self.assertEqual(request('/api/tool', self.bridge.browser_token, origin='https://evil.invalid', body={})[0], 403)
            self.assertEqual(request('/api/session', self.bridge.browser_token)[0], 200)
            status, data = request('/api/tool', self.bridge.browser_token, body={'session': self.session, 'name': 'read_review', 'arguments': {}})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)['structuredContent']['error']['code'], 'no_review')
            status, page = request('/')
            self.assertEqual(status, 200)
            self.assertNotIn(self.bridge.agent_token.encode(), page)
            self.assertNotIn(self.bridge.browser_token.encode(), page)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__': unittest.main()
