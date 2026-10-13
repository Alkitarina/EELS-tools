"""JSON restoration preserves saved peaks and publishes complete sessions atomically."""
import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from threadpoolctl import threadpool_limits

from eels_web import create_app
from eelsfit.exporting import json_text
from eelsfit.io import write_eels_hdf5
from eelsfit.interface_workflow import build_profiles, fit_regions, fit_profile
from eelsfit.interface_localization import localize_interface
from eelsfit.multivariate import decompose
from eelsfit.runtime_paths import test_runs_dir
from eelsfit.workflow_export import export_document, software_identity
from eelsfit.workflow_import import read_document, restore_workflow, validate_document
from reproduce_workflow import replay_workflow
from tests.test_bulk_region_workflow import signed_data, fit_config
from tests.test_multivariate_workflow import decomp_settings, profile_settings


class WorkflowImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix=".tmp_workflow_import_", dir=test_runs_dir())
        cls.folder = Path(cls.tmp.name)
        cls.data = signed_data(cls.folder / "原始数据.h5")
        write_eels_hdf5(cls.data, cls.data.source)
        with threadpool_limits(limits=1):
            d = decompose(cls.data, decomp_settings()); d["result_id"] = "d"
            cfg = {**profile_settings(), "bulk_scope": "global", "stripe_y_scope": "interface_roi",
                   "strip_data_rule": "independent_full_x_v1"}
            p = build_profiles(cls.data, d, cfg); p.update(result_id="p", decomposition_id="d")
            fits = fit_regions(p, {"regions": {name: fit_config(name) for name in ("Si", "Ge", "Interface", "Core")}})["fits"]
            fits["strip"] = fit_profile(p, {**fit_config("Interface"), "selection": {
                "kind": "strip", "strip": 1, "x_min": 7., "x_max": 8.}})
            for name, fit in fits.items():
                fit.update(result_id=name, profiles_id="p")
            cls.results = dict(decomposition=d, profiles=p, fits=fits, fit=fits["Core"])
            cls.document = export_document(cls.data, cls.results, software_identity())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def restore(self, document=None, **kwargs):
        with threadpool_limits(limits=1):
            return restore_workflow(self.data, document or self.document, **kwargs)

    def test_preserves_all_saved_models_and_replays_imported_export(self):
        with patch("eelsfit.workflow_peaks.line_fit", side_effect=AssertionError("Unexpected peak refit")):
            restored = self.restore()
        self.assertTrue(restored["same_data"])
        self.assertEqual(set(restored["fits"]), {"Si", "Ge", "Interface", "Core", "strip"})
        for name, original in self.results["fits"].items():
            actual = restored["fits"][name]
            self.assertNotEqual(actual["result_id"], original["result_id"])
            for model_name in ("bulk_fit", "two_stage", "joint"):
                if original.get(model_name) is not None:
                    for field in ("centers_meV", "fwhm_meV", "amplitudes", "areas", "peak_signs"):
                        np.testing.assert_array_equal(actual[model_name][field], original[model_name][field])
                    np.testing.assert_allclose(actual[model_name]["fitted"], original[model_name]["fitted"], atol=1e-12)
                    self.assertIsNone(actual[model_name]["uncertainty"])
        imported = export_document(self.data, {**restored["results"], "fits": restored["fits"]}, software_identity())
        self.assertNotIn('"peak_components"', json_text(imported))
        self.assertNotIn('"measured"', json_text(imported))
        path = self.folder / "导入后.json"
        path.write_text(json_text(imported), encoding="utf-8")
        replayed = replay_workflow(path)
        self.assertEqual(replayed["results"]["regions"], imported["results"]["regions"])
        # Re-import an export that already contains restoration provenance.
        self.assertEqual(set(self.restore(imported)["fits"]), set(restored["fits"]))

    def test_bom_validation_and_inconsistent_documents(self):
        self.assertEqual(read_document(("\ufeff"+json_text(self.document)).encode("utf-8")), self.document)
        invalid = []
        doc = copy.deepcopy(self.document); doc["format"] = "eels_peak_results_v1"; invalid.append(doc)
        doc = copy.deepcopy(self.document); doc["reproducibility"]["steps"][-1]["result_id"] = "d"; invalid.append(doc)
        doc = copy.deepcopy(self.document); doc["reproducibility"]["steps"][-1]["target_key"] = "Si"; invalid.append(doc)
        doc = copy.deepcopy(self.document); doc["results"]["fit"]["two_stage"]["centers_meV"][0] += .1; invalid.append(doc)
        doc = copy.deepcopy(self.document); doc["reproducibility"]["data"]["checksum"]["scope"] = "unknown"; invalid.append(doc)
        for document in invalid:
            with self.subTest(document=document.get("format")), self.assertRaises(ValueError):
                validate_document(document)
        for contents in (b"{", b'{"format":1,"format":2}', b'{"x":NaN}', b"[]"):
            with self.assertRaises(ValueError): read_document(contents)

    def test_version_mismatch_missing_and_moved_data(self):
        document = copy.deepcopy(self.document)
        document["reproducibility"]["software"]["python"] = "old"
        with self.assertRaisesRegex(ValueError, "版本"):
            self.restore(document)
        self.assertIn("跨版本", self.restore(document, allow_version_mismatch=True)["message"])
        with self.assertRaisesRegex(ValueError, "找不到"):
            self.restore(data_path=str(self.folder / "missing.h5"))
        changed = signed_data(self.folder / "其他.h5"); changed.intensity[0, 0, 0] += 1
        write_eels_hdf5(changed, changed.source)
        with self.assertRaisesRegex(ValueError, "校验不匹配"):
            self.restore(data_path=str(changed.source))
        with threadpool_limits(limits=1):
            restored = restore_workflow(changed, self.document, data_path=str(self.data.source))
        self.assertFalse(restored["same_data"])

    def test_partial_no_peaks_and_optional_localization(self):
        with threadpool_limits(limits=1):
            d = self.results["decomposition"]
            p = self.results["profiles"]
            no_peaks = fit_profile(p, {**fit_config("Interface"), "centers": None, "prominence": .9999, "polarity": "negative"})
            no_peaks.update(result_id="empty", profiles_id="p")
            document = export_document(self.data, dict(decomposition=d, profiles=p, fit=no_peaks), software_identity())
            restored = self.restore(document)
            self.assertIsNone(restored["fits"]["Interface"]["two_stage"])
            local = localize_interface(self.data, d, {"decomposition": decomp_settings(), "roi": p["settings"]["rois"]["Interface"]})
            local.update(result_id="local", decomposition_id="d")
            document = export_document(self.data, dict(decomposition=d, localization=local), software_identity())
            restored = self.restore(document)
            self.assertEqual(set(restored["results"]), {"decomposition", "localization"})
            self.assertEqual(restored["fits"], {})

    def test_api_atomic_failure_cancel_timeout_and_success(self):
        app = create_app(self.data.source)
        service = app.extensions["eels_service"]
        manager = app.extensions["eels_workflow"]
        client = app.test_client()
        manager.results = {key: copy.deepcopy(value) for key, value in self.results.items() if key != "fits"}
        manager.fits = copy.deepcopy(self.results["fits"])
        previous = {key: value["result_id"] for key, value in manager.results.items()}
        dataset = service.dataset_id

        def wait(job):
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                result = manager.status(job["job_id"])
                if result["status"] not in {"queued", "running"}: return result
                time.sleep(.03)
            self.fail("Import did not finish")

        def post(doc):
            return client.post("/api/workflow/import", data={"file": (io.BytesIO(json_text(doc).encode("utf-8")), "流程.json")})

        try:
            broken = copy.deepcopy(self.document)
            # Model validation happens after graphs have been rebuilt, before publication.
            for value in (broken["results"]["fit"], broken["results"]["regions"]["Core"]["result"]):
                value["joint"]["fwhm_meV"][0] = -1
            response = post(broken); self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(wait(response.json)["status"], "error")
            self.assertEqual(service.dataset_id, dataset)
            self.assertEqual({k: v["result_id"] for k, v in manager.results.items()}, previous)
            payload = {"stage": "import", "document": self.document}
            job = manager.start(payload); manager.cancel(job["job_id"])
            self.assertEqual(wait(job)["status"], "cancelled")
            self.assertEqual(wait(manager.start(payload, timeout=.001))["status"], "error")
            self.assertEqual({k: v["result_id"] for k, v in manager.results.items()}, previous)
            response = post(self.document); self.assertEqual(response.status_code, 202)
            self.assertEqual(client.post("/api/workflow/run", json={"stage": "decomposition"}).status_code, 409)
            self.assertEqual(wait(response.json)["status"], "done")
            self.assertEqual(service.dataset_id, dataset)
            self.assertEqual(len(manager.fits), 5)
            exported = client.post("/api/workflow/export", json={"result_ids": {
                k: v["result_id"] for k, v in manager.results.items()}})
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertEqual(exported.json["results"]["regions"]["Core"]["result"]["joint"],
                             self.document["results"]["regions"]["Core"]["result"]["joint"])
        finally:
            service.shutdown()

    def test_api_switches_to_recorded_h5_and_old_job_header_still_polls(self):
        other = signed_data(self.folder / "不同电流.h5")
        other.intensity += .2
        write_eels_hdf5(other, other.source)
        app = create_app(other.source)
        service = app.extensions["eels_service"]
        client = app.test_client()
        old_id = service.dataset_id
        headers = {"X-EELS-Dataset": old_id}
        try:
            response = client.post("/api/workflow/import", headers=headers, data={
                "file": (io.BytesIO(json_text(self.document).encode("utf-8")), "流程.json")})
            self.assertEqual(response.status_code, 202, response.text)
            deadline = time.monotonic()+30
            while time.monotonic() < deadline:
                job = client.get("/api/workflow/jobs/"+response.json["job_id"], headers=headers)
                self.assertEqual(job.status_code, 200, job.text)
                if job.json["status"] not in {"queued", "running"}: break
                time.sleep(.03)
            self.assertEqual(job.json["status"], "done", job.text)
            self.assertNotEqual(service.dataset_id, old_id)
            self.assertEqual(service.data.source, self.data.source)
            self.assertEqual(len(app.extensions["eels_workflow"].fits), 5)
            self.assertEqual(client.get("/api/workflow/state", headers=headers).status_code, 409)
            self.assertEqual(client.get("/api/info", headers=headers).json["dataset_id"], service.dataset_id)
        finally:
            service.shutdown()


if __name__ == "__main__":
    unittest.main()
