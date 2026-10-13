"""Energy-window and individually bounded signed Gaussian regression checks."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from threadpoolctl import threadpool_limits

from eelsfit.exporting import json_text
from eelsfit.interface_workflow import build_profiles, fit_profile
from eelsfit.io import write_eels_hdf5
from eelsfit.multivariate import decompose
from eelsfit.runtime_paths import test_runs_dir
from eelsfit.workflow_export import export_document, software_identity
from eelsfit.workflow_peaks import (AMPLITUDE_LIMIT_METHOD, candidates, cross_validate,
                                    line_fit, local_amplitude_constraint, peak_shapes)
from reproduce_workflow import replay_workflow
from tests.test_multivariate_workflow import synthetic_data, decomp_settings, profile_settings

LIMIT = {"method": AMPLITUDE_LIMIT_METHOD, "factor": 1.5}


class AmplitudeLimitTests(unittest.TestCase):
    def setUp(self):
        self.limits = threadpool_limits(limits=1)

    def tearDown(self):
        self.limits.restore_original_limits()

    def test_outside_window_values_and_nans_do_not_enter_profiles_or_residuals(self):
        data = synthetic_data()
        changed = copy.deepcopy(data)
        outside = (data.energy_meV < 20) | (data.energy_meV > 70)
        changed.intensity[:, :, outside] = np.nan
        settings = {**decomp_settings(), "fit_min": 20, "fit_max": 70}
        first = build_profiles(data, {"settings": settings}, profile_settings())
        second = build_profiles(changed, {"settings": settings}, profile_settings())
        np.testing.assert_array_equal(first["energy_meV"], second["energy_meV"])
        for region in ("Si", "Ge", "Interface", "Core"):
            for key in ("measured", "residual"):
                if key in first["regions"][region]:
                    np.testing.assert_array_equal(first["regions"][region][key], second["regions"][region][key])
        cfg = dict(selection={"kind": "region", "region": "Core"}, polarity="both", amplitude_limit=LIMIT)
        a, b = fit_profile(first, cfg), fit_profile(second, cfg)
        np.testing.assert_array_equal(a["joint"]["amplitudes"], b["joint"]["amplitudes"])

    def test_broad_gaussian_tail_can_cross_cutoff_without_outside_residual_penalty(self):
        energy = np.linspace(12, 80, 273)
        measured = 4*peak_shapes(energy, [14], [24])[:, 0]
        fit = line_fit(energy, measured, [14], fwhm_min=1, fwhm_max=35, center_radius=1)
        np.testing.assert_allclose(fit["centers_meV"], [14], atol=1e-6)
        np.testing.assert_allclose(fit["fwhm_meV"], [24], atol=1e-5)
        self.assertLess(fit["sse"], 1e-12)
        self.assertEqual(fit["fit_residual"].shape, energy.shape)
        self.assertGreater(peak_shapes(np.array([5.]), fit["centers_meV"], fit["fwhm_meV"])[0, 0], .5)
        self.assertTrue(np.all(fit["center_bounds"] >= 12))
        with self.assertRaises(ValueError):
            line_fit(energy, measured, [10])

    def test_each_signed_peak_is_bounded_even_when_large_peaks_cancel(self):
        energy = np.linspace(12, 80, 273)
        measured = peak_shapes(energy, [25, 29], [17, 18]) @ np.array([-9., 10.])
        detected = candidates(energy, measured, count=2, polarity="both", prominence=.01)
        self.assertEqual(len(detected), 2)
        centers = [p["center_meV"] for p in detected]
        signs = [p["sign"] for p in detected]
        settings = dict(center_radius=12, amplitude_limit=LIMIT)
        cap = local_amplitude_constraint(energy, measured, centers, signs, settings, "fixed_nnls_signed_residual")
        legacy = line_fit(energy, measured, centers, peak_signs=signs, center_radius=12, fwhm_max=30)
        limited = line_fit(energy, measured, centers, peak_signs=signs, center_radius=12, fwhm_max=30,
                           amplitude_constraint=cap)
        self.assertTrue(np.any(abs(legacy["amplitudes"]) > cap["amplitude_abs_upper"]*1.01))
        self.assertTrue(np.all(abs(limited["amplitudes"]) <= cap["amplitude_abs_upper"]*(1+1e-9)))
        self.assertTrue(limited["at_amplitude_limit"].any())
        self.assertTrue(np.all(limited["at_constraint_boundary"][limited["at_amplitude_limit"]]))
        np.testing.assert_array_equal(limited["peak_signs"], signs)

    def test_joint_and_staged_share_frozen_residual_caps_and_bulk_weights_stay_nonnegative(self):
        energy = np.linspace(12, 80, 273)
        templates = peak_shapes(energy, [24, 65], [8, 10]).T * 8
        measured = np.array([.6, .4]) @ templates + peak_shapes(energy, [37, 48], [4, 5]) @ [-.7, 1.3]
        result = cross_validate(energy, measured, templates, dict(peak_count=2, polarity="both", prominence=.08,
            distance=4, fwhm_min=1, fwhm_max=7, amplitude_limit=LIMIT))
        a, b = result["two_stage"], result["joint"]
        np.testing.assert_array_equal(a["amplitude_constraint"]["amplitude_abs_upper"], b["amplitude_constraint"]["amplitude_abs_upper"])
        self.assertEqual(b["amplitude_constraint"]["reference_kind"], "fixed_nnls_signed_residual")
        self.assertTrue(np.all(b["bulk_weights"] >= 0))
        self.assertTrue(np.all(abs(b["amplitudes"]) <= b["amplitude_constraint"]["amplitude_abs_upper"]*(1+1e-9)))
        self.assertLessEqual(b["sse"], a["sse"] + 1e-8)

    def test_signed_local_maximum_uses_window_clipped_neighbourhood_and_sorts_metadata(self):
        energy = np.arange(12., 23.)
        signal = np.array([-2, -3, -1, 0, 1, 2, 3, 1, 0, 0, 100.])
        settings = dict(center_radius=2, amplitude_limit=LIMIT)
        cap = local_amplitude_constraint(energy, signal, [18., 13.], [1, -1], settings, "test")
        np.testing.assert_array_equal(cap["reference_abs_heights"], [3, 3])
        np.testing.assert_array_equal(cap["neighborhood_bounds_meV"], [[16, 20], [12, 15]])
        fitted = line_fit(energy, signal, [18, 13], peak_signs=[1, -1], amplitude_constraint=cap)
        np.testing.assert_array_equal(fitted["amplitude_constraint"]["candidate_centers_meV"], [13, 18])
        np.testing.assert_array_equal(fitted["peak_signs"], [-1, 1])
        for config in ({"method": "unknown", "factor": 1.5}, {"method": AMPLITUDE_LIMIT_METHOD, "factor": 0},
                       {"method": AMPLITUDE_LIMIT_METHOD, "factor": float("nan")}):
            with self.assertRaises(ValueError):
                local_amplitude_constraint(energy, signal, [18], [1], {**settings, "amplitude_limit": config}, "test")

    def test_bulk_and_signed_caps_round_trip_and_legacy_recipe_remains_unbounded(self):
        with tempfile.TemporaryDirectory(prefix=".tmp_amplitude_replay_", dir=test_runs_dir()) as directory:
            path = Path(directory)/"input.h5"
            data = synthetic_data(path); write_eels_hdf5(data, path)
            d = decompose(data, decomp_settings()); d["result_id"] = "d"
            p = build_profiles(data, d, profile_settings()); p.update(result_id="p", decomposition_id="d")
            fits = {}
            for name in ("Si", "Core"):
                bulk = name == "Si"
                settings = dict(selection={"kind": "region", "region": name}, amplitude_limit=LIMIT,
                    analysis_kind="bulk_gaussian" if bulk else "residual", polarity="positive" if bulk else "both")
                f = fit_profile(p, settings); f.update(result_id=name, profiles_id="p"); fits[name] = f
            document = export_document(data, dict(decomposition=d, profiles=p, fits=fits, fit=fits["Core"]), software_identity())
            export = Path(directory)/"result.json"; export.write_text(json_text(document), encoding="utf-8")
            replayed = replay_workflow(export)
            for name, field in (("Si", "bulk_fit"), ("Core", "joint")):
                a = document["results"]["regions"][name]["result"][field]
                b = replayed["results"]["regions"][name]["result"][field]
                self.assertEqual(a["amplitude_constraint"], b["amplitude_constraint"])
                np.testing.assert_allclose(a["amplitudes"], b["amplitudes"], atol=1e-8)
            legacy = fit_profile(p, dict(selection={"kind": "region", "region": "Core"}, polarity="both"))
            self.assertNotIn("amplitude_constraint", legacy["joint"])
            self.assertNotIn("amplitude_limit", legacy["settings"])


if __name__ == "__main__":
    unittest.main()
