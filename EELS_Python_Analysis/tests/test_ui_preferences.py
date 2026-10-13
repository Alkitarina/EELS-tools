import copy
import json
from pathlib import Path
import tempfile
from eelsfit.runtime_paths import test_runs_dir
import unittest
from unittest.mock import patch

from eels_web import create_app
from eelsfit.io import write_eels_hdf5
from eelsfit.ui_preferences import VERSION, MAX_BYTES
from tests.test_multivariate_workflow import synthetic_data

ROOT = Path(__file__).resolve().parents[1]


class UiPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".tmp_ui_preferences_test_", dir=test_runs_dir())
        self.folder = Path(self.temp.name)
        self.data = self.folder / "synthetic.h5"
        write_eels_hdf5(synthetic_data(self.data), self.data)
        self.apps = []
        self.client = self.client_for_new_service()
        self.file = self.folder / "web_runs" / "ui_preferences.json"
        self.snapshot = {
            "controls": {"wf-init": "roi", "wf-method": "mcr_als", "wf-core-enabled": False,
                         "wf-region-evidence": "界面选区 · 参数草稿", "wf-peak-high": "60"},
            "workflow": {"seeds": [{"enabled": True, "roi": {"x_min": 0, "x_max": 3, "y_min": 0, "y_max": 7}}]},
            "legacy": {}, "dataset": {"source": str(self.data)}, "details": {"wf-local-panel": True}}

    def client_for_new_service(self):
        app = create_app(self.data)
        app.config["UI_PREFERENCES_FILE"] = self.folder / "web_runs" / "ui_preferences.json"
        self.apps.append(app)
        return app.test_client()

    def tearDown(self):
        for app in self.apps:
            app.extensions["eels_workflow"].shutdown()
            app.extensions["eels_service"].shutdown()
        self.temp.cleanup()

    def save(self, snapshot=None, sequence=1):
        return self.client.post("/api/ui-preferences", json={"snapshot": self.snapshot if snapshot is None else snapshot,
                                                           "writer": {"id": "test-page", "sequence": sequence}})

    def test_restart_retains_unicode_options_and_roi_without_analysis(self):
        self.assertIsNone(self.client.get("/api/ui-preferences").json["record"])
        self.assertFalse(self.file.exists())
        before = self.client.get("/api/workflow/state").json
        self.assertEqual(self.save().status_code, 200)
        self.assertIn("界面选区", self.file.read_text(encoding="utf-8"))
        restarted = self.client_for_new_service()
        self.assertEqual(restarted.get("/api/ui-preferences").json["record"]["snapshot"], self.snapshot)
        self.assertEqual(self.client.get("/api/workflow/state").json, before)
        self.assertEqual(restarted.get("/health").json["ui_preferences_version"], VERSION)
        self.assertEqual(restarted.get("/api/info").json["ui_preferences_version"], VERSION)

    def test_late_same_page_save_cannot_overwrite_newer_values(self):
        self.assertEqual(self.save(sequence=2).status_code, 200)
        late = copy.deepcopy(self.snapshot); late["controls"]["wf-init"] = "random"
        self.assertTrue(self.save(late, sequence=1).json["ignored_stale"])
        self.assertEqual(self.client.get("/api/ui-preferences").json["record"]["snapshot"], self.snapshot)

    def test_atomic_write_failure_keeps_original(self):
        self.save(); before = self.file.read_bytes()
        with patch("eelsfit.ui_preferences.os.replace", side_effect=OSError("simulated write failure")):
            response = self.save(sequence=2)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(list(self.file.parent.glob("*.tmp")), [])

    def test_corrupt_file_is_reported_and_never_overwritten(self):
        self.file.parent.mkdir(parents=True)
        for content in ["{broken", json.dumps({"format": VERSION, "snapshot": self.snapshot, "writer": None})]:
            self.file.write_text(content, encoding="utf-8")
            self.assertEqual(self.client.get("/api/ui-preferences").status_code, 400)
            self.assertEqual(self.save().status_code, 400)
            self.assertEqual(self.file.read_text(encoding="utf-8"), content)

    def test_invalid_payload_and_oversize_do_not_replace_saved_file(self):
        self.save(); before = self.file.read_bytes()
        for invalid in [{**self.snapshot, "results": []}, {"controls": {"wf-structure-file": "contents"}},
                        {"controls": {"wf-init": 7}}, {"controls": {}, "workflow": []},
                        {"controls": {}, "workflow": {"array": [0] * 257}},
                        {"controls": {}, "workflow": {"__proto__": {}}}]:
            self.assertEqual(self.save(invalid, sequence=2).status_code, 400)
        response = self.client.post("/api/ui-preferences", data=" " * (MAX_BYTES + 1), content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.file.read_bytes(), before)

    def test_preferences_do_not_take_analysis_lock_and_reject_foreign_origin(self):
        service = self.apps[0].extensions["eels_service"]
        with service.request_lock:
            self.assertEqual(self.save().status_code, 200)
            self.assertEqual(self.client.get("/api/ui-preferences").status_code, 200)
        self.assertEqual(self.client.post("/api/ui-preferences", json={}, headers={"Origin": "https://example.com"}).status_code, 403)
        self.assertEqual(self.client.get("/api/ui-preferences", headers={"Host": "example.com"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
