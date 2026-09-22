import asyncio
import unittest

from backend.app.lan_sync_host import RequestBodyLimitMiddleware


class BodyReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_body_once_then_forward_real_disconnect(self):
        incoming = asyncio.Queue()
        await incoming.put({'type': 'http.request', 'body': b'payload', 'more_body': False})
        calls = 0

        async def receive():
            nonlocal calls
            calls += 1
            return await incoming.get()

        async def app(scope, replay, send):
            self.assertEqual((await replay())['body'], b'payload')
            disconnected = asyncio.create_task(replay())
            await asyncio.sleep(0)
            self.assertFalse(disconnected.done(), 'Receive must wait, not spin on empty requests')
            await incoming.put({'type': 'http.disconnect'})
            self.assertEqual(await disconnected, {'type': 'http.disconnect'})

        async def send(message):
            pass

        await RequestBodyLimitMiddleware(app, 1024)({'type': 'http'}, receive, send)
        self.assertEqual(calls, 2)
