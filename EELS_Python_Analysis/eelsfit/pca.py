"""Non-parametric PCA peak analysis for EELS spectrum images."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks, peak_widths

from .bootstrap_runtime import DEFAULT_RESAMPLES, LEGACY_SCHEME, bootstrap_samples


@dataclass(frozen=True)
class PCAModel:
    """Mean spectrum and orthonormal spectral PCA loadings."""

    mean_spectrum: np.ndarray
    components: np.ndarray
    explained_variance_ratio: np.ndarray
    sample_count: int

    @property
    def retained_variance_ratio(self) -> float:
        return float(np.sum(self.explained_variance_ratio))

    def reconstruct(self, spectrum: np.ndarray) -> np.ndarray:
        spectrum = np.asarray(spectrum, dtype=float).reshape(-1)
        if spectrum.size != self.mean_spectrum.size:
            raise ValueError("Spectrum length does not match the PCA energy axis.")
        centered = spectrum - self.mean_spectrum
        return self.mean_spectrum + (centered @ self.components.T) @ self.components


@dataclass(frozen=True)
class PCAPeak:
    """A resolved local maximum measured without a parametric peak model."""

    center_meV: float
    width_meV: float
    prominence: float
    height: float
    width_height: float
    left_meV: float
    right_meV: float


@dataclass(frozen=True)
class PCAResult:
    """PCA reconstruction and non-parametric peak measurements for one ROI."""

    energy_meV: np.ndarray
    intensity: np.ndarray
    reconstructed: np.ndarray
    peaks: tuple[PCAPeak, ...]
    n_components: int
    explained_variance_ratio: np.ndarray
    retained_variance_ratio: float
    reconstruction_rmse: float
    requested_peak_count: int
    min_peak_distance_meV: float
    min_prominence_fraction: float

    @property
    def detected_peak_count(self) -> int:
        return len(self.peaks)

    @property
    def unresolved_peak_count(self) -> int:
        return max(0, self.requested_peak_count - self.detected_peak_count)


@dataclass(frozen=True)
class PCABootstrapResult:
    """Spatial block-bootstrap distribution of PCA peak measurements."""

    centers_meV: np.ndarray
    widths_meV: np.ndarray
    prominences: np.ndarray
    n_requested: int
    n_failed: int
    block_size_pixels: int

    @property
    def n_success(self) -> int:
        return int(self.centers_meV.shape[0])

    @staticmethod
    def percentile_interval(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        interval = np.percentile(values, [2.5, 97.5], axis=0)
        return interval[0], interval[1]

    @property
    def center_ci95(self) -> tuple[np.ndarray, np.ndarray]:
        return self.percentile_interval(self.centers_meV)

    @property
    def width_ci95(self) -> tuple[np.ndarray, np.ndarray]:
        return self.percentile_interval(self.widths_meV)

    @property
    def prominence_ci95(self) -> tuple[np.ndarray, np.ndarray]:
        return self.percentile_interval(self.prominences)


def _finite_rows(spectra: np.ndarray) -> np.ndarray:
    matrix = np.asarray(spectra, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("PCA spectra must be a two-dimensional matrix.")
    matrix = matrix[np.all(np.isfinite(matrix), axis=1)]
    if matrix.shape[0] < 2:
        raise ValueError("PCA requires at least two finite spectra.")
    if matrix.shape[1] < 3:
        raise ValueError("PCA requires at least three energy channels.")
    return matrix


def _validate_component_count(matrix: np.ndarray, n_components: int) -> int:
    if int(n_components) != n_components or n_components < 1:
        raise ValueError("PCA component count must be a positive integer.")
    rank_limit = min(matrix.shape[0] - 1, matrix.shape[1])
    if n_components > rank_limit:
        raise ValueError(
            f"PCA component count cannot exceed {rank_limit} for this data selection."
        )
    return int(n_components)


def fit_pca_model(spectra: np.ndarray, n_components: int) -> PCAModel:
    """Fit an exact mean-centered PCA model to spectra[sample, energy]."""

    matrix = _finite_rows(spectra)
    n_components = _validate_component_count(matrix, n_components)
    mean_spectrum = np.mean(matrix, axis=0)
    centered = matrix - mean_spectrum
    _, singular_values, loadings = np.linalg.svd(centered, full_matrices=False)
    total_variation = float(np.sum(np.square(singular_values)))
    if total_variation > np.finfo(float).eps:
        ratios = np.square(singular_values[:n_components]) / total_variation
    else:
        ratios = np.zeros(n_components, dtype=float)
    return PCAModel(
        mean_spectrum=mean_spectrum,
        components=loadings[:n_components],
        explained_variance_ratio=ratios,
        sample_count=matrix.shape[0],
    )


def _fit_randomized_pca_model(
    spectra: np.ndarray,
    n_components: int,
    rng: np.random.Generator,
    oversamples: int = 8,
    power_iterations: int = 2,
) -> PCAModel:
    """Fit a reproducible truncated PCA model for repeated bootstrap samples."""

    matrix = _finite_rows(spectra)
    n_components = _validate_component_count(matrix, n_components)
    mean_spectrum = np.mean(matrix, axis=0)
    centered = matrix - mean_spectrum
    scale = float(np.linalg.norm(centered))
    if scale <= np.finfo(float).eps:
        return fit_pca_model(matrix, n_components)
    centered = centered / scale

    rank_limit = min(centered.shape[0] - 1, centered.shape[1])
    projected_rank = min(rank_limit, n_components + max(2, oversamples))
    random_directions = rng.standard_normal((centered.shape[1], projected_rank))
    projected = centered @ random_directions
    for _ in range(power_iterations):
        basis, _ = np.linalg.qr(projected, mode="reduced")
        projected = centered @ (centered.T @ basis)
    basis, _ = np.linalg.qr(projected, mode="reduced")
    reduced = basis.T @ centered
    _, singular_values, loadings = np.linalg.svd(reduced, full_matrices=False)
    total_variation = float(np.sum(np.square(centered)))
    ratios = np.square(singular_values[:n_components]) / total_variation
    return PCAModel(
        mean_spectrum=mean_spectrum,
        components=loadings[:n_components],
        explained_variance_ratio=ratios,
        sample_count=matrix.shape[0],
    )


def _quadratic_peak_center(
    energy_meV: np.ndarray, intensity: np.ndarray, index: int
) -> tuple[float, float]:
    if index <= 0 or index >= energy_meV.size - 1:
        return float(energy_meV[index]), float(intensity[index])
    local_energy = energy_meV[index - 1 : index + 2]
    local_intensity = intensity[index - 1 : index + 2]
    coefficients = np.polyfit(local_energy, local_intensity, 2)
    if not np.all(np.isfinite(coefficients)) or coefficients[0] >= 0.0:
        return float(energy_meV[index]), float(intensity[index])
    center = float(-coefficients[1] / (2.0 * coefficients[0]))
    if center < local_energy[0] or center > local_energy[-1]:
        return float(energy_meV[index]), float(intensity[index])
    height = float(np.polyval(coefficients, center))
    return center, height


def estimate_resolved_peaks(
    energy_meV: np.ndarray,
    intensity: np.ndarray,
    peak_count: int,
    min_peak_distance_meV: float,
    min_prominence_fraction: float,
) -> tuple[PCAPeak, ...]:
    """Measure the strongest resolved maxima and their half-prominence widths."""

    energy_meV = np.asarray(energy_meV, dtype=float).reshape(-1)
    intensity = np.asarray(intensity, dtype=float).reshape(-1)
    if energy_meV.size != intensity.size:
        raise ValueError("Energy and intensity must have the same length.")
    if energy_meV.size < 3 or np.any(np.diff(energy_meV) <= 0.0):
        raise ValueError("Energy values must be strictly increasing.")
    if int(peak_count) != peak_count or not 1 <= peak_count <= 16:
        raise ValueError("Requested peak count must be between 1 and 16.")
    if not np.isfinite(min_peak_distance_meV) or min_peak_distance_meV <= 0.0:
        raise ValueError("Minimum peak distance must be positive.")
    if not np.isfinite(min_prominence_fraction) or not 0.0 <= min_prominence_fraction < 1.0:
        raise ValueError("Minimum prominence fraction must be in [0, 1).")
    if not np.all(np.isfinite(intensity)):
        raise ValueError("Peak estimation requires finite intensities.")

    energy_step = float(np.median(np.diff(energy_meV)))
    distance_channels = max(1, int(np.ceil(min_peak_distance_meV / energy_step)))
    signal_range = float(np.ptp(intensity))
    minimum_prominence = min_prominence_fraction * max(
        signal_range, np.finfo(float).eps
    )
    candidate_indices, properties = find_peaks(
        intensity,
        distance=distance_channels,
        prominence=minimum_prominence,
    )
    if candidate_indices.size == 0:
        return ()

    prominences = np.asarray(properties["prominences"], dtype=float)
    strongest = np.argsort(prominences)[::-1][: int(peak_count)]
    selected = sorted(
        zip(candidate_indices[strongest], prominences[strongest], strict=True),
        key=lambda item: item[0],
    )
    channel_coordinates = np.arange(energy_meV.size, dtype=float)
    peaks: list[PCAPeak] = []
    for index, prominence in selected:
        _, width_heights, left_ips, right_ips = peak_widths(
            intensity, [int(index)], rel_height=0.5
        )
        left_meV = float(np.interp(left_ips[0], channel_coordinates, energy_meV))
        right_meV = float(np.interp(right_ips[0], channel_coordinates, energy_meV))
        center_meV, height = _quadratic_peak_center(
            energy_meV, intensity, int(index)
        )
        peaks.append(
            PCAPeak(
                center_meV=center_meV,
                width_meV=right_meV - left_meV,
                prominence=float(prominence),
                height=height,
                width_height=float(width_heights[0]),
                left_meV=left_meV,
                right_meV=right_meV,
            )
        )
    return tuple(peaks)


def analyze_pca_reconstruction(
    model: PCAModel,
    energy_meV: np.ndarray,
    roi_spectra: np.ndarray,
    peak_count: int,
    min_peak_distance_meV: float,
    min_prominence_fraction: float,
) -> PCAResult:
    """Project an ROI mean into a PCA subspace and measure resolved peaks."""

    roi_matrix = _finite_rows(roi_spectra)
    intensity = np.mean(roi_matrix, axis=0)
    reconstructed = model.reconstruct(intensity)
    peaks = estimate_resolved_peaks(
        energy_meV,
        reconstructed,
        peak_count,
        min_peak_distance_meV,
        min_prominence_fraction,
    )
    rmse = float(np.sqrt(np.mean(np.square(reconstructed - intensity))))
    return PCAResult(
        energy_meV=np.asarray(energy_meV, dtype=float).reshape(-1),
        intensity=intensity,
        reconstructed=reconstructed,
        peaks=peaks,
        n_components=model.components.shape[0],
        explained_variance_ratio=model.explained_variance_ratio,
        retained_variance_ratio=model.retained_variance_ratio,
        reconstruction_rmse=rmse,
        requested_peak_count=int(peak_count),
        min_peak_distance_meV=float(min_peak_distance_meV),
        min_prominence_fraction=float(min_prominence_fraction),
    )


def _sample_spatial_blocks(
    cube: np.ndarray, block_size: int, rng: np.random.Generator
) -> np.ndarray:
    cube = np.asarray(cube, dtype=float)
    if cube.ndim != 3:
        raise ValueError("A spatial spectrum image must have shape [y, x, energy].")
    y_count, x_count, energy_count = cube.shape
    if y_count < 1 or x_count < 1:
        raise ValueError("The spatial selection contains no pixels.")
    block_y = min(int(block_size), y_count)
    block_x = min(int(block_size), x_count)
    target_count = y_count * x_count
    block_count = int(np.ceil(target_count / (block_y * block_x)))
    sampled: list[np.ndarray] = []
    for _ in range(block_count):
        start_y = int(rng.integers(0, y_count - block_y + 1))
        start_x = int(rng.integers(0, x_count - block_x + 1))
        block = cube[
            start_y : start_y + block_y,
            start_x : start_x + block_x,
            :,
        ]
        sampled.append(block.reshape(-1, energy_count))
    return np.concatenate(sampled, axis=0)[:target_count]


def spatial_block_pca_bootstrap(
    energy_meV: np.ndarray,
    full_cube: np.ndarray,
    roi_cube: np.ndarray,
    reference: PCAResult,
    n_resamples: int = DEFAULT_RESAMPLES,
    block_size_pixels: int = 2,
    seed: int | None = None,
    progress: Callable[[int, int], None] | None = None,
    workers: int = 1,
    rng_scheme: str = LEGACY_SCHEME,
) -> PCABootstrapResult:
    """Recompute PCA after spatial block resampling and remeasure each peak."""

    if n_resamples < 2:
        raise ValueError("At least two PCA bootstrap resamples are required.")
    if block_size_pixels < 1:
        raise ValueError("Spatial block size must be at least one pixel.")
    if not reference.peaks:
        raise ValueError("No resolved reference peaks are available for bootstrap.")

    samples = bootstrap_samples(_pca_resample, {
        "energy": energy_meV, "full": full_cube, "roi": roi_cube,
        "reference": reference, "block_size": block_size_pixels,
        "fast_sampling": rng_scheme != LEGACY_SCHEME,
    }, n_resamples, seed, workers, rng_scheme, progress, backend="thread")
    successful = [sample for sample in samples if sample is not None]
    if not successful:
        raise RuntimeError(
            "All PCA bootstrap samples failed to reproduce the resolved peaks."
        )
    return PCABootstrapResult(
        centers_meV=np.asarray([sample[0] for sample in successful]),
        widths_meV=np.asarray([sample[1] for sample in successful]),
        prominences=np.asarray([sample[2] for sample in successful]),
        n_requested=int(n_resamples),
        n_failed=n_resamples - len(successful),
        block_size_pixels=int(block_size_pixels),
    )


def _sample_spatial_blocks_fast(cube, block_size, rng):
    y_count, x_count, energy_count = cube.shape
    block_y, block_x = min(block_size, y_count), min(block_size, x_count)
    target = y_count * x_count
    count = int(np.ceil(target / (block_y * block_x)))
    starts = rng.integers(0, [y_count - block_y + 1, x_count - block_x + 1], size=(count, 2))
    ys = starts[:, 0, None, None] + np.arange(block_y)[None, :, None]
    xs = starts[:, 1, None, None] + np.arange(block_x)[None, None, :]
    return cube[ys, xs].reshape(-1, energy_count)[:target]


def _pca_resample(context, rng):
    reference = context["reference"]
    sample = _sample_spatial_blocks_fast if context["fast_sampling"] else _sample_spatial_blocks
    try:
        global_sample = sample(context["full"], context["block_size"], rng)
        roi_sample = sample(context["roi"], context["block_size"], rng)
        model = _fit_randomized_pca_model(global_sample, reference.n_components, rng)
        result = analyze_pca_reconstruction(
            model, context["energy"], roi_sample, reference.requested_peak_count,
            reference.min_peak_distance_meV, reference.min_prominence_fraction,
        )
        if len(result.peaks) < len(reference.peaks):
            return None
        reference_centers = np.asarray([peak.center_meV for peak in reference.peaks])
        candidate_centers = np.asarray([peak.center_meV for peak in result.peaks])
        rows, columns = linear_sum_assignment(np.abs(reference_centers[:, None] - candidate_centers[None, :]))
        matched = [result.peaks[item] for item in columns[np.argsort(rows)]]
        return tuple(np.asarray([getattr(peak, field) for peak in matched])
                     for field in ("center_meV", "width_meV", "prominence"))
    except (FloatingPointError, RuntimeError, ValueError, np.linalg.LinAlgError):
        return None
