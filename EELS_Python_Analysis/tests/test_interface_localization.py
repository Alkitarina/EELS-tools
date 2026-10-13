import copy
import io
import json
from pathlib import Path
import tempfile
from eelsfit.runtime_paths import test_runs_dir
import time
import unittest

import numpy as np
from threadpoolctl import threadpool_limits

from eels_web import create_app
from eelsfit.io import write_eels_hdf5
from eelsfit.interface_localization import localize_interface, core_proposal, interface_crop
from eelsfit.multivariate import decompose
from eelsfit.interface_workflow import build_profiles, fit_profile
from eelsfit.workflow_export import export_document, software_identity
from reproduce_workflow import replay_workflow
from tests.test_multivariate_workflow import synthetic_data, decomp_settings, profile_settings

ROOT = Path(__file__).resolve().parents[1]


def local_settings(method="mcr_als"):
    return {"roi": profile_settings()["rois"]["Interface"],
            "decomposition": {"method": method, "components": 3, "starts": 1, "max_iter": 60,
                              "seed": 29, "negative_policy": "clip_for_decomposition" if method == "nmf" else "reject"}}


class InterfaceLocalizationTests(unittest.TestCase):
    def setUp(self):
        self.limits = threadpool_limits(limits=1)
        self.data = synthetic_data()
        self.parent = {"settings": decomp_settings(), "result_id": "global"}

    def tearDown(self):
        self.limits.restore_original_limits()

    def test_both_solvers_use_only_total_interface_and_the_actual_parent_window(self):
        original = self.data.intensity.copy()
        for method in ("mcr_als", "nmf"):
            cfg = local_settings(method)
            cfg["decomposition"].update(fit_min=30., fit_max=50.)
            result = localize_interface(self.data, self.parent, cfg)
            self.assertEqual(result["weights"].shape, (8, 7, 3))
            self.assertEqual(result["summary"]["valid_pixels"], 56)
            self.assertTrue(result["settings"]["includes_existing_core"])
            np.testing.assert_array_equal(result["energy_meV"], self.data.energy_meV)
            changed = copy.deepcopy(self.data)
            changed.intensity[:, :10] += 1000
            repeat = localize_interface(changed, self.parent, cfg)
            np.testing.assert_array_equal(result["weights"], repeat["weights"])
        np.testing.assert_array_equal(self.data.intensity, original)
        reject = local_settings("nmf"); reject["decomposition"]["negative_policy"] = "reject"
        with self.assertRaisesRegex(ValueError, "负值"):
            localize_interface(self.data, self.parent, reject)

    def fixture_local(self):
        crop, roi = interface_crop(self.data, local_settings()["roi"])
        weights = np.ones((8, 7, 3))
        weights[3, 2:4, 0] = 5
        weights[4, 2, 0] = 5
        weights[0, 6, 0] = 6
        return {"result_id": "local", "settings": {"roi": roi, "decomposition": decomp_settings()},
                "weights": weights, "x_nm": crop.x_nm, "y_nm": crop.y_nm}

    def test_expansion_uses_three_widths_same_y_and_leaves_analysis_rois_unchanged(self):
        original = self.data.intensity.copy()
        for method in ("nmf", "mcr_als"):
            cfg = local_settings(method)
            cfg["roi"].update(y_min=2., y_max=5.)
            cfg["x_expansion_percent"] = 100
            saved = copy.deepcopy(cfg)
            result = localize_interface(self.data, self.parent, cfg)
            self.assertEqual(cfg, saved)
            self.assertEqual(result["settings"]["roi"], cfg["roi"])
            self.assertEqual(result["settings"]["analysis_roi"],
                             {"x_min": 3., "x_max": 12., "y_min": 2., "y_max": 5.})
            self.assertEqual(result["weights"].shape, (4, 19, 3))
            self.assertEqual(result["summary"]["analysis_domain"]["requested_width_factor"], 3.)
            np.testing.assert_array_equal(result["x_nm"], self.data.x_nm[6:25])
            np.testing.assert_array_equal(result["y_nm"], self.data.y_nm[2:6])
            changed = copy.deepcopy(self.data)
            outside = ((self.data.x_nm < 3) | (self.data.x_nm > 12))[None, :] | (
                       (self.data.y_nm < 2) | (self.data.y_nm > 5))[:, None]
            changed.intensity[outside] += 1000
            repeat = localize_interface(changed, self.parent, cfg)
            np.testing.assert_array_equal(result["weights"], repeat["weights"])
        np.testing.assert_array_equal(self.data.intensity, original)

    def test_expansion_clips_only_at_image_edges_and_validates_percent(self):
        cfg = local_settings()
        cfg.update(x_expansion_percent=100, roi={"x_min": .5, "x_max": 13., "y_min": 2., "y_max": 5.})
        result = localize_interface(self.data, self.parent, cfg)
        self.assertEqual(result["settings"]["requested_analysis_roi"]["x_min"], -12.)
        self.assertEqual(result["settings"]["analysis_roi"],
                         {"x_min": 0., "x_max": 15., "y_min": 2., "y_max": 5.})
        self.assertTrue(result["summary"]["analysis_domain"]["left_clipped"])
        self.assertTrue(result["summary"]["analysis_domain"]["right_clipped"])
        self.assertTrue(any("截到" in message for message in result["summary"]["warnings"]))
        for bad in (-1, 1001, float("nan"), float("inf"), True, "bad"):
            with self.subTest(percent=bad), self.assertRaisesRegex(ValueError, "扩展比例"):
                localize_interface(self.data, self.parent, {**cfg, "x_expansion_percent": bad})
        with self.assertRaisesRegex(ValueError, "宽度"):
            localize_interface(self.data, self.parent, {**cfg, "roi": {**cfg["roi"], "x_max": .5}})
        legacy = localize_interface(self.data, self.parent, local_settings())
        zero = localize_interface(self.data, self.parent, {**local_settings(), "x_expansion_percent": 0})
        np.testing.assert_array_equal(legacy["weights"], zero["weights"])

    def test_expanded_core_may_be_outside_original_interface_but_remainder_stays_in_roi(self):
        crop, bounds = interface_crop(self.data, {"x_min": 3., "x_max": 12., "y_min": 0., "y_max": 7.})
        weights = np.ones((8, 19, 3))
        weights[3:5, 4:6, 0] = 5  # x=5..5.5, outside Interface x=6..9.
        local = {"result_id": "local", "settings": {**local_settings(), "analysis_roi": bounds,
                 "decomposition": decomp_settings(), "x_expansion_percent": 100},
                 "weights": weights, "x_nm": crop.x_nm, "y_nm": crop.y_nm}
        rois = profile_settings()["rois"]
        before = copy.deepcopy(rois)
        proposal = core_proposal(self.data, local, {"rois": rois, "threshold_percent": 80, "min_pixels": 2})
        self.assertEqual(rois, before)
        self.assertIsNone(proposal["apply_error"])
        self.assertEqual(proposal["selected"]["roi"], {"x_min": 5., "x_max": 5.5, "y_min": 3., "y_max": 4.})
        self.assertEqual(proposal["region_spectra"]["Interface"]["pixel_count"], 56)
        np.testing.assert_allclose(proposal["region_spectra"]["Interface"]["spectrum"],
                                   self.data.intensity[:, 12:19].mean(axis=(0, 1)))
        np.testing.assert_allclose(proposal["region_spectra"]["Core"]["spectrum"],
                                   self.data.intensity[3:5, 10:12].mean(axis=(0, 1)))

    def test_connected_candidates_preview_actual_bounding_rectangle_and_single_pixel(self):
        local = self.fixture_local()
        cfg = {"rois": profile_settings()["rois"], "component": 0, "threshold_percent": 80, "min_pixels": 1}
        proposal = core_proposal(self.data, local, cfg)
        selected = proposal["selected"]
        self.assertEqual((selected["pixel_count"], selected["rectangle_pixels"], selected["extra_rectangle_pixels"]), (3, 4, 1))
        self.assertEqual(proposal["candidate_count"], 2)
        self.assertIsNone(proposal["apply_error"])
        expected = self.data.intensity[3:5, 14:16].mean(axis=(0, 1))
        np.testing.assert_allclose(proposal["region_spectra"]["Core"]["spectrum"], expected)
        single = core_proposal(self.data, local, {**cfg, "threshold_percent": 100})
        self.assertEqual(single["selected"]["pixel_count"], 1)
        self.assertEqual(single["selected"]["rectangle_pixels"], 1)
        self.assertEqual(int(single["preview_mask"].sum()), 1)
        self.assertEqual(single["region_spectra"]["Core"]["pixel_count"], 1)

    def test_empty_uniform_stale_and_whole_interface_are_not_silent_core_selections(self):
        local = self.fixture_local()
        cfg = {"rois": profile_settings()["rois"], "component": 0, "min_pixels": 99}
        self.assertIsNone(core_proposal(self.data, local, cfg)["selected"])
        with self.assertRaisesRegex(ValueError, "空间权重差"):
            core_proposal(self.data, local, {**cfg, "component": 1})
        stale = copy.deepcopy(cfg); stale["rois"]["Interface"]["x_min"] = 6.5
        with self.assertRaisesRegex(ValueError, "选框已变化"):
            core_proposal(self.data, local, stale)
        whole = core_proposal(self.data, local, {**cfg, "threshold_percent": 0, "min_pixels": 1})
        self.assertIn("普通界面没有像素", whole["apply_error"])
        low = core_proposal(self.data, local, {**cfg, "direction": "low", "threshold_percent": 0, "min_pixels": 1})
        self.assertIsNotNone(low["selected"])

    def test_local_recipe_roundtrip_is_compact_and_checks_parent_identity(self):
        with tempfile.TemporaryDirectory(prefix=".tmp_local_replay_", dir=test_runs_dir()) as folder:
            path = Path(folder)/"synthetic.h5"; self.data.source = path; write_eels_hdf5(self.data, path)
            parent = decompose(self.data, decomp_settings()); parent["result_id"] = "global"
            for expansion in ({}, {"x_expansion_percent": 100}):
                local = localize_interface(self.data, parent, {**local_settings(), **expansion})
                local.update(result_id="local", decomposition_id="global")
                document = export_document(self.data, {"decomposition": parent, "localization": local}, software_identity())
                self.assertEqual(len(document["reproducibility"]["steps"]), 1)
                self.assertNotIn("weights", document["results"]["localization"])
                self.assertEqual("analysis_roi" in document["results"]["localization"], bool(expansion))
                file = Path(folder)/"result.json"; file.write_text(json.dumps(document), encoding="utf-8")
                repeat = replay_workflow(file)
                self.assertEqual(repeat["results"]["localization"], document["results"]["localization"])
            document["reproducibility"]["localization"]["decomposition_id"] = "wrong"
            file.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "父结果"):
                replay_workflow(file)

    def test_job_success_failure_cancel_and_timeout_preserve_primary_results(self):
        with tempfile.TemporaryDirectory(prefix=".tmp_local_api_", dir=test_runs_dir()) as folder:
            path = Path(folder)/"synthetic.h5"; write_eels_hdf5(self.data, path)
            app = create_app(path); client = app.test_client(); manager = app.extensions["eels_workflow"]
            service = app.extensions["eels_service"]
            try:
                parent = decompose(self.data, decomp_settings()); parent["result_id"] = "global"
                profiles = build_profiles(self.data, parent, profile_settings()); profiles.update(result_id="profiles", decomposition_id="global")
                fitted = fit_profile(profiles, {"selection": {"kind": "region", "region": "Core"}})
                fitted.update(result_id="fit", profiles_id="profiles")
                manager.results.update(decomposition=parent, profiles=profiles, fit=fitted); manager.fits["Core"] = fitted
                def wait(job):
                    deadline = time.monotonic()+20
                    while time.monotonic() < deadline:
                        reply = client.get("/api/workflow/jobs/"+job).json
                        if reply["status"] not in ("running", "queued"): return reply
                        time.sleep(.03)
                    self.fail("Local job did not finish")
                payload = {"stage": "localization", "decomposition_id": "global", "settings": local_settings()}
                completed = wait(client.post("/api/workflow/run", json=payload).json["job_id"])
                self.assertEqual(completed["status"], "done", completed)
                local_id = completed["result_id"]
                view = client.get("/api/workflow/localization?result_id="+local_id)
                self.assertEqual(view.status_code, 200)
                self.assertEqual(view.json["settings"]["x_expansion_percent"], 100)
                self.assertEqual(np.asarray(view.json["weights"]).shape, (8, 19, 3))
                failed = copy.deepcopy(payload); failed["settings"]["roi"]["x_max"] = 100
                self.assertEqual(wait(client.post("/api/workflow/run", json=failed).json["job_id"])["status"], "error")
                job = client.post("/api/workflow/run", json=payload).json["job_id"]
                client.post("/api/workflow/jobs/"+job+"/cancel")
                self.assertEqual(wait(job)["status"], "cancelled")
                app.config["WORKFLOW_MAX_SECONDS"] = .001
                self.assertEqual(wait(client.post("/api/workflow/run", json=payload).json["job_id"])["status"], "error")
                self.assertEqual(manager.results["localization"]["result_id"], local_id)
                self.assertIs(manager.results["profiles"], profiles); self.assertIs(manager.fits["Core"], fitted)
                exported = client.post("/api/workflow/export", json={"result_ids": {"decomposition": "global", "profiles": "profiles", "localization": local_id}})
                self.assertEqual(exported.status_code, 200)
                self.assertEqual(exported.json["results"]["localization"]["result_id"], local_id)
                arrays = client.get("/api/workflow/arrays?decomposition_id=global&localization_id="+local_id)
                self.assertEqual(arrays.status_code, 200)
                with np.load(io.BytesIO(arrays.data)) as archive:
                    np.testing.assert_array_equal(archive["interface_component_weights_yxk"], manager.results["localization"]["weights"])
                    np.testing.assert_array_equal(archive["interface_x_nm"], manager.results["localization"]["x_nm"])
            finally:
                service.shutdown()


if __name__ == "__main__":
    unittest.main()
