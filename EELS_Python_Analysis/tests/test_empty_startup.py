"""An empty service stays empty until an explicit successful data/workflow import."""
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from threadpoolctl import threadpool_limits

from eels_web import create_app
from eelsfit.exporting import json_text
from eelsfit.io import write_eels_hdf5
from eelsfit.interface_workflow import build_profiles, fit_profile
from eelsfit.multivariate import decompose
from eelsfit.runtime_paths import test_runs_dir
from eelsfit.workflow_export import export_document, software_identity
from tests.test_multivariate_workflow import synthetic_data, decomp_settings, profile_settings
from tests.test_bulk_region_workflow import fit_config


class EmptyStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".tmp_empty_start_", dir=test_runs_dir())
        self.folder = Path(self.tmp.name)
        with patch("eels_web.load_eels_hdf5", side_effect=AssertionError("Startup must not load a file")):
            self.app = create_app()
        self.app.config["IMPORT_ROOT"] = self.folder / "imports"
        self.app.config["UI_PREFERENCES_FILE"] = self.folder / "ui_preferences.json"
        self.client = self.app.test_client()
        self.service = self.app.extensions["eels_service"]

    def tearDown(self):
        self.service.shutdown()
        self.tmp.cleanup()

    def wait(self, path, headers=None):
        deadline = time.monotonic()+30
        while time.monotonic() < deadline:
            response = self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            if response.json["status"] not in {"queued", "running"}:
                return response.json
            time.sleep(.03)
        self.fail("Import did not finish")

    def test_empty_health_info_and_data_guards_preserve_preferences(self):
        self.assertIsNone(self.service.data)
        with self.client.get("/") as response:
            self.assertEqual(response.status_code, 200)
        health = self.client.get("/health").json
        self.assertFalse(health["dataset_loaded"])
        self.assertIsNone(health["source"])
        self.assertFalse(health["busy"])
        self.assertEqual(health["empty_startup_version"], "deferred_data_v1")
        info = self.client.get("/api/info").json
        for field in ("source", "shape", "x_nm", "y_nm", "energy_meV"):
            self.assertIsNone(info[field])
        self.assertEqual(self.client.get("/api/workflow/state").json["result_ids"], {})
        for path in ("/api/map", "/api/reference-maps", "/api/workflow/decomposition", "/api/workflow/arrays"):
            self.assertEqual(self.client.get(path).status_code, 409, path)
        for path in ("/api/pca", "/api/fit", "/api/preview", "/api/region-fit", "/api/workflow/run", "/api/export"):
            self.assertEqual(self.client.post(path, json={}).status_code, 409, path)
        preferences = Path(self.app.config["UI_PREFERENCES_FILE"])
        preferences.write_bytes(b"keep this prior preference file unchanged")
        response = self.client.post("/api/ui-preferences", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(preferences.read_bytes(), b"keep this prior preference file unchanged")

    def test_failed_import_keeps_empty_then_h5_initializes_real_data(self):
        initial_id = self.service.dataset_id
        invalid = self.client.post("/api/import", data={"file": (io.BytesIO(b"not HDF5"), "invalid.h5")})
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(self.wait("/api/jobs/"+invalid.json["job_id"])["status"], "error")
        self.assertIsNone(self.service.data)
        self.assertEqual(self.service.dataset_id, initial_id)
        data = synthetic_data(self.folder/"首次导入.h5")
        write_eels_hdf5(data, data.source)
        response = self.client.post("/api/import", json={"path": str(data.source)})
        self.assertEqual(self.wait("/api/jobs/"+response.json["job_id"])["status"], "done")
        info = self.client.get("/api/info").json
        self.assertTrue(info["dataset_loaded"])
        self.assertNotEqual(info["dataset_id"], initial_id)
        self.assertEqual(info["source"], str(data.source))
        self.assertEqual(self.client.get("/api/map?fit_min=10&fit_max=80").status_code, 200)

    def test_json_can_be_first_import(self):
        with threadpool_limits(limits=1):
            data = synthetic_data(self.folder/"流程原始数据.h5")
            write_eels_hdf5(data, data.source)
            d = decompose(data, decomp_settings()); d["result_id"] = "d"
            p = build_profiles(data, d, profile_settings()); p.update(result_id="p", decomposition_id="d")
            fitted = fit_profile(p, fit_config("Si")); fitted.update(result_id="f", profiles_id="p")
            document = export_document(data, dict(decomposition=d, profiles=p, fit=fitted), software_identity())
        headers = {"X-EELS-Dataset": self.service.dataset_id}
        response = self.client.post("/api/workflow/import", headers=headers, data={
            "file": (io.BytesIO(json_text(document).encode("utf-8")), "流程.json")})
        self.assertEqual(response.status_code, 202, response.text)
        job = self.wait("/api/workflow/jobs/"+response.json["job_id"], headers)
        self.assertEqual(job["status"], "done", job)
        self.assertTrue(self.client.get("/health").json["dataset_loaded"])
        actual = self.app.extensions["eels_workflow"].fits["Si"]
        self.assertEqual(list(actual["bulk_fit"]["centers_meV"]), list(fitted["bulk_fit"]["centers_meV"]))


if __name__ == "__main__":
    unittest.main()
