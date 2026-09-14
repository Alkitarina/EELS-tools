"""Numerical routines for bounded multi-Gaussian EELS fitting."""

from __future__ import annotations

from .bootstrap_runtime import DEFAULT_RESAMPLES, LEGACY_SCHEME, bootstrap_samples

from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np
from scipy.optimize import least_squares
from scipy.signal import find_peaks, savgol_filter


GAUSSIAN_FWHM_FACTOR = 2.0 * np.sqrt(2.0 * np.log(2.0))


@dataclass
class FitConfig:
    """Constraints for a multi-Gaussian fit.

    Peak widths are specified as FWHM in meV. The linear baseline is
    b0 + b1 * energy_meV.
    """

    centers: np.ndarray
    center_lower: np.ndarray
    center_upper: np.ndarray
    fwhm_lower: np.ndarray
    fwhm_upper: np.ndarray
    include_baseline: bool = True
    max_nfev: int = 5_000

    def __post_init__(self) -> None:
        self.centers = np.asarray(self.centers, dtype=float).reshape(-1)
        self.center_lower = np.asarray(self.center_lower, dtype=float).reshape(-1)
        self.center_upper = np.asarray(self.center_upper, dtype=float).reshape(-1)
        self.fwhm_lower = np.asarray(self.fwhm_lower, dtype=float).reshape(-1)
        self.fwhm_upper = np.asarray(self.fwhm_upper, dtype=float).reshape(-1)
        self.validate()

    @property
    def n_peaks(self) -> int:
        return int(self.centers.size)

    def validate(self) -> None:
        if self.centers.size == 0:
            raise ValueError("At least one peak center is required.")

        for name, value in (
            ("center_lower", self.center_lower),
            ("center_upper", self.center_upper),
            ("fwhm_lower", self.fwhm_lower),
            ("fwhm_upper", self.fwhm_upper),
        ):
            if value.size != self.centers.size:
                raise ValueError(
                    f"{name} needs {self.centers.size} values, received {value.size}."
                )
            if not np.all(np.isfinite(value)):
                raise ValueError(f"{name} contains a non-finite value.")

        if np.any(self.center_lower >= self.center_upper):
            raise ValueError("Every center lower bound must be below its upper bound.")
        if np.any((self.centers < self.center_lower) | (self.centers > self.center_upper)):
            raise ValueError("Each initial center must be inside its bounds.")
        if np.any(self.fwhm_lower <= 0.0):
            raise ValueError("FWHM lower bounds must be positive.")
        if np.any(self.fwhm_lower >= self.fwhm_upper):
            raise ValueError("Every FWHM lower bound must be below its upper bound.")


@dataclass
class FitResult:
    """Result of fitting one region-averaged spectrum."""

    energy_meV: np.ndarray
    intensity: np.ndarray
    fitted: np.ndarray
    baseline: np.ndarray
    components: np.ndarray
    amplitudes: np.ndarray
    centers_meV: np.ndarray
    sigmas_meV: np.ndarray
    raw_parameters: np.ndarray
    success: bool
    message: str
    cost: float
    nfev: int

    @property
    def fwhm_meV(self) -> np.ndarray:
        return self.sigmas_meV * GAUSSIAN_FWHM_FACTOR

    @property
    def areas(self) -> np.ndarray:
        return self.amplitudes * self.sigmas_meV * np.sqrt(2.0 * np.pi)

    @property
    def residual(self) -> np.ndarray:
        return self.intensity - self.fitted

    @property
    def rmse(self) -> float:
        return float(np.sqrt(np.mean(np.square(self.residual))))


@dataclass
class AutoFitResult:
    """Best bounded fit selected from deterministic multi-start optimization."""

    result: FitResult
    config: FitConfig
    n_starts_requested: int
    n_starts_successful: int


