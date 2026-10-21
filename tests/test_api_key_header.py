# SPDX-License-Identifier: GPL-3.0-or-later
"""The API key must travel in the X-API-Key header and never in a URL.

Run with the Python that ships with QGIS (it provides the qgis modules):
    QT_QPA_PLATFORM=offscreen <QGIS python> -m unittest discover tests
"""
import os
import sys
import unittest
from unittest import mock

from qgis.core import QgsApplication

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

KEY = "k" * 32
_app = QgsApplication([], True)
_app.initQgis()

from gamma_earth_plugin import gamma_earth_plugin as plugin  # noqa: E402


class ApiKeyHeaderTest(unittest.TestCase):
    def test_headers_carry_the_key(self):
        headers = plugin.api_headers(KEY)
        self.assertEqual(headers["X-API-Key"], KEY)
        self.assertTrue(headers["User-Agent"].startswith("GammaEarthQGIS/"))

    def test_credits_request_has_no_key_in_the_url(self):
        response = mock.Mock(status_code=200, json=lambda: {"credits": {}, "jobs": []})
        with mock.patch.object(plugin.requests, "get", return_value=response) as get:
            worker = plugin.CreditsWorker(KEY, 5)
            results = []
            worker.result.connect(results.append)
            worker.run()
        url = get.call_args.args[0]
        self.assertEqual(url, plugin.DEFAULT_API_BASE_URL)
        self.assertNotIn(KEY, url)
        self.assertEqual(get.call_args.kwargs["headers"]["X-API-Key"], KEY)
        self.assertEqual(get.call_args.kwargs["params"], {"limit": 5})
        self.assertEqual(len(results), 1)

    def test_submit_posts_to_the_base_url_with_the_header(self):
        sent = {}

        class Dialog:
            def set_busy(self, *_):
                pass

        instance = plugin.GammaEarthPlugin.__new__(plugin.GammaEarthPlugin)
        instance.dialog = Dialog()
        instance.run_request = lambda method, url, on_ok, on_err, **kw: sent.update(
            method=method, url=url, **kw)
        instance.submit_job(KEY, "1 2 3 4", "2024-01-31", "7", "100", ["MS"])
        self.assertEqual(sent["method"], "POST")
        self.assertEqual(sent["url"], plugin.DEFAULT_API_BASE_URL)
        self.assertNotIn(KEY, sent["url"])
        self.assertEqual(sent["headers"]["X-API-Key"], KEY)
        self.assertNotIn(KEY, repr(sent["json"]))

    def test_key_format(self):
        for good in (KEY, "abc-DEF_123.456~xyz"):
            self.assertTrue(plugin.valid_api_key_format(good), good)
        for bad in ("", "has space", "tab\tkey", "new\nline", "ключ", "a" * 5 + "\x00"):
            self.assertFalse(plugin.valid_api_key_format(bad), repr(bad))

    def test_the_dialog_opens_and_shows_the_disclosure(self):
        with mock.patch.object(plugin, "load_api_key", return_value=""):
            dialog = plugin.AOIDialog()
        text = dialog.brand_label.text()
        self.assertIn("api.gamma.earth", text)
        self.assertIn(plugin.PRIVACY_URL, text)
        self.assertEqual(dialog.api_key_input.echoMode(), plugin.QLineEdit.Password)
        dialog.close()

    def test_redact_hides_the_key(self):
        self.assertEqual(plugin.redact(f"boom {KEY}", KEY), "boom ***")


if __name__ == "__main__":
    unittest.main()
