import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.app import config
from backend.app.routers import speech as router
from backend.app.services import speech


class SpeechPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for directory in ('en_US', 'en_GB'):
            target = self.root / directory
            target.mkdir()
            (target / 'model.onnx').write_bytes(b'synthetic model')
            (target / 'tokens.txt').write_text('synthetic tokens', encoding='utf-8')
            (target / 'espeak-ng-data').mkdir()
        self.service = speech.SpeechService(self.root)
        self.calls = []
        self.engine = SimpleNamespace(generate=lambda text, **kwargs: (self.calls.append(text) or SimpleNamespace(samples=[0.1] * 100, sample_rate=22050)))
        self.loader = patch.object(self.service, '_load', return_value=self.engine)
        self.load_mock = self.loader.start()

    def tearDown(self):
        self.loader.stop()
        self.temporary.cleanup()

    def test_public_cannot_enable_alan_even_if_directory_is_present(self):
        with patch.object(speech.importlib.util, 'find_spec', return_value=object()):
            accents = self.service.status()['accents']
        if getattr(config, 'APP_VARIANT', '') == 'public':
            self.assertEqual(accents, ['us'])
            with self.assertRaises(speech.SpeechError) as caught:
                self.service.synthesize('hello', 'gb')
            self.assertEqual(caught.exception.status_code, 503)
        else:
            self.assertEqual(accents, ['us', 'gb'])

    def test_missing_engine_is_unavailable(self):
        with patch.object(speech.importlib.util, 'find_spec', return_value=None):
            self.assertFalse(self.service.status()['available'])

    def test_cache_is_bounded_and_reuses_identical_clip(self):
        with patch.object(speech, 'MAX_CACHE_BYTES', 300):
            first = self.service.synthesize('hello', 'us')
            self.assertEqual(first, self.service.synthesize('hello', 'us'))
            self.assertEqual(self.calls, ['hello'])
            self.service.synthesize('world', 'us')
            self.assertLessEqual(self.service._cache_bytes, 300)
            self.service.synthesize('hello', 'us')
            self.assertEqual(self.calls, ['hello', 'world', 'hello'])

    def test_model_update_invalidates_cached_audio(self):
        self.service.synthesize('hello', 'us')
        (self.root / 'en_US/model.onnx').write_bytes(b'new synthetic model')
        self.service.synthesize('hello', 'us')
        self.assertEqual(self.calls, ['hello', 'hello'])
        self.assertEqual(self.load_mock.call_count, 2)

    def test_obsolete_waiters_never_run_inference(self):
        entered, release = threading.Event(), threading.Event()
        def generate(text, **kwargs):
            self.calls.append(text)
            if text == 'first':
                entered.set()
                self.assertTrue(release.wait(3))
            return SimpleNamespace(samples=[0.1] * 100, sample_rate=22050)
        self.engine.generate = generate
        outcomes = {}
        def run(text):
            try:
                self.service.synthesize(text, 'us')
                outcomes[text] = 'ok'
            except speech.SpeechError as exc:
                outcomes[text] = exc.code
        threads = [threading.Thread(target=run, args=(text,)) for text in ('first', 'second', 'third')]
        threads[0].start()
        self.assertTrue(entered.wait(3))
        try:
            for expected, thread in enumerate(threads[1:], 2):
                thread.start()
                deadline = time.monotonic() + 3
                while self.service._latest < expected and time.monotonic() < deadline:
                    time.sleep(.001)
                self.assertEqual(self.service._latest, expected)
        finally:
            release.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(3)
        self.assertEqual(self.calls, ['first', 'third'])
        self.assertEqual(outcomes, {'first': 'speech_superseded', 'second': 'speech_superseded', 'third': 'ok'})

    def test_http_wav_and_invalid_input_use_real_route(self):
        app = FastAPI()
        app.include_router(router.router, prefix='/api')
        with patch.object(router, 'get_speech_service', return_value=self.service), TestClient(app) as client:
            response = client.post('/api/speech/synthesize', json={'text':'hello', 'accent':'us', 'request_id':'synthetic'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['content-type'], 'audio/wav')
            self.assertTrue(response.content.startswith(b'RIFF'))
            rejected = client.post('/api/speech/synthesize', json={'text':'<speak>hello</speak>'})
            self.assertEqual(rejected.status_code, 400)
            self.assertNotIn('synthetic model', rejected.text)


if __name__ == '__main__':
    unittest.main()