@dataclass
class BootstrapResult:
    """Distribution of refitted parameters from residual block bootstrap."""

    centers_meV: np.ndarray
    fwhm_meV: np.ndarray
    areas: np.ndarray
    amplitudes: np.ndarray
    n_requested: int
    n_failed: int
    block_size: int

    @property
    def n_success(self) -> int:
        return int(self.centers_meV.shape[0])

    def percentile_interval(
        self, values: np.ndarray, lower: float = 2.5, upper: float = 97.5
    ) -> tuple[np.ndarray, np.ndarray]:
        if values.size == 0:
            raise ValueError("Bootstrap produced no successful fits.")
        return (
            np.percentile(values, lower, axis=0),
            np.percentile(values, upper, axis=0),
        )

    @property
    def center_ci95(self) -> tuple[np.ndarray, np.ndarray]:
        return self.percentile_interval(self.centers_meV)

    @property
    def fwhm_ci95(self) -> tuple[np.ndarray, np.ndarray]:
        return self.percentile_interval(self.fwhm_meV)

    @property
    def area_ci95(self) -> tuple[np.ndarray, np.ndarray]:
        return self.percentile_interval(self.areas)


def expand_values(values: Iterable[float], n_peaks: int, name: str) -> np.ndarray:
    """Accept one shared value or one value per peak."""

    result = np.asarray(list(values), dtype=float).reshape(-1)
    if result.size == 1:
        return np.repeat(result, n_peaks)
    if result.size != n_peaks:
        raise ValueError(f"{name} must contain one value or {n_peaks} values.")
    return result


def parse_number_list(value: str) -> np.ndarray:
    """Parse comma or whitespace separated numeric input from the GUI."""

    cleaned = value.replace(",", " ").replace(";", " ").split()
    if not cleaned:
        raise ValueError("A numeric list is required.")
    return np.asarray([float(item) for item in cleaned], dtype=float)


def _gaussian_components(
    energy_meV: np.ndarray,
    amplitudes: np.ndarray,
    centers_meV: np.ndarray,
    sigmas_meV: np.ndarray,
) -> np.ndarray:
    difference = energy_meV[None, :] - centers_meV[:, None]
    return amplitudes[:, None] * np.exp(
        -0.5 * np.square(difference / sigmas_meV[:, None])
    )


