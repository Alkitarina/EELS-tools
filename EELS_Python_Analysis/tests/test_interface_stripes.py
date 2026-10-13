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
from eelsfit.interface_workflow import build_profiles, fit_profile, STRIP_DATA_VERSION
from eelsfit.multivariate import decompose
from eelsfit.workflow_export import export_document, software_identity
from reproduce_workflow import replay_workflow
from tests.test_multivariate_workflow import synthetic_data, decomp_settings, profile_settings

ROOT = Path(__file__).resolve().parents[1]


def stripe_settings():
    settings = profile_settings()
    settings.update(stripe_y_scope="interface_roi", stripe_rows=3)
    settings["rois"]["Interface"].update(y_min=2.2, y_max=6.2)
    settings["rois"]["Core"].update(x_min=4.5, x_max=5.5, y_min=3., y_max=4.)
    return settings


class InterfaceStripeTests(unittest.TestCase):
    def setUp(self):
        self.limits = threadpool_limits(limits=1)
        self.data = synthetic_data()
        self.parent = {"settings": decomp_settings()}

    def tearDown(self):
        self.limits.restore_original_limits()

    def test_interface_y_starts_at_first_selected_row_and_covers_full_x(self):
        settings = stripe_settings()
        result = build_profiles(self.data, self.parent, settings)
        self.assertEqual([(s["row_start"], s["row_stop"]) for s in result["strips"]], [(3, 6), (6, 7)])
        self.assertEqual([(s["y_min"], s["y_max"]) for s in result["strips"]], [(3., 5.), (6., 6.)])
        np.testing.assert_array_equal(result["x_axis_nm"], self.data.x_nm)
        for strip in result["strips"]:
            expected = self.data.intensity[strip["row_start"]:strip["row_stop"]].mean(axis=0)
            np.testing.assert_array_equal(strip["measured"], expected)
            self.assertEqual(strip["measured"].shape, (31, 141))
        domain = result["summary"]["strip_domain"]
        self.assertEqual((domain["row_count"], domain["x_scope"]), (4, "full_scan"))
        # Removing a complete row from ordinary Interface must not remove it from the stripes.
        settings["rois"]["Core"].update(x_min=6., x_max=9., y_min=4., y_max=4.)
        with_core_row = build_profiles(self.data, self.parent, settings)
        np.testing.assert_array_equal(with_core_row["strips"][0]["measured"], result["strips"][0]["measured"])
        # A height larger than the selected y span yields one shorter stripe.
        settings["stripe_rows"] = 7
        self.assertEqual(len(build_profiles(self.data, self.parent, settings)["strips"]), 1)

    def test_core_mark_uses_only_y_pixel_centers_including_endpoints(self):
        settings = stripe_settings()
        displaced = build_profiles(self.data, self.parent, settings)
        self.assertLess(settings["rois"]["Core"]["x_max"], settings["rois"]["Interface"]["x_min"])
        self.assertEqual([s["core_overlap_rows"] for s in displaced["strips"]], [2, 0])
        settings["rois"]["Core"].update(x_min=7., x_max=8., y_min=5., y_max=6.)
        centered = build_profiles(self.data, self.parent, settings)
        self.assertEqual([s["core_overlap_rows"] for s in centered["strips"]], [1, 1])
        settings["rois"]["Core"].update(x_min=4.5, x_max=5.5)
        shifted = build_profiles(self.data, self.parent, settings)
        self.assertEqual([s["core_overlap_rows"] for s in shifted["strips"]], [1, 1])
        settings["core_enabled"] = False
        self.assertEqual([s["core_overlap_rows"] for s in build_profiles(self.data, self.parent, settings)["strips"]], [0, 0])

    def test_alignment_uses_selected_y_support_and_keeps_body_templates(self):
        settings = stripe_settings()
        plain = build_profiles(self.data, self.parent, settings)
        settings["alignment"] = {"enabled": True, "anchors": [[3., 7.], [6., 8.]]}
        aligned = build_profiles(self.data, self.parent, settings)
        np.testing.assert_array_equal(aligned["templates"], plain["templates"])
        np.testing.assert_allclose(aligned["alignment"]["centers_nm"], np.linspace(7, 8, 4))
        self.assertEqual(aligned["summary"]["strip_domain"]["x_scope"], "aligned_common_support")
        for center in aligned["alignment"]["centers_nm"]:
            query = aligned["x_axis_nm"]+center
            self.assertGreaterEqual(query[0], self.data.x_nm[0])
            self.assertLessEqual(query[-1], self.data.x_nm[-1])
        settings["alignment"]["anchors"] = [[4., 7.], [6., 8.]]
        with self.assertRaisesRegex(ValueError, "覆盖"):
            build_profiles(self.data, self.parent, settings)

    def test_bulk_boxes_outside_stripe_y_never_remove_full_x_data(self):
        settings = stripe_settings()
        settings.update(strip_data_rule=STRIP_DATA_VERSION, bulk_scope="global")
        for name in ("Si", "Ge"):
            settings["rois"][name].update(y_min=0., y_max=1.)
        result = build_profiles(self.data, self.parent, settings)
        self.assertEqual(result["summary"]["measured_strips"], 2)
        self.assertEqual(result["summary"]["available_strips"], 2)
        self.assertEqual(result["summary"]["missing_strips"], [])
        for strip in result["strips"]:
            np.testing.assert_array_equal(strip["measured"], self.data.intensity[strip["row_start"]:strip["row_stop"]].mean(axis=0))
            np.testing.assert_array_equal(strip["templates"], result["templates"])
        np.testing.assert_allclose(result["raw_bulk"][1], self.data.intensity[:2, self.data.x_nm >= 11].mean(axis=(0, 1)), rtol=1e-12, atol=1e-15)
        settings["rois"]["Ge"].update(y_min=6., y_max=7., x_min=13.)
        moved = build_profiles(self.data, self.parent, settings)
        for first, second in zip(result["strips"], moved["strips"]):
            np.testing.assert_array_equal(first["measured"], second["measured"])
            self.assertEqual(first["core_overlap_rows"], second["core_overlap_rows"])

    def test_template_error_keeps_real_maps_and_bulk_gaussian_available(self):
        settings = stripe_settings()
        settings.update(strip_data_rule=STRIP_DATA_VERSION, bulk_scope="global",
                        template={"kind": "parametric", "fwhm_min": 12., "fwhm_max": 3.})
        result = build_profiles(self.data, self.parent, settings)
        self.assertEqual(result["summary"]["measured_strips"], 2)
        self.assertEqual(result["summary"]["available_strips"], 0)
        self.assertEqual(len(result["summary"]["template_errors"]), 2)
        for strip in result["strips"]:
            self.assertEqual(strip["measured_status"], "available")
            self.assertEqual(strip["status"], "unavailable")
            self.assertNotIn("residual", strip)
            np.testing.assert_array_equal(strip["measured"], self.data.intensity[strip["row_start"]:strip["row_stop"]].mean(axis=0))
        bulk = fit_profile(result, {"analysis_kind": "bulk_gaussian", "selection": {"kind": "region", "region": "Si"}, "centers": [28.]})
        self.assertEqual(bulk["analysis_kind"], "bulk_gaussian")
        with self.assertRaisesRegex(ValueError, "体谱不可用"):
            fit_profile(result, {"selection": {"kind": "region", "region": "Interface"}})

        with tempfile.TemporaryDirectory(prefix=".tmp_independent_strips_api_", dir=test_runs_dir()) as folder:
            path = Path(folder)/"source.h5"; write_eels_hdf5(self.data, path)
            app = create_app(path); client = app.test_client(); manager = app.extensions["eels_workflow"]
            try:
                parent = decompose(self.data, decomp_settings()); parent["result_id"] = "global"
                result.update(result_id="profiles", decomposition_id="global")
                manager.results.update(decomposition=parent, profiles=result)
                reply = client.get("/api/workflow/strip?result_id=profiles&index=1")
                self.assertEqual(reply.status_code, 200)
                self.assertEqual(reply.json["measured_status"], "available")
                self.assertEqual(len(reply.json["measured"]), len(self.data.x_nm))
                arrays = client.get("/api/workflow/arrays?decomposition_id=global&profiles_id=profiles")
                with np.load(io.BytesIO(arrays.data)) as archive:
                    self.assertEqual(archive["strip_001_measured"].shape, (31, 141))
                    self.assertNotIn("strip_001_residual", archive)
                preview = client.post("/api/workflow/preview", json={"profiles_id": "profiles", "analysis_kind": "bulk_gaussian", "selection": {"kind": "region", "region": "Si"}})
                self.assertEqual(preview.status_code, 200)
                doc = export_document(self.data, {"decomposition": parent, "profiles": result}, software_identity())
                self.assertEqual(doc["results"]["profiles"]["strips"][1]["measured_status"], "available")
                self.assertNotIn("measured", doc["results"]["profiles"]["strips"][1])
            finally:
                app.extensions["eels_service"].shutdown()

    def test_new_and_legacy_recipes_replay_their_actual_stripe_domain(self):
        with tempfile.TemporaryDirectory(prefix=".tmp_interface_stripe_replay_", dir=test_runs_dir()) as folder:
            path = Path(folder)/"source.h5"
            self.data.source = path
            write_eels_hdf5(self.data, path)
            parent = decompose(self.data, decomp_settings()); parent["result_id"] = "global"
            for legacy in (False, True):
                settings = stripe_settings()
                if legacy:
                    settings.pop("stripe_y_scope"); settings["stripe_rows"] = 2
                else:
                    settings.update(strip_data_rule=STRIP_DATA_VERSION, bulk_scope="global")
                profiles = build_profiles(self.data, parent, settings)
                profiles.update(result_id="profiles", decomposition_id="global")
                fit = fit_profile(profiles, {"selection": {"kind": "strip", "strip": 3 if legacy else 0,
                                                         "x_min": 5., "x_max": 10.}, "centers": [46.], "peak_count": 1})
                fit.update(result_id="fit", profiles_id="profiles")
                doc = export_document(self.data, {"decomposition": parent, "profiles": profiles, "fit": fit}, software_identity())
                if legacy:
                    doc["reproducibility"]["steps"][1]["settings"].pop("stripe_y_scope")
                result_file = Path(folder)/("legacy.json" if legacy else "new.json")
                result_file.write_text(json.dumps(doc), encoding="utf-8")
                repeat = replay_workflow(result_file)
                self.assertEqual(repeat["results"]["profiles"]["strips"], doc["results"]["profiles"]["strips"])
                self.assertEqual(repeat["reproducibility"]["steps"][1]["settings"]["stripe_y_scope"], "full_y" if legacy else "interface_roi")
                np.testing.assert_allclose(repeat["results"]["fit"]["two_stage"]["centers_meV"], doc["results"]["fit"]["two_stage"]["centers_meV"])
            with self.assertRaisesRegex(ValueError, "条带 y"):
                build_profiles(self.data, parent, {**stripe_settings(), "stripe_y_scope": "unknown"})

    def test_api_defaults_to_interface_rows_and_exports_geometry(self):
        with tempfile.TemporaryDirectory(prefix=".tmp_interface_stripe_api_", dir=test_runs_dir()) as folder:
            path = Path(folder)/"source.h5"; write_eels_hdf5(self.data, path)
            app = create_app(path); client = app.test_client(); manager = app.extensions["eels_workflow"]
            try:
                parent = decompose(self.data, decomp_settings()); parent["result_id"] = "global"
                manager.results["decomposition"] = parent
                settings = stripe_settings(); settings.pop("stripe_y_scope"); settings.pop("bulk_scope")
                settings["rois"]["Ge"].update(y_min=0., y_max=1.)
                reply = client.post("/api/workflow/run", json={"stage": "profiles", "decomposition_id": "global", "settings": settings})
                self.assertEqual(reply.status_code, 202)
                deadline = time.monotonic()+20
                while time.monotonic() < deadline:
                    job = manager.status(reply.json["job_id"])
                    if job["status"] not in {"running", "queued"}: break
                    time.sleep(.05)
                self.assertEqual(job["status"], "done", job)
                profile_id = job["result_id"]
                result = client.get("/api/workflow/profiles", query_string={"result_id": profile_id}).json
                self.assertEqual(result["settings"]["stripe_y_scope"], "interface_roi")
                self.assertEqual(result["settings"]["strip_data_rule"], STRIP_DATA_VERSION)
                self.assertEqual(result["settings"]["bulk_scope"], "global")
                self.assertEqual(result["summary"]["available_strips"], 2)
                self.assertEqual([s["core_overlap_rows"] for s in result["strips"]], [2, 0])
                arrays = client.get("/api/workflow/arrays", query_string={"decomposition_id": "global", "profiles_id": profile_id})
                self.assertEqual(arrays.status_code, 200)
                with np.load(io.BytesIO(arrays.data)) as archive:
                    np.testing.assert_array_equal(archive["strip_row_bounds"], [[3, 6], [6, 7]])
                    np.testing.assert_array_equal(archive["strip_core_overlap_rows"], [2, 0])
                    self.assertEqual(archive["strip_000_measured"].shape, (31, 141))
            finally:
                app.extensions["eels_service"].shutdown()


if __name__ == "__main__":
    unittest.main()
