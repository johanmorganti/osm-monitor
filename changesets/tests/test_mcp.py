import json

from django.test import SimpleTestCase

PING = {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}


class McpTests(SimpleTestCase):
    def post(self, payload):
        body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
        # secure=True: with HTTPS_ONLY on, a plain request is redirected.
        return self.client.post('/mcp', body, content_type='application/json', secure=True)

    def test_initialize_and_tools_list(self):
        reply = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}}).json()
        self.assertEqual(reply['result']['protocolVersion'], '2025-06-18')
        tools = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}).json()['result']['tools']
        self.assertIn('summary', [t['name'] for t in tools])
        self.assertTrue(all(t['inputSchema']['type'] == 'object' for t in tools))

    def test_unknown_protocol_version_gets_the_newest_supported(self):
        reply = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '1999-01-01'}}).json()
        self.assertEqual(reply['result']['protocolVersion'], '2025-11-25')

    def test_notification_gets_202(self):
        self.assertEqual(self.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}).status_code, 202)

    def test_parse_error_and_wrong_method(self):
        self.assertEqual(self.post(b'{nope').json()['error']['code'], -32700)
        self.assertEqual(self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'nope'}).json()['error']['code'], -32601)
        self.assertEqual(self.client.get('/mcp', secure=True).status_code, 405)

    def test_batches_of_one_to_ten(self):
        self.assertEqual(len(self.post([PING] * 10).json()), 10)
        for size in (0, 11, 2000):
            with self.subTest(size=size):
                self.assertEqual(self.post([PING] * size).json()['error']['code'], -32600)

    def test_malformed_messages_do_not_take_the_batch_down(self):
        replies = self.post([PING, {'jsonrpc': '2.0', 'id': 3, 'method': 5},
                             {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': [1]}, 'junk']).json()
        self.assertEqual([r.get('result') is not None for r in replies], [True, False, False, False])
        self.assertEqual([r['error']['code'] for r in replies[1:]], [-32600, -32602, -32600])

    def test_unknown_tool_and_unknown_argument(self):
        unknown = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'nope'}}).json()
        self.assertEqual(unknown['error']['code'], -32602)
        bad_arg = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                             'params': {'name': 'summary', 'arguments': {'bogus': 1}}}).json()
        self.assertTrue(bad_arg['result']['isError'])
        self.assertIn('unknown argument', bad_arg['result']['content'][0]['text'])
