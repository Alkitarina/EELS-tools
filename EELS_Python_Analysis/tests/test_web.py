from __future__ import annotations

import tempfile
from eelsfit.runtime_paths import test_runs_dir
import time
import unittest
import io
import json
import subprocess
import sys
import threading
import copy
from pathlib import Path
from unittest.mock import patch

import h5py
import numpy as np

from eels_web import create_app


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix=".tmp_test_web_", dir=test_runs_dir())
        self.path = Path(self.temp_dir.name) / "synthetic_eels.h5"
        energy = np.linspace(0.0, 100.0, 201)
        x_nm = np.linspace(0.0, 4.0, 5)
        y_nm = np.linspace(0.0, 3.0, 4)
        peak = 10.0 * np.exp(-0.5 * ((energy - 45.0) / 5.0) ** 2)
        intensity = np.empty((y_nm.size, x_nm.size, energy.size))
        for y_index in range(y_nm.size):
            for x_index in range(x_nm.size):
                intensity[y_index, x_index] = peak + 0.5 + 0.03 * x_index

        with h5py.File(self.path, "w") as handle:
            handle.create_dataset("/intensity", data=intensity)
            handle.create_dataset("/x_nm", data=x_nm)
            handle.create_dataset("/y_nm", data=y_nm)
            handle.create_dataset("/energy_meV", data=energy)

        self.app = create_app(self.path)
        self.app.config["IMPORT_ROOT"] = Path(self.temp_dir.name) / "imports"
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.app.extensions["eels_service"].shutdown()
        self.temp_dir.cleanup()

    def fit_payload(self) -> dict:
        return {
            "region": "Si",
            "roi": {"x_min": 0.0, "x_max": 2.0, "y_min": 0.0, "y_max": 3.0},
            "fit_min": 20.0,
            "fit_max": 70.0,
            "config": {
                "centers": "45",
                "center_lower": "35",
                "center_upper": "55",
                "fwhm_lower": "4",
                "fwhm_upper": "18",
                "include_baseline": True,
            },
        }

    def test_health_identifies_project_process_and_busy_jobs(self):
        from eels_web import BootstrapJob
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["application"], "eels-python-analysis")
        self.assertEqual(response.json["launcher_protocol"], 1)
        self.assertEqual(response.json["result_format"], "eels_peak_results_v1")
        self.assertEqual(Path(response.json["project_root"]), Path(__file__).resolve().parents[1])
        self.assertGreater(response.json["pid"], 0)
        self.assertFalse(response.json["busy"])
        self.app.extensions["eels_service"].jobs["pending"] = BootstrapJob("pending", ["Si"], 10)
        self.assertTrue(self.client.get("/health").json["busy"])
        self.assertEqual(self.client.get("/health").json["instance_id"], response.json["instance_id"])

    def test_web_and_both_api_defaults_use_500_resamples(self):
        with self.client.get("/") as response:
            self.assertIn('id="samples" type="number" min="2" step="1" value="500"', response.text)
        service = self.app.extensions["eels_service"]
        payload = self.fit_payload()
        payload.update(regions=["Si"], rois={"Si": payload["roi"]})
        with patch.object(service._executor, "submit"):
            self.assertEqual(service.start_bootstrap(payload).total, 500)
            payload["config"] = {"mode": "pca", "component_count": 1, "peak_count": 1,
                                 "min_peak_distance_meV": 5, "min_prominence_percent": 0.5}
            self.assertEqual(service.start_pca_bootstrap(payload).total, 500)

    def test_parallel_pca_export_replays_with_one_worker(self):
        from eelsfit.bootstrap_runtime import MAX_WORKERS
        from reproduce import replay
        if MAX_WORKERS < 2:
            self.skipTest("Two workers required")
        payload = self.fit_payload()
        payload.update(regions=["Si"], rois={"Si": payload["roi"]}, samples=8, workers=2,
                       config={"mode": "pca", "component_count": 1, "peak_count": 1,
                               "min_peak_distance_meV": 5, "min_prominence_percent": 0.5})
        status = self.wait_job(self.client.post("/api/pca/bootstrap", json=payload))
        self.assertEqual(status["status"], "done", status)
        self.assertEqual(status["workers"], 2)
        result = self.client.get("/api/pca/results").json["Si"]
        document = self.client.post("/api/export", json={"results": [
            {"analysis_type": "pca", "region": "Si", "result_id": result["result_id"]}
        ]}).json
        recipe = document["reproducibility"]["analyses"][0]["bootstrap"]
        self.assertEqual(recipe["rng_scheme"], "seedsequence_per_resample_v1")
        self.assertEqual(recipe["backend"], "thread")
        output = Path(self.temp_dir.name) / "parallel.json"
        output.write_text(json.dumps(document), encoding="utf-8")
        reproduced = replay(output)
        self.assertEqual(reproduced["results"], document["results"])
        actual = reproduced["reproducibility"]["analyses"][0]["bootstrap"]
        self.assertEqual(actual["workers"], 1)
        self.assertEqual(actual["backend"], "serial")

    def wait_job(self, started):
        self.assertEqual(started.status_code, 200, started.json)
        for _ in range(500):
            status = self.client.get("/api/jobs/" + started.json["job_id"]).json
            if status["status"] in {"done", "error"}:
                return status
            time.sleep(0.01)
        self.fail("Job did not finish")

    def test_import_hdf5_upload_clears_old_results_and_rejects_stale_dataset(self):
        previous = self.client.get("/api/info").json["dataset_id"]
        self.client.post("/api/fit", json=self.fit_payload())
        with self.path.open("rb") as source:
            started = self.client.post("/api/import", data={"file": (source, "测试数据.h5")})
        self.assertEqual(self.wait_job(started)["status"], "done")
        self.assertNotEqual(previous, self.client.get("/api/info").json["dataset_id"])
        self.assertEqual(self.client.get("/api/results").json, {})
        stale = self.client.post("/api/fit", json=self.fit_payload(), headers={"X-EELS-Dataset": previous})
        self.assertEqual(stale.status_code, 409)

    def test_failed_import_keeps_data_and_results(self):
        result = self.client.post("/api/fit", json=self.fit_payload()).json
        previous = self.client.get("/api/info").json
        started = self.client.post("/api/import", data={"file": (io.BytesIO(b"not hdf5"), "bad.h5")})
        self.assertEqual(self.wait_job(started)["status"], "error")
        self.assertEqual(previous, self.client.get("/api/info").json)
        self.assertEqual(result["result_id"], self.client.get("/api/results").json["Si"]["result_id"])
        self.assertEqual(self.client.post("/api/import", json={"path": "relative.mat"}).status_code, 400)
        self.assertEqual(self.client.post("/api/import", data={"file": (io.BytesIO(b"x"), "x.exe")}).status_code, 400)

    def test_reference_maps_preserve_analysis_and_refresh_after_import(self):
        from tests.test_reference_maps import synthetic_reference_data
        from eelsfit.io import write_eels_hdf5
        before = self.client.post("/api/fit", json=self.fit_payload()).json
        bundle = self.client.get("/api/reference-maps")
        self.assertEqual(bundle.status_code, 200)
        self.assertFalse(bundle.json["maps"]["total_counts"]["available"])
        self.assertEqual(self.client.get("/api/results").json["Si"], before)
        source = Path(self.temp_dir.name) / "with_counts.h5"
        write_eels_hdf5(synthetic_reference_data(),source)
        self.assertEqual(self.wait_job(self.client.post("/api/import",json={"path":str(source)}))["status"],"done")
        updated = self.client.get("/api/reference-maps").json
        self.assertNotEqual(bundle.json["dataset_id"],updated["dataset_id"])
        self.assertTrue(updated["maps"]["total_counts"]["available"])
        stale = self.client.get("/api/reference-maps", headers={"X-EELS-Dataset":bundle.json["dataset_id"]})
        self.assertEqual(stale.status_code,409)

    def test_import_blocks_mutations_until_atomic_switch(self):
        from eelsfit.io import load_eels_hdf5
        entered, release = threading.Event(), threading.Event()
        def delayed(_source, _destination):
            entered.set()
            release.wait(5)
            return load_eels_hdf5(self.path)
        with patch("eels_web.prepare_import", side_effect=delayed):
            started = self.client.post("/api/import", json={"path": str(self.path)})
            self.assertTrue(entered.wait(2))
            try:
                self.assertEqual(self.client.post("/api/fit", json=self.fit_payload()).status_code, 409)
                self.assertEqual(self.client.post("/api/import", json={"path": str(self.path)}).status_code, 409)
            finally:
                release.set()
            self.assertEqual(self.wait_job(started)["status"], "done")

    def test_export_json_replays_all_modes_and_region_specific_bootstrap_seeds(self):
        selections = []
        originals = []
        for mode, region, seed in [("manual", "Si", 18), ("auto", "Ge", 29), ("pca", "Interface", 31)]:
            payload = self.fit_payload()
            payload["region"] = region
            payload["fit_min"] = {"Si": 20, "Ge": 21, "Interface": 22}[region]
            payload["config"].update(mode=mode, seed=seed, peak_count=1, search_starts=4,
                                     component_count=1, min_peak_distance_meV=5, min_prominence_percent=0.5)
            payload.update(regions=[region], rois={region: payload["roi"]}, samples=4, block_size=1, seed=seed)
            endpoint = "/api/pca" if mode == "pca" else "/api"
            self.assertEqual(self.wait_job(self.client.post(endpoint + "/bootstrap", json=payload))["status"], "done")
            result = self.client.get(endpoint + "/results").json[region]
            self.assertEqual(result["recipe"]["bootstrap"]["seed"], seed)
            originals.append(result)
            selections.append({"analysis_type": "pca" if mode == "pca" else "gaussian",
                               "region": region, "result_id": result["result_id"]})
        response = self.client.post("/api/export", json={"results": selections})
        self.assertEqual(response.status_code, 200, response.data[:300])
        self.assertEqual(response.mimetype, "application/json")
        self.assertTrue(response.headers["Content-Disposition"].endswith(".json"))
        manifest = response.json
        self.assertEqual(manifest["format"], "eels_peak_results_v1")
        self.assertEqual(list(manifest)[-1], "reproducibility")
        self.assertEqual(len(manifest["results"]), 3)
        self.assertEqual(manifest["reproducibility"]["analyses"][1]["fit_min"], 21)
        self.assertEqual([r["bootstrap"]["seed"] for r in manifest["reproducibility"]["analyses"]], [18, 29, 31])
        self.assertNotIn('"energy_meV": [', response.data.decode())
        self.assertNotIn('"centers_meV": [', response.data.decode())
        self.assertNotIn('"intensity": [', response.data.decode())
        result_path = Path(self.temp_dir.name) / "results.json"
        result_path.write_bytes(response.data)
        completed = subprocess.run([sys.executable, "-X", "utf8", "reproduce.py", str(result_path)],
                                   capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        replayed = json.loads(result_path.with_name("results_reproduced.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["reproducibility"]["analyses"], replayed["reproducibility"]["analyses"])
        for original, reproduced in zip(manifest["results"], replayed["results"]):
            for key in ("center_meV", "width_meV", "center_std_meV", "width_std_meV", "center_ci95", "width_ci95"):
                np.testing.assert_allclose(original["peaks"][0][key], reproduced["peaks"][0][key], atol=1e-8)

    def test_export_requires_current_result_ids_and_does_not_invent_uncertainty(self):
        self.assertEqual(self.client.post("/api/export", json={"results": []}).status_code, 400)
        first = self.client.post("/api/fit", json=self.fit_payload()).json
        selection = {"analysis_type": "gaussian", "region": "Si", "result_id": first["result_id"]}
        response = self.client.post("/api/export", json={"results": [selection]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["results"][0]["uncertainty"]["status"], "not_computed")
        self.assertIsNone(response.json["results"][0]["peaks"][0]["center_std_meV"])
        self.assertIsNone(response.json["results"][0]["peaks"][0]["width_ci95"])
        self.client.post("/api/fit", json=self.fit_payload())
        self.assertEqual(self.client.post("/api/export", json={"results": [selection]}).status_code, 400)

    def test_cross_origin_import_rejected(self):
        response = self.client.post("/api/import", json={"path": str(self.path)}, headers={"Origin": "https://example.com"})
        self.assertEqual(response.status_code, 403)

    def test_zero_peak_region_remains_in_json(self):
        service = self.app.extensions["eels_service"]
        service.data.intensity[:] = 1.0
        payload = self.fit_payload()
        payload["config"] = {"mode": "pca", "component_count": 1, "peak_count": 2,
                             "min_peak_distance_meV": 5, "min_prominence_percent": 0.5}
        result = self.client.post("/api/pca", json=payload).json
        self.assertEqual(result["detected_peak_count"], 0)
        response = self.client.post("/api/export", json={"results": [
            {"analysis_type": "pca", "region": "Si", "result_id": result["result_id"]}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["results"][0]["region"], "Si")
        self.assertEqual(response.json["results"][0]["detected_peak_count"], 0)
        self.assertEqual(response.json["results"][0]["peaks"], [])

    def test_replay_accepts_moved_input_but_rejects_changed_values_or_versions(self):
        from eelsfit.exporting import json_text
        from reproduce import replay
        result = self.client.post("/api/fit", json=self.fit_payload()).json
        exported = self.client.post("/api/export", json={"results": [
            {"analysis_type": "gaussian", "region": "Si", "result_id": result["result_id"]}]}).json
        result_path = Path(self.temp_dir.name) / "result.json"
        result_path.write_text(json_text(exported), encoding="utf-8")
        moved = self.path.with_name("renamed.h5")
        self.path.rename(moved)
        with self.assertRaisesRegex(ValueError, "Input data not found"):
            replay(result_path)
        reproduced = replay(result_path, moved)
        self.assertEqual(reproduced["results"][0]["region"], "Si")
        exported["reproducibility"]["software"]["python"] = "0.0.0"
        result_path.write_text(json_text(exported), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            replay(result_path, moved)
        with self.assertWarns(RuntimeWarning):
            replay(result_path, moved, allow_version_mismatch=True)
        with h5py.File(moved, "r+") as handle:
            handle["intensity"][0, 0, 0] += 0.1
        with self.assertWarns(RuntimeWarning):
            with self.assertRaisesRegex(ValueError, "Data checksum mismatch"):
                replay(result_path, moved, allow_version_mismatch=True)

    def test_numeric_checksum_is_storage_independent_and_uses_loaded_data(self):
        from eelsfit.exporting import data_checksum
        from eelsfit.io import load_eels_hdf5
        service = self.app.extensions["eels_service"]
        original = data_checksum(service.data)
        reversed_path = self.path.with_name("matlab_order.h5")
        with h5py.File(reversed_path, "w") as handle:
            handle["intensity"] = service.data.intensity.transpose(2, 1, 0)
            handle["x_nm"] = service.data.x_nm
            handle["y_nm"] = service.data.y_nm
            handle["energy_meV"] = service.data.energy_meV
            handle.attrs["irrelevant_header"] = "different"
        self.assertEqual(original, data_checksum(load_eels_hdf5(reversed_path)))
        with h5py.File(self.path, "r+") as handle:
            handle["intensity"][0, 0, 0] += 1
        self.assertEqual(original, data_checksum(service.data))
        self.assertNotEqual(original, data_checksum(load_eels_hdf5(self.path)))

    def test_replay_rejects_unknown_schema_and_mismatched_recipe_identity(self):
        from eelsfit.exporting import json_text
        from reproduce import replay
        result = self.client.post("/api/fit", json=self.fit_payload()).json
        exported = self.client.post("/api/export", json={"results": [
            {"analysis_type": "gaussian", "region": "Si", "result_id": result["result_id"]}]}).json
        path = Path(self.temp_dir.name) / "invalid.json"
        cases = []
        changed = copy.deepcopy(exported)
        changed["format"] = "unknown"
        cases.append(changed)
        changed = copy.deepcopy(exported)
        changed["reproducibility"]["data"]["checksum"]["scope"] = "unknown"
        cases.append(changed)
        changed = copy.deepcopy(exported)
        changed["reproducibility"]["analyses"][0]["region"] = "Ge"
        cases.append(changed)
        changed = copy.deepcopy(exported)
        changed["reproducibility"]["analyses"].append(changed["reproducibility"]["analyses"][0])
        cases.append(changed)
        for case in cases:
            path.write_text(json_text(case), encoding="utf-8")
            with self.assertRaises(ValueError):
                replay(path)

    def test_preview_endpoint_returns_selected_mean_spectrum(self) -> None:
        response = self.client.post(
            "/api/preview",
            json={
                "region": "Interface",
                "roi": {"x_min": 1.0, "x_max": 3.0, "y_min": 0.0, "y_max": 3.0},
                "fit_min": 20.0,
                "fit_max": 70.0,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["region"], "Interface")
        self.assertEqual(response.json["pixel_count"], 12)
        self.assertEqual(len(response.json["energy_meV"]), 101)
        self.assertAlmostEqual(response.json["energy_meV"][0], 20.0)
        self.assertAlmostEqual(response.json["energy_meV"][-1], 70.0)
        self.assertAlmostEqual(response.json["intensity"][50], 10.56, places=2)

    def test_fit_and_bootstrap_endpoints(self) -> None:
        info = self.client.get("/api/info")
        self.assertEqual(info.status_code, 200)
        self.assertEqual(info.json["shape"], {"y": 4, "x": 5, "energy": 201})

        fitted = self.client.post("/api/fit", json=self.fit_payload())
        self.assertEqual(fitted.status_code, 200)
        self.assertAlmostEqual(fitted.json["peaks"][0]["center_meV"], 45.0, places=2)
        self.assertIsNone(fitted.json["peaks"][0]["center_ci95"])

        bootstrap_payload = {
            **self.fit_payload(),
            "regions": ["Si"],
            "rois": {"Si": self.fit_payload()["roi"]},
            "samples": 4,
            "block_size": 2,
        }
        started = self.client.post("/api/bootstrap", json=bootstrap_payload)
        self.assertEqual(started.status_code, 200)
        job_id = started.json["job_id"]

        status = None
        for _ in range(100):
            status = self.client.get(f"/api/jobs/{job_id}").json
            if status["status"] in {"done", "error"}:
                break
            time.sleep(0.02)
        self.assertEqual(status["status"], "done")

        results = self.client.get("/api/results")
        self.assertEqual(results.status_code, 200)
        self.assertEqual(results.json["Si"]["bootstrap"]["successful"], 4)

        exported = self.client.get("/api/export.csv")
        self.assertEqual(exported.status_code, 200)
        self.assertIn(b"center_meV", exported.data)

    def test_automatic_fit_endpoint(self) -> None:
        payload = {
            "region": "Ge",
            "roi": {"x_min": 2.0, "x_max": 4.0, "y_min": 0.0, "y_max": 3.0},
            "fit_min": 20.0,
            "fit_max": 70.0,
            "config": {
                "mode": "auto",
                "peak_count": 1,
                "search_starts": 12,
                "fwhm_lower": "4",
                "fwhm_upper": "18",
                "include_baseline": True,
            },
        }
        fitted = self.client.post("/api/fit", json=payload)
        self.assertEqual(fitted.status_code, 200)
        self.assertEqual(fitted.json["fit_mode"], "auto")
        self.assertEqual(fitted.json["auto_search"]["starts_requested"], 12)
        self.assertGreaterEqual(fitted.json["auto_search"]["starts_successful"], 10)
        self.assertAlmostEqual(fitted.json["peaks"][0]["center_meV"], 45.0, places=1)

        bootstrap_payload = {
            **payload,
            "regions": ["Ge"],
            "rois": {"Ge": payload["roi"]},
            "samples": 4,
            "block_size": 2,
        }
        started = self.client.post("/api/bootstrap", json=bootstrap_payload)
        self.assertEqual(started.status_code, 200)
        job_id = started.json["job_id"]
        status = None
        for _ in range(100):
            status = self.client.get(f"/api/jobs/{job_id}").json
            if status["status"] in {"done", "error"}:
                break
            time.sleep(0.02)
        self.assertEqual(status["status"], "done")
        result = self.client.get("/api/results").json["Ge"]
        self.assertEqual(result["fit_mode"], "auto")
        self.assertEqual(result["bootstrap"]["successful"], 4)

    def test_pca_peak_analysis_and_spatial_bootstrap_endpoints(self) -> None:
        payload = {
            "region": "Interface",
            "roi": {"x_min": 1.0, "x_max": 3.0, "y_min": 0.0, "y_max": 3.0},
            "fit_min": 20.0,
            "fit_max": 70.0,
            "config": {
                "mode": "pca",
                "component_count": 1,
                "peak_count": 1,
                "min_peak_distance_meV": 5.0,
                "min_prominence_percent": 0.5,
            },
        }
        analyzed = self.client.post("/api/pca", json=payload)
        self.assertEqual(analyzed.status_code, 200)
        self.assertEqual(analyzed.json["analysis_type"], "pca")
        self.assertEqual(analyzed.json["detected_peak_count"], 1)
        self.assertAlmostEqual(
            analyzed.json["peaks"][0]["center_meV"], 45.0, places=2
        )
        self.assertAlmostEqual(
            analyzed.json["peaks"][0]["width_meV"], 11.77, delta=0.25
        )
        self.assertIsNone(analyzed.json["peaks"][0]["center_ci95"])

        started = self.client.post(
            "/api/pca/bootstrap",
            json={
                **payload,
                "regions": ["Interface"],
                "rois": {"Interface": payload["roi"]},
                "samples": 4,
                "block_size": 1,
            },
        )
        self.assertEqual(started.status_code, 200)
        job_id = started.json["job_id"]
        status = None
        for _ in range(100):
            status = self.client.get(f"/api/jobs/{job_id}").json
            if status["status"] in {"done", "error"}:
                break
            time.sleep(0.02)
        self.assertEqual(status["status"], "done")
        self.assertEqual(status["analysis_type"], "pca")

        result = self.client.get("/api/pca/results").json["Interface"]
        self.assertEqual(result["bootstrap"]["successful"], 4)
        self.assertIsNotNone(result["peaks"][0]["center_ci95"])

        exported = self.client.get("/api/pca/export.csv")
        self.assertEqual(exported.status_code, 200)
        self.assertIn(b"half_prominence_width_meV", exported.data)


if __name__ == "__main__":
    unittest.main()
