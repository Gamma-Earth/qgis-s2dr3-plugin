# SPDX-License-Identifier: GPL-3.0-or-later
"""An AOI file (GeoJSON, KML, GPX) is sent as `aoi_file`; the bounding box as `aoi`.

Run with the Python that ships with QGIS (it provides the qgis modules):
    QT_QPA_PLATFORM=offscreen <QGIS python> -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from qgis.core import QgsApplication

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_app = QgsApplication([], True)
_app.initQgis()

from gamma_earth_plugin import gamma_earth_plugin as plugin  # noqa: E402

KEY = "k" * 32
GEOJSON = {"type": "FeatureCollection", "features": [{
    "type": "Feature", "properties": {},
    "geometry": {"type": "Polygon", "coordinates": [[[9.19, 45.466], [9.199, 45.46],
                                                     [9.19, 45.453], [9.181, 45.46],
                                                     [9.19, 45.466]]]}}]}
KML = "<?xml version='1.0'?><kml><Placemark/></kml>"


class AoiFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text, mode="w"):
        path = os.path.join(self.tmp.name, name)
        with open(path, mode) as f:
            f.write(text)
        return path

    def test_geojson_is_sent_as_parsed_json(self):
        content, problem = plugin.read_aoi_file(self.write("a.geojson", json.dumps(GEOJSON)))
        self.assertIsNone(problem)
        self.assertEqual(content, GEOJSON)

    def test_kml_and_gpx_are_sent_as_text(self):
        for name in ("a.kml", "A.GPX"):
            content, problem = plugin.read_aoi_file(self.write(name, KML))
            self.assertIsNone(problem)
            self.assertEqual(content, KML)

    def test_a_bom_is_accepted(self):
        path = self.write("a.json", b"\xef\xbb\xbf" + json.dumps(GEOJSON).encode(), "wb")
        self.assertEqual(plugin.read_aoi_file(path)[0], GEOJSON)

    def test_bad_files_give_a_message(self):
        for name, data in (("a.geojson", "not json"), ("b.json", "42"),
                           ("c.json", "null")):
            content, problem = plugin.read_aoi_file(self.write(name, data))
            self.assertIsNone(content, name)
            self.assertIn("not valid", problem)
        content, problem = plugin.read_aoi_file(self.write("d.json", b"\xff\xfe\x00", "wb"))
        self.assertIsNone(content)
        self.assertIn("Cannot read", problem)

    def test_a_file_over_the_service_limit_is_refused_here(self):
        path = self.write("big.json", " " * (plugin.AOI_FILE_MAX_BYTES + 1))
        content, problem = plugin.read_aoi_file(path)
        self.assertIsNone(content)
        self.assertIn("150 KB", problem)

    def _submit(self, aoi):
        sent = {}
        instance = plugin.GammaEarthPlugin.__new__(plugin.GammaEarthPlugin)
        instance.dialog = mock.Mock()
        instance.run_request = lambda method, url, ok, err, **kw: sent.update(kw)
        instance.submit_job(KEY, aoi, "2024-01-31", "7", "100", ["TCI"])
        return sent["json"]

    def test_a_file_replaces_aoi_in_the_payload(self):
        body = self._submit({"aoi_file": GEOJSON})
        self.assertEqual(body["aoi_file"], GEOJSON)
        self.assertNotIn("aoi", body)
        self.assertNotIn(KEY, repr(body))

    def test_a_bounding_box_is_still_sent_as_aoi(self):
        body = self._submit("1 2 3 4")
        self.assertEqual(body["aoi"], "1 2 3 4")
        self.assertNotIn("aoi_file", body)

    def test_the_dialog_labels_are_not_geojson_only(self):
        with mock.patch.object(plugin, "load_api_key", return_value=""):
            dialog = plugin.AOIDialog()
        self.assertNotIn("GeoJSON file", dialog.rb_geojson.text())
        self.assertNotIn("GeoJSON", dialog.geojson_browse.text())
        dialog.close()


if __name__ == "__main__":
    unittest.main()
