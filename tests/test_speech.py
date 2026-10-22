import io
import os
import tempfile
import unittest
import wave
from pathlib import Path

from backend.app.services.speech import SpeechError, SpeechService


MODELS = Path(os.environ.get('ENGLISH_PRACTICE_TTS_DIR', Path(__file__).resolve().parents[1] / 'tts'))


class SpeechServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (MODELS / 'en_US' / 'model.onnx').is_file():
            raise unittest.SkipTest('TTS models are not available')
        cls.service = SpeechService(MODELS)

    def test_status_reports_us(self):
        status = self.service.status()
        self.assertEqual(status['engine'], 'sherpa-onnx')
        self.assertIn('us', status['accents'])
        self.assertTrue(status['available'])

    def test_synthesize_us_returns_wav(self):
        result = self.service.synthesize('hello', 'us')
        self.assertTrue(result['audio'].startswith(b'RIFF'))
        self.assertGreater(result['samples'], 0)
        with wave.open(io.BytesIO(result['audio']), 'rb') as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getsampwidth(), 2)
            self.assertEqual(handle.getframerate(), result['sample_rate'])
            self.assertGreater(handle.getnframes(), 0)

    @unittest.skipUnless((MODELS / 'en_GB' / 'model.onnx').is_file(), 'English GB model is not available')
    def test_synthesize_gb_returns_wav(self):
        result = self.service.synthesize('hello', 'gb')
        self.assertTrue(result['audio'].startswith(b'RIFF'))
        self.assertEqual(result['accent'], 'gb')

    def test_rejects_ssml(self):
        with self.assertRaises(SpeechError) as raised:
            self.service.synthesize('<speak>hello</speak>', 'us')
        self.assertEqual(raised.exception.code, 'speech_text_invalid')

    def test_rejects_too_many_words(self):
        with self.assertRaises(SpeechError) as raised:
            self.service.synthesize('one two three four five six', 'us')
        self.assertEqual(raised.exception.code, 'speech_text_too_long')


class SpeechAvailabilityTests(unittest.TestCase):
    def test_empty_root_reports_no_accents(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SpeechService(Path(directory))
            self.assertEqual(service.status()['accents'], [])
            self.assertFalse(service.status()['available'])

    def test_missing_accent_is_controlled(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SpeechService(Path(directory))
            with self.assertRaises(SpeechError) as raised:
                service.synthesize('hello', 'gb')
            self.assertEqual(raised.exception.status_code, 503)
            self.assertEqual(raised.exception.code, 'speech_accent_unavailable')


if __name__ == '__main__':
    unittest.main()
