from __future__ import annotations

import unittest
import urllib.request
from backend.app.services import updates

OFFICIAL = "https://github.com/wssfk12138/english-multiple-choice-practice-machine/releases/download/v0.1.1/app.exe"


class OfficialUpdatePolicyTests(unittest.TestCase):
    def test_only_this_project_can_supply_direct_update_urls(self):
        validate = updates._validate_update_url
        self.assertEqual(validate(OFFICIAL), OFFICIAL)
        for value in [
            "https://github.com/other/repo/releases/download/v1/app.exe",
            OFFICIAL.replace("practice-machine/releases", "practice-machine-evil/releases"),
            OFFICIAL.replace("github.com/", "github.com.evil/"),
            "https://github.com/wssfk12138/english-multiple-choice-practice-machine/releases/download/../../../../other/repo/app.exe",
            "https://github.com/wssfk12138/english-multiple-choice-practice-machine/releases/download/%2e%2e/other.exe",
            "https://release-assets.githubusercontent.com/arbitrary.exe",
            "https://github.com:invalid/wssfk12138/release.exe",
            OFFICIAL.replace("https:", "http:"),
        ]:
            with self.subTest(value=value), self.assertRaises(updates.UpdateError):
                validate(value)

    def test_signed_github_asset_redirect_is_allowed_but_other_hosts_are_blocked(self):
        handler = updates._RestrictedRedirectHandler()
        request = urllib.request.Request(OFFICIAL)
        signed = "https://release-assets.githubusercontent.com/github-production-release-asset/123/app.exe?signature=synthetic"
        redirected = handler.redirect_request(request, None, 302, "Found", {}, signed)
        self.assertEqual(redirected.full_url, signed)
        for value in ["https://example.com/app.exe", "http://127.0.0.1/app.exe", "https://github.com/other/repo/releases/download/v1/app.exe"]:
            with self.subTest(value=value), self.assertRaises(updates.UpdateError):
                handler.redirect_request(request, None, 302, "Found", {}, value)
