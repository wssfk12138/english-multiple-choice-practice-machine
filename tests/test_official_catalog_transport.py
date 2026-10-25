import unittest
import urllib.request
from unittest.mock import Mock, patch

from backend.app.services import question_bank_catalog as service
from backend.app.services.official_download_sources import OFFICIAL_CATALOG, is_official_download_url


class OfficialCatalogTransportTests(unittest.TestCase):
    def test_slow_official_source_falls_back_without_leaving_partial_file(self):
        import hashlib
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from tests.test_question_bank_catalog import Response
        from backend.app.services.official_download_sources import OFFICIAL_BANK_RELEASE, PROXY_ORIGINS
        content = b'verified synthetic asset'
        url = OFFICIAL_BANK_RELEASE + 'test.esq'
        package = {'downloadUrl':url,'size':len(content),'sha256':hashlib.sha256(content).hexdigest()}
        with TemporaryDirectory() as directory, patch.object(service,'_open_remote',side_effect=[Response(content,url),Response(content,PROXY_ORIGINS[0]+url)]) as opener, patch.object(service.time,'monotonic',side_effect=[0,31,32,32.1]):
            target = service.download_package(package,Path(directory),public_proxy_fallback=True)
            self.assertEqual(target.read_bytes(),content)
            self.assertEqual(opener.call_count,2)
            self.assertEqual(list(Path(directory).glob('*.part')),[])

    def test_official_catalog_uses_scoped_https_transport_under_fake_ip_dns(self):
        opener = Mock()
        with patch.object(service, '_assert_public_host', side_effect=service.QuestionBankCatalogError('fake-IP DNS')), patch.object(service.urllib.request, 'build_opener', return_value=opener):
            service._open_remote(urllib.request.Request(OFFICIAL_CATALOG), 30)
        opener.open.assert_called_once()
        self.assertEqual(opener.open.call_args.args[0].full_url, OFFICIAL_CATALOG)

    def test_custom_catalog_still_requires_public_dns(self):
        with patch.object(service, '_assert_public_host', side_effect=service.QuestionBankCatalogError('private DNS')), patch.object(service.urllib.request, 'build_opener') as opener:
            with self.assertRaises(service.QuestionBankCatalogError):
                service._open_remote(urllib.request.Request('https://example.com/catalog.json'), 30)
        opener.assert_not_called()
        self.assertFalse(is_official_download_url(OFFICIAL_CATALOG + '?other=1'))
        self.assertFalse(is_official_download_url(OFFICIAL_CATALOG.replace('github.com/', 'github.com.evil/')))

    def test_official_response_and_redirect_cannot_escape_github(self):
        signed = 'https://release-assets.githubusercontent.com/asset/file?signature=synthetic'
        service._validate_response_url(OFFICIAL_CATALOG, signed)
        handler = service._OfficialGitHubRedirectHandler()
        for value in ['https://example.com/catalog.json', 'http://127.0.0.1/private', 'https://github.com/other/repo/releases/download/v1/file.esq']:
            with self.subTest(value=value), self.assertRaises(service.QuestionBankCatalogError):
                handler.redirect_request(urllib.request.Request(OFFICIAL_CATALOG), None, 302, 'Found', {}, value)

    def test_all_actual_bank_assets_and_exact_proxy_wrappers_use_scoped_transport(self):
        from backend.app.services.official_download_sources import OFFICIAL_BANK_RELEASE, PROXY_ORIGINS, official_download_sources
        names = ['postgraduate-english-one-2002-2010-2026-v1.1.3.esq',
                 'postgraduate-english-two-2010-2025-v1.1.3.esq',
                 'cet4-complete-v1.2.1.esq', 'cet6-complete-v1.2.1.esq']
        for name in names:
            url = OFFICIAL_BANK_RELEASE + name
            self.assertEqual(len(official_download_sources(url,True)),3)
            self.assertEqual(official_download_sources(url,False),[url])
            for source in [url, *(origin+url for origin in PROXY_ORIGINS)]:
                with self.subTest(source=source), patch.object(service,'_assert_public_host',side_effect=service.QuestionBankCatalogError('fake-IP DNS')), patch.object(service.urllib.request,'build_opener') as opener:
                    self.assertTrue(is_official_download_url(source))
                    service._open_remote(urllib.request.Request(source),30)
                    opener.return_value.open.assert_called_once()
                    service._validate_response_url(source,'https://release-assets.githubusercontent.com/file?signature=synthetic')

    def test_bank_scope_does_not_admit_private_or_lookalike_targets(self):
        from backend.app.services.official_download_sources import OFFICIAL_BANK_RELEASE, PROXY_ORIGINS
        invalid = [OFFICIAL_BANK_RELEASE+'../evil.esq',OFFICIAL_BANK_RELEASE+'%2e%2e/evil.esq',
                   OFFICIAL_BANK_RELEASE+'x.esq?target=evil',OFFICIAL_BANK_RELEASE+'x.esq#fragment',
                   OFFICIAL_BANK_RELEASE.replace('github.com','github.com.evil')+'x.esq',
                   OFFICIAL_BANK_RELEASE.replace('question-banks-2026-09-20','other-release')+'x.esq',
                   'https://ghfast.top/https://127.0.0.1/private',
                   'https://gh-proxy.com/https://github.com/other/repo/releases/download/x/x.esq']
        for url in invalid:
            self.assertFalse(is_official_download_url(url),url)
            with self.subTest(url=url), self.assertRaises(service.QuestionBankCatalogError):
                service._validate_official_transfer(url,redirect=True)
