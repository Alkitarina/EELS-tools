from __future__ import annotations

import unittest

import numpy as np
from eelsfit.bootstrap_runtime import INDEPENDENT_SCHEME, MAX_WORKERS

from eelsfit.core import (
    FitConfig,
    automatic_fit_spectrum,
    fit_spectrum,
    residual_block_bootstrap,
)


class GaussianFitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.energy = np.linspace(0.0, 120.0, 401)
        self.config = FitConfig(
            centers=np.array([35.0, 72.0]),
            center_lower=np.array([25.0, 62.0]),
            center_upper=np.array([45.0, 82.0]),
            fwhm_lower=np.array([4.0, 4.0]),
            fwhm_upper=np.array([24.0, 24.0]),
            include_baseline=True,
        )

        peak_one = 8.0 * np.exp(-0.5 * ((self.energy - 35.0) / 4.0) ** 2)
        peak_two = 5.5 * np.exp(-0.5 * ((self.energy - 72.0) / 6.5) ** 2)
        rng = np.random.default_rng(19)
        self.intensity = peak_one + peak_two + 0.8 + 0.005 * self.energy
        self.intensity += rng.normal(0.0, 0.12, self.energy.size)

    def test_constrained_fit_recovers_centers(self) -> None:
        result = fit_spectrum(self.energy, self.intensity, self.config)
        self.assertTrue(result.success)
        np.testing.assert_allclose(result.centers_meV, [35.0, 72.0], atol=0.6)
        self.assertGreater(result.areas[0], 0.0)
        self.assertGreater(result.areas[1], 0.0)

    def test_residual_block_bootstrap_returns_intervals(self) -> None:
        fit = fit_spectrum(self.energy, self.intensity, self.config)
        bootstrap = residual_block_bootstrap(
            fit,
            self.config,
            n_resamples=50,
            block_size=4,
            seed=7,
        )
        self.assertGreaterEqual(bootstrap.n_success, 45)
        low, high = bootstrap.center_ci95
        self.assertTrue(np.all(low < high))
        self.assertTrue(np.all((low < [35.0, 72.0]) & ([35.0, 72.0] < high)))

    def test_automatic_multistart_fit_recovers_unknown_centers(self) -> None:
        automatic = automatic_fit_spectrum(
            self.energy,
            self.intensity,
            n_peaks=2,
            fwhm_lower=4.0,
            fwhm_upper=24.0,
            include_baseline=True,
            n_starts=20,
            seed=31,
        )
        self.assertGreaterEqual(automatic.n_starts_successful, 18)
        np.testing.assert_allclose(
            automatic.result.centers_meV, [35.0, 72.0], atol=0.8
        )
        self.assertLess(automatic.result.rmse, 0.2)

    @unittest.skipIf(MAX_WORKERS < 2, "Two workers required")
    def test_parallel_gaussian_has_identical_ordered_samples(self):
        fit = fit_spectrum(self.energy, self.intensity, self.config)
        kwargs = dict(n_resamples=12, block_size=4, seed=7, rng_scheme=INDEPENDENT_SCHEME)
        serial = residual_block_bootstrap(fit, self.config, workers=1, **kwargs)
        parallel = residual_block_bootstrap(fit, self.config, workers=2, **kwargs)
        for field in ("centers_meV", "fwhm_meV", "areas", "amplitudes"):
            np.testing.assert_array_equal(getattr(serial, field), getattr(parallel, field))
        self.assertEqual(serial.n_failed, parallel.n_failed)


if __name__ == "__main__":
    unittest.main()
