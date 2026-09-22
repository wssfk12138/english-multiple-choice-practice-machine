import hashlib
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from backend.app.services import question_bank_catalog as service
from backend.app.services.official_download_sources import OFFICIAL_RELEASE, official_download_sources

class Response(BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url
    def geturl(self):
        return self.url

class PublicProxyFallbackTests(unittest.TestCase):
    def test_scope_is_exact_and_opt_in(self):
        url = OFFICIAL_RELEASE + 'bank.esq'
        self.assertEqual(len(official_download_sources(url, True)), 3)
        self.assertEqual(official_download_sources(url, False), [url])
        for value in [url + '?secret=1', url + '/other', OFFICIAL_RELEASE + '../bank.esq', 'https://example.com/bank.esq', url.replace('github.com/', 'github.com.evil/')]:
            self.assertEqual(official_download_sources(value, True), [value])

    def test_network_and_bad_hash_fallback_preserve_expected_bytes(self):
        url = OFFICIAL_RELEASE + 'bank.esq'
        data = b'valid package'
        package = dict(downloadUrl=url, fileName='bank.esq', size=len(data), sha256=hashlib.sha256(data).hexdigest())
        calls = []
        def open_remote(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise urllib.error.URLError('injected official outage')
            return Response(b'wrong package' if len(calls) == 2 else data, request.full_url)
        with TemporaryDirectory() as directory, patch.object(service, '_open_remote', side_effect=open_remote), patch.object(service, 'validate_remote_url', side_effect=lambda url, **kwargs: url):
            target = service.download_package(package, Path(directory), public_proxy_fallback=True)
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(list(Path(directory).iterdir()), [target])
        self.assertEqual(calls, official_download_sources(url, True))
        self.assertEqual(package['downloadUrl'], url)

    def test_all_bad_hashes_fail_without_residue(self):
        url = OFFICIAL_RELEASE + 'bank.esq'
        package = dict(downloadUrl=url, fileName='bank.esq', size=3, sha256='0' * 64)
        with TemporaryDirectory() as directory, patch.object(service, '_open_remote', side_effect=lambda request, timeout: Response(b'bad', request.full_url)), patch.object(service, 'validate_remote_url', side_effect=lambda url, **kwargs: url):
            with self.assertRaises(ValueError):
                service.download_package(package, Path(directory), public_proxy_fallback=True)
            self.assertEqual(list(Path(directory).iterdir()), [])

