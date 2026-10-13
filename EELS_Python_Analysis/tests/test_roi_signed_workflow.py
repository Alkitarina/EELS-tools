"""Independent synthetic checks for ROI initialization and depletion features."""
import copy
import json
from pathlib import Path
import tempfile
from eelsfit.runtime_paths import test_runs_dir
import unittest

import numpy as np
from threadpoolctl import threadpool_limits

from eelsfit.multivariate import decompose, roi_initial_spectra
from eelsfit.workflow_peaks import candidates, line_fit, cross_validate
from eelsfit.interface_workflow import build_profiles, fit_profile
from eelsfit.workflow_export import export_document, software_identity
from eelsfit.exporting import json_text
from eelsfit.io import write_eels_hdf5
from eels_web import create_app
from reproduce_workflow import replay_workflow
from tests.test_multivariate_workflow import synthetic_data, decomp_settings, profile_settings

ROOT = Path(__file__).resolve().parents[1]


def roi_settings(method="mcr_als"):
    rois = profile_settings()["rois"]
    return {**decomp_settings(method), "init": "roi", "seed_negative_policy": "clip_initial",
            "seed_rois": [{"component": i, "roi": rois[name]} for i, name in enumerate(("Si", "Ge"))]}


class RoiSignedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.limits = threadpool_limits(limits=1)

    def tearDown(self):
        self.limits.restore_original_limits()

    def test_roi_means_use_actual_window_and_complete_pixels_without_changing_data(self):
        data = synthetic_data()
        data.intensity[0, 0, 40] = np.nan
        original = data.intensity.copy()
        settings = {**roi_settings(), "fit_min": 20., "fit_max": 70.}
        result = roi_initial_spectra(data, settings)
        selected = data.intensity[:, data.x_nm <= 4, :][:, :, (data.energy_meV >= 20) & (data.energy_meV <= 70)].reshape(-1, 101)
        expected = selected[np.isfinite(selected).all(axis=1)].mean(axis=0)
        np.testing.assert_array_equal(result["mean_spectra"][0], expected)
        self.assertEqual(result["metadata"][0]["excluded_pixels"], 1)
        self.assertTrue((result["initial_spectra"] > 0).all())
        np.testing.assert_array_equal(original, data.intensity)
        with self.assertRaisesRegex(ValueError, "负通道"):
            roi_initial_spectra(data, {**settings, "seed_negative_policy": "reject"})

    def test_roi_empty_duplicate_and_outside_rejected(self):
        settings = roi_settings()
        data = synthetic_data()
        for change, message in (([], "至少"), ([settings["seed_rois"][0]]*2, "唯一"),
                                ([{"component": 0, "roi": dict(x_min=-1, x_max=1, y_min=0, y_max=7)}], "范围"),
                                ([{"component": 0, "roi": dict(x_min=.1, x_max=.2, y_min=0, y_max=7)}], "至少")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                roi_initial_spectra(data, {**settings, "seed_rois": change})

    def test_partial_roi_initialization_for_both_solvers_is_repeatable(self):
        data = synthetic_data()
        original = data.intensity.copy()
        for method in ("mcr_als", "nmf"):
            settings = {**roi_settings(method), "negative_policy": "clip_for_decomposition", "max_iter": 100}
            result = decompose(data, settings)
            repeated = decompose(data, settings)
            np.testing.assert_array_equal(result["spectra"], repeated["spectra"])
            self.assertEqual(result["summary"]["roi_initialization"]["unseeded_components"], [2])
            self.assertTrue(result["summary"]["roi_initialization"]["used_by_selected_start"])
            self.assertFalse(result["summary"]["roi_initialization"]["fixed_spectra"])
            self.assertLess(result["summary"]["rmse"], .1)
        np.testing.assert_array_equal(original, data.intensity)

    def test_signed_gaussian_and_lorentzian_amplitudes_and_areas(self):
        e = np.linspace(10, 80, 281)
        for shape in ("gaussian", "lorentzian"):
            def peak(center, width):
                d = (e-center)/width
                return np.exp(-4*np.log(2)*d*d) if shape == "gaussian" else 1/(1+4*d*d)
            measured = -1.7*peak(32, 3.4) + 2.2*peak(53, 4.1)
            result = line_fit(e, measured, [53, 32], shape=shape, peak_signs=[1, -1], fwhm_min=1, fwhm_max=8)
            np.testing.assert_allclose(result["centers_meV"], [32, 53], atol=1e-5)
            np.testing.assert_allclose(result["amplitudes"], [-1.7, 2.2], atol=1e-5)
            self.assertLess(result["areas"][0], 0)
            self.assertFalse(result["at_constraint_boundary"].any())
            self.assertLess(result["rmse"], 1e-6)

    def test_joint_fit_recovers_missing_bulk_feature_with_nonnegative_bulk_weights(self):
        e = np.linspace(10, 80, 281)
        peak = lambda c, w: np.exp(-4*np.log(2)*((e-c)/w)**2)
        si, ge = 8*peak(25, 9)+2*peak(37, 3), 6*peak(64, 10)
        measured = .6*si+.4*ge-.7*peak(37, 3)+1.3*peak(48, 4)
        settings = dict(peak_count=2, polarity="both", prominence=.08, distance=4, fwhm_min=1, fwhm_max=7)
        result = cross_validate(e, measured, np.array([si, ge]), settings)
        np.testing.assert_allclose(result["joint"]["centers_meV"], [37, 48], atol=1e-4)
        np.testing.assert_allclose(result["joint"]["amplitudes"], [-.7, 1.3], atol=1e-4)
        np.testing.assert_allclose(result["joint"]["bulk_weights"], [.6, .4], atol=1e-5)
        self.assertTrue((result["two_stage"]["amplitudes"] < 0).any())
        self.assertTrue(np.isfinite(result["comparison"]["amplitude_ratio_joint_to_staged"]).all())
        self.assertLessEqual(result["joint"]["sse"], result["two_stage"]["sse"]+1e-8)
        negative = cross_validate(e, measured, np.array([si, ge]), {**settings, "polarity": "negative"})
        self.assertTrue((negative["joint"]["amplitudes"] < 0).all())
        manual = cross_validate(e, measured, np.array([si, ge]), {**settings, "centers": [48, 37]})
        np.testing.assert_array_equal(manual["joint"]["peak_signs"], [-1, 1])

    def test_negative_only_peak_is_detected_and_legacy_default_remains_positive(self):
        e = np.linspace(10, 80, 281)
        signal = -np.exp(-.5*((e-40)/2)**2)
        self.assertEqual(candidates(e, signal), [])
        found = candidates(e, signal, polarity="both")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["sign"], -1)
        self.assertEqual(found[0]["center_meV"], 40)

    def test_roi_and_signed_settings_survive_compact_export_and_replay(self):
        with tempfile.TemporaryDirectory(prefix=".tmp_roi_signed_replay_", dir=test_runs_dir()) as folder:
            data = synthetic_data(Path(folder)/"signed.h5")
            data.intensity -= .8*np.exp(-((data.x_nm[None,:,None]-7.5)/1.1)**2)*np.exp(-.5*((data.energy_meV[None,None,:]-38)/1.1)**2)
            write_eels_hdf5(data, data.source)
            d = decompose(data, roi_settings());d["result_id"] = "d"
            p = build_profiles(data, d, profile_settings());p.update(result_id="p", decomposition_id="d")
            f = fit_profile(p, {"selection": {"kind": "region", "region": "Core"}, "polarity": "both", "peak_count": 2})
            f.update(result_id="f", profiles_id="p")
            self.assertTrue((f["joint"]["amplitudes"] < 0).any())
            document = export_document(data, dict(decomposition=d, profiles=p, fit=f), software_identity())
            path = Path(folder)/"signed.json";path.write_text(json_text(document), encoding="utf-8")
            replayed = replay_workflow(path)
            np.testing.assert_allclose(replayed["results"]["fit"]["joint"]["amplitudes"], document["results"]["fit"]["joint"]["amplitudes"], atol=1e-8)
            self.assertEqual(document["reproducibility"]["steps"][0]["settings"]["seed_rois"], roi_settings()["seed_rois"])
            self.assertEqual(document["reproducibility"]["steps"][2]["settings"]["polarity"], "both")
            self.assertNotIn('"initial_spectra"', json_text(document))

    def test_initialization_inspection_needs_no_completed_decomposition(self):
        with tempfile.TemporaryDirectory(prefix=".tmp_roi_signed_api_", dir=test_runs_dir()) as folder:
            data = synthetic_data(Path(folder)/"seed.h5");write_eels_hdf5(data, data.source)
            app = create_app(data.source)
            try:
                client = app.test_client()
                mapped = client.get("/api/workflow/initialization-map?fit_min=10&fit_max=80")
                self.assertEqual(mapped.status_code, 200, mapped.text)
                result = client.post("/api/workflow/initialization-preview", json={**roi_settings(), "preprocessing_confirmed": False})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(len(result.json["initial_spectra"]), 2)
                self.assertEqual(app.extensions["eels_workflow"].results, {})
                self.assertEqual(client.get("/health").json["workflow_revision"], "interface_y_stripes_v1")
            finally:
                app.extensions["eels_service"].shutdown()


if __name__ == "__main__":
    unittest.main()