def _unpack_parameters(
    parameters: np.ndarray, n_peaks: int, include_baseline: bool
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    amplitudes = parameters[:n_peaks]
    centers = parameters[n_peaks : 2 * n_peaks]
    sigmas = parameters[2 * n_peaks : 3 * n_peaks]
    baseline_parameters = (
        parameters[3 * n_peaks :] if include_baseline else np.empty(0, dtype=float)
    )
    return amplitudes, centers, sigmas, baseline_parameters


def evaluate_model(
    energy_meV: np.ndarray,
    parameters: np.ndarray,
    n_peaks: int,
    include_baseline: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return total model, individual peak components, and baseline."""

    amplitudes, centers, sigmas, baseline_parameters = _unpack_parameters(
        parameters, n_peaks, include_baseline
    )
    components = _gaussian_components(energy_meV, amplitudes, centers, sigmas)
    if include_baseline:
        baseline = baseline_parameters[0] + baseline_parameters[1] * energy_meV
    else:
        baseline = np.zeros_like(energy_meV)
    return components.sum(axis=0) + baseline, components, baseline


def _initial_baseline(energy_meV: np.ndarray, intensity: np.ndarray) -> tuple[float, float]:
    edge_count = max(2, int(np.ceil(energy_meV.size * 0.1)))
    edge_indices = np.r_[
        np.arange(edge_count),
        np.arange(energy_meV.size - edge_count, energy_meV.size),
    ]
    slope, intercept = np.polyfit(energy_meV[edge_indices], intensity[edge_indices], 1)
    return float(intercept), float(slope)


def _initial_parameters(
    energy_meV: np.ndarray, intensity: np.ndarray, config: FitConfig
) -> np.ndarray:
    if config.include_baseline:
        intercept, slope = _initial_baseline(energy_meV, intensity)
        estimated_baseline = intercept + slope * energy_meV
    else:
        intercept, slope = 0.0, 0.0
        estimated_baseline = np.zeros_like(intensity)

    signal = intensity - estimated_baseline
    signal_height = max(float(np.nanmax(signal)), np.finfo(float).eps)
    amplitudes = np.interp(config.centers, energy_meV, signal)
    amplitudes = np.maximum(amplitudes, signal_height * 0.05)
    sigmas = 0.5 * (config.fwhm_lower + config.fwhm_upper) / GAUSSIAN_FWHM_FACTOR
    parameters = np.r_[amplitudes, config.centers, sigmas]
    if config.include_baseline:
        parameters = np.r_[parameters, intercept, slope]
    return parameters


def _parameter_bounds(config: FitConfig) -> tuple[np.ndarray, np.ndarray]:
    lower = np.r_[
        np.zeros(config.n_peaks),
        config.center_lower,
        config.fwhm_lower / GAUSSIAN_FWHM_FACTOR,
    ]
    upper = np.r_[
        np.full(config.n_peaks, np.inf),
        config.center_upper,
        config.fwhm_upper / GAUSSIAN_FWHM_FACTOR,
    ]
    if config.include_baseline:
        lower = np.r_[lower, -np.inf, -np.inf]
        upper = np.r_[upper, np.inf, np.inf]
    return lower, upper


def _parameter_scales(
    energy_meV: np.ndarray, intensity: np.ndarray, config: FitConfig
) -> np.ndarray:
    """Scale physically different optimization parameters to similar magnitudes."""

    intensity_scale = max(
        float(np.nanmax(np.abs(intensity))),
        float(np.nanmax(intensity) - np.nanmin(intensity)),
        1.0,
    )
    energy_span = max(float(energy_meV[-1] - energy_meV[0]), 1.0)
    center_scale = np.maximum(config.center_upper - config.center_lower, 1.0)
    sigma_scale = np.maximum(
        (config.fwhm_upper - config.fwhm_lower) / GAUSSIAN_FWHM_FACTOR,
        0.1,
    )
    scales = np.r_[
        np.full(config.n_peaks, intensity_scale),
        center_scale,
        sigma_scale,
    ]
    if config.include_baseline:
        scales = np.r_[scales, intensity_scale, intensity_scale / energy_span]
    return scales


def _clip_initial(
    parameters: np.ndarray, lower: np.ndarray, upper: np.ndarray
) -> np.ndarray:
    """Keep the initial point strictly inside finite bounds."""

    clipped = np.asarray(parameters, dtype=float).copy()
    finite_lower = np.isfinite(lower)
    finite_upper = np.isfinite(upper)
    span = np.where(finite_lower & finite_upper, upper - lower, 1.0)
    epsilon = np.maximum(np.abs(span) * 1e-9, 1e-12)
    clipped[finite_lower] = np.maximum(
        clipped[finite_lower], lower[finite_lower] + epsilon[finite_lower]
    )
    clipped[finite_upper] = np.minimum(
        clipped[finite_upper], upper[finite_upper] - epsilon[finite_upper]
    )
    return clipped


def fit_spectrum(
    energy_meV: np.ndarray,
    intensity: np.ndarray,
    config: FitConfig,
    initial_parameters: np.ndarray | None = None,
) -> FitResult:
    """Fit a bounded multi-Gaussian model to a one-dimensional spectrum."""

    energy_meV = np.asarray(energy_meV, dtype=float).reshape(-1)
    intensity = np.asarray(intensity, dtype=float).reshape(-1)
    finite = np.isfinite(energy_meV) & np.isfinite(intensity)
    energy_meV = energy_meV[finite]
    intensity = intensity[finite]

    required_parameters = 3 * config.n_peaks + (2 if config.include_baseline else 0)
    if energy_meV.size <= required_parameters:
        raise ValueError(
            "The fit window needs more energy channels than model parameters."
        )
    if np.any(np.diff(energy_meV) <= 0):
        raise ValueError("Energy values must be strictly increasing.")

    lower, upper = _parameter_bounds(config)
    if initial_parameters is None:
        initial_parameters = _initial_parameters(energy_meV, intensity, config)
    initial_parameters = _clip_initial(initial_parameters, lower, upper)

    response_scale = max(
        float(np.nanmax(np.abs(intensity))),
        float(np.nanmax(intensity) - np.nanmin(intensity)),
        1.0,
    )
    parameter_scales = _parameter_scales(energy_meV, intensity, config)

    def residuals(parameters: np.ndarray) -> np.ndarray:
        model, _, _ = evaluate_model(
            energy_meV, parameters, config.n_peaks, config.include_baseline
        )
        return (model - intensity) / response_scale

    optimized = least_squares(
        residuals,
        initial_parameters,
        bounds=(lower, upper),
        max_nfev=config.max_nfev,
        method="trf",
        x_scale=parameter_scales,
    )
    fitted, components, baseline = evaluate_model(
        energy_meV, optimized.x, config.n_peaks, config.include_baseline
    )
    amplitudes, centers, sigmas, _ = _unpack_parameters(
        optimized.x, config.n_peaks, config.include_baseline
    )

    # Present outputs in ascending energy order. The raw parameter vector stays
    # in the user-defined constraint order for bootstrap refitting.
    order = np.argsort(centers)
    return FitResult(
        energy_meV=energy_meV,
        intensity=intensity,
        fitted=fitted,
        baseline=baseline,
        components=components[order],
        amplitudes=amplitudes[order],
        centers_meV=centers[order],
        sigmas_meV=sigmas[order],
        raw_parameters=optimized.x,
        success=bool(optimized.success),
        message=str(optimized.message),
        cost=float(optimized.cost),
        nfev=int(optimized.nfev),
    )


def _smoothed_seed_signal(
    energy_meV: np.ndarray, intensity: np.ndarray, fwhm_lower: np.ndarray
) -> np.ndarray:
    """Remove a provisional baseline and smooth only for initial-center seeding."""

    intercept, slope = _initial_baseline(energy_meV, intensity)
    signal = intensity - (intercept + slope * energy_meV)
    if signal.size < 5:
        return signal

    energy_step = max(float(np.median(np.diff(energy_meV))), np.finfo(float).eps)
    max_window = signal.size if signal.size % 2 else signal.size - 1
    if max_window < 5:
        return signal
    nominal_window = int(round(float(np.min(fwhm_lower)) / energy_step))
    window = max(5, min(max_window, nominal_window))
    if window % 2 == 0:
        window = min(max_window, window + 1)
    if window < 5:
        return signal
    return savgol_filter(signal, window_length=window, polyorder=min(3, window - 2))


def _spaced_center_seed(
    ranked_centers: np.ndarray,
    lower: float,
    upper: float,
    n_peaks: int,
    min_spacing: float,
) -> np.ndarray:
    """Choose prominent seed centers without starting peaks on top of one another."""

    selected: list[float] = []
    for center in ranked_centers:
        if lower <= center <= upper and all(
            abs(center - existing) >= min_spacing for existing in selected
        ):
            selected.append(float(center))
        if len(selected) == n_peaks:
            return np.sort(np.asarray(selected, dtype=float))

    uniform = np.linspace(lower, upper, n_peaks + 2, dtype=float)[1:-1]
    for center in uniform:
        if all(abs(center - existing) >= min_spacing for existing in selected):
            selected.append(float(center))
        if len(selected) == n_peaks:
            return np.sort(np.asarray(selected, dtype=float))

    # If the requested peak count is denser than the width constraint allows,
    # a uniformly distributed start is still preferable to duplicated centers.
    return uniform


def _random_center_seed(
    rng: np.random.Generator,
    lower: float,
    upper: float,
    n_peaks: int,
    min_spacing: float,
) -> np.ndarray:
    """Draw a sorted start while preferring a useful separation between peaks."""

    candidate = np.linspace(lower, upper, n_peaks + 2, dtype=float)[1:-1]
    for _ in range(100):
        candidate = np.sort(rng.uniform(lower, upper, size=n_peaks))
        if n_peaks == 1 or np.all(np.diff(candidate) >= min_spacing):
            break
    return candidate


def _automatic_center_starts(
    energy_meV: np.ndarray,
    intensity: np.ndarray,
    n_peaks: int,
    fwhm_lower: np.ndarray,
    n_starts: int,
    seed: int | None,
) -> list[np.ndarray]:
    """Generate reproducible heuristic, uniform, and random center starts."""

    lower = float(energy_meV[0])
    upper = float(energy_meV[-1])
    energy_span = upper - lower
    energy_step = max(float(np.median(np.diff(energy_meV))), np.finfo(float).eps)
    edge_margin = min(energy_span * 0.1, max(energy_step * 0.25, 1e-9))
    inner_lower = lower + edge_margin
    inner_upper = upper - edge_margin
    if inner_lower >= inner_upper:
        inner_lower, inner_upper = lower, upper

    min_spacing = max(float(np.min(fwhm_lower)) * 0.35, energy_step)
    smoothed = _smoothed_seed_signal(energy_meV, intensity, fwhm_lower)
    peak_distance = max(1, int(round(min_spacing / energy_step)))
    local_indices, _ = find_peaks(smoothed, distance=peak_distance)
    ranked_indices = np.r_[
        local_indices[np.argsort(smoothed[local_indices])[::-1]],
        np.argsort(smoothed)[::-1],
    ]
    ranked_indices = np.unique(ranked_indices)
    # np.unique sorts values, so re-rank by signal height before selecting starts.
    ranked_indices = ranked_indices[np.argsort(smoothed[ranked_indices])[::-1]]
    heuristic = _spaced_center_seed(
        energy_meV[ranked_indices],
        inner_lower,
        inner_upper,
        n_peaks,
        min_spacing,
    )
    uniform = np.linspace(inner_lower, inner_upper, n_peaks + 2, dtype=float)[1:-1]

    starts: list[np.ndarray] = []
    seen: set[tuple[float, ...]] = set()

    def add_start(values: np.ndarray) -> None:
        values = np.sort(np.clip(np.asarray(values, dtype=float), inner_lower, inner_upper))
        key = tuple(np.round(values, 10))
        if key not in seen and len(starts) < n_starts:
            starts.append(values)
            seen.add(key)

    add_start(heuristic)
    add_start(uniform)
    rng = np.random.default_rng(seed)
    while len(starts) < n_starts:
        add_start(
            _random_center_seed(
                rng, inner_lower, inner_upper, n_peaks, min_spacing
            )
        )
    return starts


def automatic_fit_spectrum(
    energy_meV: np.ndarray,
    intensity: np.ndarray,
    n_peaks: int,
    fwhm_lower: Iterable[float] | float,
    fwhm_upper: Iterable[float] | float,
    include_baseline: bool = True,
    n_starts: int = 48,
    seed: int | None = 20260728,
    max_nfev: int = 2_000,
) -> AutoFitResult:
    """Fit a known number of Gaussians and select the lowest-residual solution.

    Peak centers are free within the selected energy window. The search uses
    several deterministic and random starting center sets, while amplitudes
    remain non-negative and FWHM bounds stay physically explicit.
    """

    energy_meV = np.asarray(energy_meV, dtype=float).reshape(-1)
    intensity = np.asarray(intensity, dtype=float).reshape(-1)
    finite = np.isfinite(energy_meV) & np.isfinite(intensity)
    energy_meV = energy_meV[finite]
    intensity = intensity[finite]

    if int(n_peaks) != n_peaks or n_peaks < 1:
        raise ValueError("n_peaks must be a positive integer.")
    n_peaks = int(n_peaks)
    if n_starts < 1:
        raise ValueError("n_starts must be at least one.")
    if max_nfev < 1:
        raise ValueError("max_nfev must be at least one.")
    if energy_meV.size < 2 or np.any(np.diff(energy_meV) <= 0):
        raise ValueError("Energy values must be strictly increasing.")

    lower_widths = expand_values(
        np.atleast_1d(fwhm_lower), n_peaks, "FWHM lower"
    )
    upper_widths = expand_values(
        np.atleast_1d(fwhm_upper), n_peaks, "FWHM upper"
    )
    if (
        not np.all(np.isfinite(lower_widths))
        or not np.all(np.isfinite(upper_widths))
        or np.any(lower_widths <= 0.0)
        or np.any(lower_widths >= upper_widths)
    ):
        raise ValueError("FWHM bounds must be finite, positive, and ordered.")
    starts = _automatic_center_starts(
        energy_meV,
        intensity,
        n_peaks,
        lower_widths,
        int(n_starts),
        seed,
    )

    best_result: FitResult | None = None
    best_config: FitConfig | None = None
    successful = 0
    center_lower = np.repeat(float(energy_meV[0]), n_peaks)
    center_upper = np.repeat(float(energy_meV[-1]), n_peaks)

    for centers in starts:
        config = FitConfig(
            centers=centers,
            center_lower=center_lower,
            center_upper=center_upper,
            fwhm_lower=lower_widths,
            fwhm_upper=upper_widths,
            include_baseline=include_baseline,
            max_nfev=int(max_nfev),
        )
        try:
            result = fit_spectrum(energy_meV, intensity, config)
        except (FloatingPointError, RuntimeError, ValueError):
            continue
        if not np.isfinite(result.rmse) or not np.all(np.isfinite(result.centers_meV)):
            continue
        successful += 1
        if best_result is None or result.rmse < best_result.rmse:
            best_result = result
            best_config = config

    if best_result is None or best_config is None:
        raise RuntimeError("Automatic fitting failed for every starting point.")
    return AutoFitResult(
        result=best_result,
        config=best_config,
        n_starts_requested=len(starts),
        n_starts_successful=successful,
    )


def estimate_block_size(residual: np.ndarray) -> int:
    """Choose a contiguous residual block length from the residual ACF."""

    residual = np.asarray(residual, dtype=float).reshape(-1)
    residual = residual[np.isfinite(residual)]
    if residual.size < 8:
        return 1
    residual = residual - np.mean(residual)
    scale = np.dot(residual, residual)
    if scale <= np.finfo(float).eps:
        return 1

    autocorrelation = np.correlate(residual, residual, mode="full")[residual.size - 1 :]
    autocorrelation = autocorrelation / scale
    cutoff = np.flatnonzero(np.abs(autocorrelation[1:]) < np.exp(-1.0))
    if cutoff.size == 0:
        candidate = max(2, residual.size // 10)
    else:
        candidate = int(cutoff[0] + 1)
    return int(np.clip(candidate, 1, max(1, residual.size // 4)))


def _sample_circular_blocks(
    residual: np.ndarray, block_size: int, rng: np.random.Generator
) -> np.ndarray:
    sample_count = residual.size
    blocks_needed = int(np.ceil(sample_count / block_size))
    starts = rng.integers(0, sample_count, size=blocks_needed)
    indices = (starts[:, None] + np.arange(block_size)[None, :]) % sample_count
    return residual[indices].reshape(-1)[:sample_count]


def residual_block_bootstrap(
    fit_result: FitResult,
    config: FitConfig,
    n_resamples: int = DEFAULT_RESAMPLES,
    block_size: int | None = None,
    seed: int | None = None,
    progress: Callable[[int, int], None] | None = None,
    workers: int = 1,
    rng_scheme: str = LEGACY_SCHEME,
) -> BootstrapResult:
    """Refit spectra built from circular block-resampled fit residuals.

    This is conditional on the selected ROI, fit model, preprocessing, and
    parameter bounds. It does not account for uncertainty from changing those
    analysis choices.
    """

    if n_resamples < 2:
        raise ValueError("At least two bootstrap resamples are required.")

    residual = fit_result.residual - np.mean(fit_result.residual)
    if block_size is None:
        block_size = estimate_block_size(residual)
    if block_size < 1:
        raise ValueError("Block size must be at least one.")

    samples = bootstrap_samples(_gaussian_resample, {
        "fit": fit_result, "config": config, "residual": residual, "block_size": block_size,
    }, n_resamples, seed, workers, rng_scheme, progress)
    successful = [sample for sample in samples if sample is not None]
    if not successful:
        raise RuntimeError("All bootstrap fits failed. Review bounds and fit window.")

    return BootstrapResult(
        centers_meV=np.asarray([sample[0] for sample in successful]),
        fwhm_meV=np.asarray([sample[1] for sample in successful]),
        areas=np.asarray([sample[2] for sample in successful]),
        amplitudes=np.asarray([sample[3] for sample in successful]),
        n_requested=n_resamples,
        n_failed=n_resamples - len(successful),
        block_size=int(block_size),
    )


def _gaussian_resample(context, rng):
    fit = context["fit"]
    synthetic = fit.fitted + _sample_circular_blocks(context["residual"], context["block_size"], rng)
    try:
        refit = fit_spectrum(fit.energy_meV, synthetic, context["config"], initial_parameters=fit.raw_parameters)
        if not refit.success or not np.all(np.isfinite(refit.centers_meV)):
            return None
        return refit.centers_meV, refit.fwhm_meV, refit.areas, refit.amplitudes
    except (FloatingPointError, RuntimeError, ValueError):
        return None
