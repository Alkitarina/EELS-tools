from __future__ import annotations

import unittest

import numpy as np
from eelsfit.bootstrap_runtime import INDEPENDENT_SCHEME, MAX_WORKERS

from eelsfit.pca import (
    analyze_pca_reconstruction,
    fit_pca_model,
    spatial_block_pca_bootstrap,
)


class PCAPeakTests(unittest.TestCase):
    def setUp(self) -> None:
        self.energy = np.linspace(0.0, 100.0, 401)
        first = np.exp(-0.5 * ((self.energy - 30.0) / 3.0) ** 2)
        second = np.exp(-0.5 * ((self.energy - 75.0) / 5.0) ** 2)
        rng = np.random.default_rng(24)
        self.cube = np.empty((8, 6, self.energy.size), dtype=float)
        for y_index in range(self.cube.shape[0]):
            for x_index in range(self.cube.shape[1]):
                first_scale = 8.0 + 0.25 * x_index
                second_scale = 5.0 + 0.20 * y_index
                baseline = 0.4 + 0.004 * self.energy + 0.01 * x_index
                self.cube[y_index, x_index] = (
                    first_scale * first
                    + second_scale * second
                    + baseline
                    + rng.normal(0.0, 0.025, self.energy.size)
                )

    def analyze(self):
        spectra = self.cube.reshape(-1, self.energy.size)
        model = fit_pca_model(spectra, n_components=3)
        return analyze_pca_reconstruction(
            model,
            self.energy,
            self.cube[:, :3, :].reshape(-1, self.energy.size),
            peak_count=2,
            min_peak_distance_meV=10.0,
            min_prominence_fraction=0.01,
        )

    def test_pca_reconstruction_recovers_resolved_peak_positions_and_widths(self) -> None:
        result = self.analyze()

        self.assertEqual(result.detected_peak_count, 2)
        np.testing.assert_allclose(
            [peak.center_meV for peak in result.peaks], [30.0, 75.0], atol=0.35
        )
        np.testing.assert_allclose(
            [peak.width_meV for peak in result.peaks], [7.06, 11.77], atol=0.8
        )
        self.assertGreater(result.retained_variance_ratio, 0.97)
        self.assertLess(result.reconstruction_rmse, 0.02)

    def test_pca_spatial_bootstrap_returns_parameter_intervals(self) -> None:
        reference = self.analyze()
        bootstrap = spatial_block_pca_bootstrap(
            self.energy,
            self.cube,
            self.cube[:, :3, :],
            reference,
            n_resamples=20,
            block_size_pixels=2,
            seed=17,
        )

        self.assertGreaterEqual(bootstrap.n_success, 18)
        self.assertEqual(bootstrap.centers_meV.shape[1], 2)
        center_low, center_high = bootstrap.center_ci95
        width_low, width_high = bootstrap.width_ci95
        self.assertTrue(np.all(center_low < center_high))
        self.assertTrue(np.all(width_low < width_high))

    @unittest.skipIf(MAX_WORKERS < 2, "Two workers required")
    def test_parallel_pca_has_identical_ordered_samples(self):
        reference = self.analyze()
        kwargs = dict(n_resamples=12, block_size_pixels=2, seed=17, rng_scheme=INDEPENDENT_SCHEME)
        serial = spatial_block_pca_bootstrap(self.energy, self.cube, self.cube[:, :3], reference, workers=1, **kwargs)
        parallel = spatial_block_pca_bootstrap(self.energy, self.cube, self.cube[:, :3], reference, workers=2, **kwargs)
        for field in ("centers_meV", "widths_meV", "prominences"):
            np.testing.assert_array_equal(getattr(serial, field), getattr(parallel, field))
        self.assertEqual(serial.n_failed, parallel.n_failed)


if __name__ == "__main__":
    unittest.main()
