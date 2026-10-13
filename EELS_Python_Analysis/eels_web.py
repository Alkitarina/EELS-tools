"""Local browser UI for EELS ROI selection, PCA analysis, peak fitting, and bootstrap."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import socket
import threading
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory

from eelsfit.core import (
    AutoFitResult,
    BootstrapResult,
    FitConfig,
    FitResult,
    automatic_fit_spectrum,
    expand_values,
    fit_spectrum,
    residual_block_bootstrap,
)
from eelsfit.io import EELSData, load_eels_hdf5
from eelsfit.version import __version__ as APP_VERSION
from eelsfit.exporting import RESULT_FORMAT, build_result_document, json_text, software_identity
from eelsfit.importing import prepare_import
from eelsfit.zest_import import VERSION as ZEST_IMPORT_VERSION, normalize_options as zest_options
from eelsfit.multivariate import VERSION as WORKFLOW_VERSION, FEATURE_REVISION as WORKFLOW_REVISION
from eelsfit.workflow_jobs import WorkflowManager
from eelsfit.workflow_api import register_workflow
from eelsfit.workflow_import import IMPORT_VERSION as WORKFLOW_IMPORT_VERSION
from eelsfit.ui_preferences import register_preferences, VERSION as UI_PREFERENCES_VERSION
from eelsfit.interface_workflow import STRIP_DATA_VERSION
from eelsfit.interface_localization import LOCALIZATION_RANGE_VERSION
from eelsfit.workflow_peaks import AMPLITUDE_LIMIT_METHOD, CANDIDATE_SCORE_METHOD
from eelsfit.runtime_identity import ui_source_id
from eelsfit.reference_maps import REFERENCE_MAP_VERSION, build_reference_maps
from eelsfit.region_fit import REGION_FIT_VERSION, FIT_TIMEOUT_SECONDS, run_bounded_fit, validate_thresholds
from eelsfit.pca import (
    PCABootstrapResult,
    PCAModel,
    PCAResult,
    analyze_pca_reconstruction,
    fit_pca_model,
    spatial_block_pca_bootstrap,
)


REGIONS = ("Si", "Interface", "Ge")
RUNTIME_SOFTWARE = software_identity(Path(__file__).resolve().parent)
# Freeze at import: source edits must not relabel an already running backend.
RUNTIME_UI_SOURCE_ID = ui_source_id()

from eelsfit.bootstrap_runtime import BOOTSTRAP_ENGINE, DEFAULT_RESAMPLES, INDEPENDENT_SCHEME, MAX_WORKERS, resolve_workers


class RequestValidationError(ValueError):
    """A request contains an invalid analysis setting."""


@dataclass(frozen=True)
class AutoFitSettings:
    """Inputs needed to construct an automatic multi-start Gaussian model."""

    peak_count: int
    fwhm_lower: np.ndarray
    fwhm_upper: np.ndarray
    include_baseline: bool
    n_starts: int
    seed: int


@dataclass(frozen=True)
class PCASettings:
    """Inputs for non-parametric PCA reconstruction and peak measurement."""

    component_count: int
    peak_count: int
    min_peak_distance_meV: float
    min_prominence_fraction: float
    seed: int


@dataclass(frozen=True)
class ROI:
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def normalized(self) -> "ROI":
        return ROI(
            min(self.x_min, self.x_max),
            max(self.x_min, self.x_max),
            min(self.y_min, self.y_max),
            max(self.y_min, self.y_max),
        )

    def as_dict(self) -> dict[str, float]:
        normalized = self.normalized()
        return {
            "x_min": normalized.x_min,
            "x_max": normalized.x_max,
            "y_min": normalized.y_min,
            "y_max": normalized.y_max,
        }


@dataclass
class RegionAnalysis:
    region: str
    roi: ROI
    pixel_count: int
    config: FitConfig
    result: FitResult
    fit_mode: str = "manual"
    auto_fit: AutoFitResult | None = None
    bootstrap: BootstrapResult | None = None
    recipe: dict[str, Any] = field(default_factory=dict)
    result_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class RegionPCAAnalysis:
    region: str
    roi: ROI
    pixel_count: int
    model_sample_count: int
    settings: PCASettings
    result: PCAResult
    bootstrap: PCABootstrapResult | None = None
    recipe: dict[str, Any] = field(default_factory=dict)
    result_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class BootstrapJob:
    job_id: str
    regions: list[str]
    total: int
    completed: int = 0
    status: str = "queued"
    error: str | None = None
    analysis_type: str = "gaussian"
    workers: int = 1
    backend: str = "process"


def _float_sequence(value: Any, field_name: str) -> np.ndarray:
    if isinstance(value, str):
        values: list[Any] = value.replace(",", " ").replace(";", " ").split()
    elif isinstance(value, (list, tuple, np.ndarray)):
        values = list(value)
    elif value is None:
        values = []
    else:
        values = [value]

    if not values:
        raise RequestValidationError(f"{field_name} is required.")
    try:
        result = np.asarray([float(item) for item in values], dtype=float)
    except (TypeError, ValueError) as error:
        raise RequestValidationError(f"{field_name} must contain numbers.") from error
    if not np.all(np.isfinite(result)):
        raise RequestValidationError(f"{field_name} contains a non-finite value.")
    return result


def _parse_config(payload: dict[str, Any]) -> FitConfig:
    config_payload = payload.get("config")
    if not isinstance(config_payload, dict):
        raise RequestValidationError("config is required.")

    centers = _float_sequence(config_payload.get("centers"), "Initial centers")
    peak_count = centers.size
    return FitConfig(
        centers=centers,
        center_lower=expand_values(
            _float_sequence(config_payload.get("center_lower"), "Center lower"),
            peak_count,
            "Center lower",
        ),
        center_upper=expand_values(
            _float_sequence(config_payload.get("center_upper"), "Center upper"),
            peak_count,
            "Center upper",
        ),
        fwhm_lower=expand_values(
            _float_sequence(config_payload.get("fwhm_lower"), "FWHM lower"),
            peak_count,
            "FWHM lower",
        ),
        fwhm_upper=expand_values(
            _float_sequence(config_payload.get("fwhm_upper"), "FWHM upper"),
            peak_count,
            "FWHM upper",
        ),
        include_baseline=bool(config_payload.get("include_baseline", True)),
    )


def _parse_auto_settings(config_payload: dict[str, Any]) -> AutoFitSettings:
    try:
        peak_count = int(float(config_payload.get("peak_count")))
    except (TypeError, ValueError) as error:
        raise RequestValidationError("Peak count must be an integer.") from error
    if not 1 <= peak_count <= 16:
        raise RequestValidationError("Peak count must be between 1 and 16.")

    try:
        n_starts = int(float(config_payload.get("search_starts", 48)))
    except (TypeError, ValueError) as error:
        raise RequestValidationError("Search starts must be an integer.") from error
    if not 1 <= n_starts <= 256:
        raise RequestValidationError("Search starts must be between 1 and 256.")

    try:
        seed = int(config_payload.get("seed", 20260728))
    except (TypeError, ValueError) as error:
        raise RequestValidationError("Seed must be an integer.") from error

    fwhm_lower = expand_values(
        _float_sequence(config_payload.get("fwhm_lower"), "FWHM lower"),
        peak_count,
        "FWHM lower",
    )
    fwhm_upper = expand_values(
        _float_sequence(config_payload.get("fwhm_upper"), "FWHM upper"),
        peak_count,
        "FWHM upper",
    )
    if np.any(fwhm_lower <= 0.0) or np.any(fwhm_lower >= fwhm_upper):
        raise RequestValidationError(
            "Every FWHM lower bound must be positive and smaller than its upper bound."
        )
    return AutoFitSettings(
        peak_count=peak_count,
        fwhm_lower=fwhm_lower,
        fwhm_upper=fwhm_upper,
        include_baseline=bool(config_payload.get("include_baseline", True)),
        n_starts=n_starts,
        seed=seed,
    )


def _parse_fit_settings(payload: dict[str, Any]) -> FitConfig | AutoFitSettings:
    config_payload = payload.get("config")
    if not isinstance(config_payload, dict):
        raise RequestValidationError("config is required.")
    mode = str(config_payload.get("mode", "manual")).strip().lower()
    if mode == "auto":
        return _parse_auto_settings(config_payload)
    if mode == "manual":
        return _parse_config(payload)
    raise RequestValidationError("Fit mode must be auto or manual.")


def _parse_pca_settings(payload: dict[str, Any]) -> PCASettings:
    config_payload = payload.get("config")
    if not isinstance(config_payload, dict):
        raise RequestValidationError("config is required.")
    if str(config_payload.get("mode", "")).strip().lower() != "pca":
        raise RequestValidationError("PCA analysis requires config mode pca.")

    try:
        component_count = int(float(config_payload.get("component_count")))
        peak_count = int(float(config_payload.get("peak_count")))
    except (TypeError, ValueError) as error:
        raise RequestValidationError(
            "PCA component count and peak count must be integers."
        ) from error
    if not 1 <= component_count <= 64:
        raise RequestValidationError("PCA component count must be between 1 and 64.")
    if not 1 <= peak_count <= 16:
        raise RequestValidationError("Peak count must be between 1 and 16.")

    try:
        min_distance = float(config_payload.get("min_peak_distance_meV", 5.0))
        min_prominence_percent = float(
            config_payload.get("min_prominence_percent", 0.5)
        )
        seed = int(config_payload.get("seed", 20260728))
    except (TypeError, ValueError) as error:
        raise RequestValidationError("PCA settings contain an invalid number.") from error
    if not np.isfinite(min_distance) or min_distance <= 0.0:
        raise RequestValidationError("Minimum peak distance must be positive.")
    if (
        not np.isfinite(min_prominence_percent)
        or not 0.0 <= min_prominence_percent < 100.0
    ):
        raise RequestValidationError(
            "Minimum prominence percent must be between 0 and 100."
        )
    return PCASettings(
        component_count=component_count,
        peak_count=peak_count,
        min_peak_distance_meV=min_distance,
        min_prominence_fraction=min_prominence_percent / 100.0,
        seed=seed,
    )


def _parse_region(payload: dict[str, Any]) -> str:
    region = str(payload.get("region", ""))
    if region not in REGIONS:
        raise RequestValidationError("Choose Si, Interface, or Ge.")
    return region


def _parse_roi(payload: Any, data: EELSData) -> ROI:
    if not isinstance(payload, dict):
        raise RequestValidationError("ROI coordinates are required.")
    try:
        roi = ROI(
            float(payload["x_min"]),
            float(payload["x_max"]),
            float(payload["y_min"]),
            float(payload["y_max"]),
        ).normalized()
    except (KeyError, TypeError, ValueError) as error:
        raise RequestValidationError("ROI needs x_min, x_max, y_min, and y_max.") from error

    clipped = ROI(
        max(float(data.x_nm[0]), roi.x_min),
        min(float(data.x_nm[-1]), roi.x_max),
        max(float(data.y_nm[0]), roi.y_min),
        min(float(data.y_nm[-1]), roi.y_max),
    )
    if clipped.x_min > clipped.x_max or clipped.y_min > clipped.y_max:
        raise RequestValidationError("ROI is outside the available spatial range.")
    return clipped


def _parse_energy_window(payload: dict[str, Any], data: EELSData) -> tuple[float, float]:
    try:
        lower = float(payload["fit_min"])
        upper = float(payload["fit_max"])
    except (KeyError, TypeError, ValueError) as error:
        raise RequestValidationError("fit_min and fit_max are required.") from error

    if lower >= upper:
        raise RequestValidationError("Fit minimum must be smaller than fit maximum.")
    mask = (data.energy_meV >= lower) & (data.energy_meV <= upper)
    if int(mask.sum()) < 8:
        raise RequestValidationError("The fit range contains too few energy channels.")
    return lower, upper


def _parse_block_size(value: Any) -> int | None:
    if value is None or str(value).strip().lower() in {"", "auto"}:
        return None
    try:
        block_size = int(float(value))
    except (TypeError, ValueError) as error:
        raise RequestValidationError("Block size must be a positive integer or auto.") from error
    if block_size < 1:
        raise RequestValidationError("Block size must be at least one.")
    return block_size


class AnalysisService:
    """Own the EELS data and all analysis state for one local server process."""

    def __init__(self, data_path: str | Path | EELSData | None = None) -> None:
        self.data = (data_path if isinstance(data_path, EELSData) else
                     load_eels_hdf5(data_path) if data_path is not None else None)
        self.dataset_id = uuid.uuid4().hex
        self.request_lock = threading.Lock()
        self.region_fit_lock = threading.Lock()
        self.results: dict[str, RegionAnalysis] = {}
        self.pca_results: dict[str, RegionPCAAnalysis] = {}
        self.jobs: dict[str, BootstrapJob] = {}
        self._pca_models: dict[tuple[int, int, int], PCAModel] = {}
        self._reference_maps: dict | None = None
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eels-bootstrap")

    def shutdown(self) -> None:
        if hasattr(self, "workflow"):
            self.workflow.shutdown()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def start_import(self, source: Path, destination: Path, options=None) -> BootstrapJob:
        job = BootstrapJob(uuid.uuid4().hex, [], 1, analysis_type="import")
        with self._lock:
            self.jobs[job.job_id] = job
        self._executor.submit(self._import_worker, job, source, destination, options)
        return job

    def _import_worker(self, job: BootstrapJob, source: Path, destination: Path, options=None) -> None:
        try:
            with self._lock:
                job.status = "running"
            if source.suffix.lower() == ".zip":
                def progress(completed, total):
                    with self._lock:
                        job.completed, job.total = completed, total
                candidate = prepare_import(source, destination, zest_options=options, progress=progress)
            else:
                candidate = prepare_import(source, destination)
            # Commit only a fully validated candidate; failed imports keep old results.
            with self.request_lock, self._lock:
                self.data = candidate
                self.dataset_id = uuid.uuid4().hex
                self.results.clear()
                self.pca_results.clear()
                self._pca_models.clear()
                self._reference_maps = None
                job.completed = job.total
                job.status = "done"
        except Exception as error:
            with self._lock:
                job.error = str(error)
                job.status = "error"

    def default_rois(self) -> dict[str, dict[str, float]]:
        x_min, x_max = float(self.data.x_nm[0]), float(self.data.x_nm[-1])
        y_min, y_max = float(self.data.y_nm[0]), float(self.data.y_nm[-1])
        span = x_max - x_min
        return {
            "Si": ROI(x_min, x_min + span * 0.38, y_min, y_max).as_dict(),
            "Interface": ROI(
                x_min + span * 0.38, x_min + span * 0.62, y_min, y_max
            ).as_dict(),
            "Ge": ROI(x_min + span * 0.62, x_max, y_min, y_max).as_dict(),
        }

    def info(self) -> dict[str, Any]:
        if self.data is None:
            return {"dataset_id": self.dataset_id, "dataset_loaded": False, "source": None,
                    "shape": None, "x_nm": None, "y_nm": None, "energy_meV": None,
                    "zlp_fwhm_meV": None, "default_rois": {},
                    "reference_maps_version": REFERENCE_MAP_VERSION, "region_fit_version": REGION_FIT_VERSION}
        y_count, x_count, energy_count = self.data.shape
        zlp_fwhm = self.data.attributes.get("zlp_fwhm_meV")
        if isinstance(zlp_fwhm, np.ndarray):
            zlp_fwhm = zlp_fwhm.reshape(-1)[0] if zlp_fwhm.size else None
        if isinstance(zlp_fwhm, (list, tuple)):
            zlp_fwhm = zlp_fwhm[0] if zlp_fwhm else None
        if isinstance(zlp_fwhm, np.generic):
            zlp_fwhm = zlp_fwhm.item()
        if zlp_fwhm is not None:
            zlp_fwhm = float(zlp_fwhm)
        return {
            "dataset_id": self.dataset_id,
            "dataset_loaded": True,
            "source": str(self.data.source),
            "shape": {"y": y_count, "x": x_count, "energy": energy_count},
            "input_preparation": self.data.attributes.get("zest_import"),
            "x_nm": [float(self.data.x_nm[0]), float(self.data.x_nm[-1])],
            "y_nm": [float(self.data.y_nm[0]), float(self.data.y_nm[-1])],
            "energy_meV": [
                float(self.data.energy_meV[0]),
                float(self.data.energy_meV[-1]),
            ],
            "zlp_fwhm_meV": zlp_fwhm,
            "default_rois": self.default_rois(),
            "reference_maps_version": REFERENCE_MAP_VERSION,
            "region_fit_version": REGION_FIT_VERSION,
        }

    def reference_maps(self) -> dict:
        if self._reference_maps is None:
            self._reference_maps = build_reference_maps(self.data)
        return {"dataset_id": self.dataset_id, **self._reference_maps}

    def map_data(self, lower: float, upper: float) -> dict[str, Any]:
        lower, upper = _parse_energy_window(
            {"fit_min": lower, "fit_max": upper}, self.data
        )
        mask = (self.data.energy_meV >= lower) & (self.data.energy_meV <= upper)
        values = np.trapezoid(
            self.data.intensity[:, :, mask], self.data.energy_meV[mask], axis=2
        )
        finite = values[np.isfinite(values)]
        if not finite.size:
            raise RequestValidationError("所选能量范围没有可显示的有限信号。")
        display_min, display_max = np.percentile(finite, [2.0, 99.5])
        if display_min == display_max:
            display_max = display_min + 1.0
        return {
            "fit_min": lower,
            "fit_max": upper,
            "x_nm": self.data.x_nm.tolist(),
            "y_nm": self.data.y_nm.tolist(),
            "values": [[float(v) if np.isfinite(v) else None for v in row] for row in values],
            "display_min": float(display_min),
            "display_max": float(display_max),
        }

    def _extract_spectrum(
        self, roi: ROI, lower: float, upper: float
    ) -> tuple[np.ndarray, np.ndarray, int]:
        x_mask = (self.data.x_nm >= roi.x_min) & (self.data.x_nm <= roi.x_max)
        y_mask = (self.data.y_nm >= roi.y_min) & (self.data.y_nm <= roi.y_max)
        if not x_mask.any() or not y_mask.any():
            raise RequestValidationError("ROI does not contain a pixel center.")

        energy_mask = (self.data.energy_meV >= lower) & (
            self.data.energy_meV <= upper
        )
        cube = self.data.intensity[y_mask][:, x_mask, :]
        if not np.any(np.isfinite(cube[:, :, energy_mask])):
            raise RequestValidationError("此 ROI 在所选能窗内没有有效谱线，请选择已计算的像素。")
        spectrum = np.nanmean(cube, axis=(0, 1))
        pixel_count = int(x_mask.sum() * y_mask.sum())
        if "zest_import" in self.data.attributes:
            pixel_count = int(np.count_nonzero(np.all(np.isfinite(cube[:, :, energy_mask]), axis=2)))
        return (
            self.data.energy_meV[energy_mask],
            spectrum[energy_mask],
            pixel_count,
        )

    def _extract_pca_cubes(
        self, roi: ROI, lower: float, upper: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
        x_mask = (self.data.x_nm >= roi.x_min) & (self.data.x_nm <= roi.x_max)
        y_mask = (self.data.y_nm >= roi.y_min) & (self.data.y_nm <= roi.y_max)
        if not x_mask.any() or not y_mask.any():
            raise RequestValidationError("ROI does not contain a pixel center.")
        energy_mask = (self.data.energy_meV >= lower) & (
            self.data.energy_meV <= upper
        )
        full_cube = self.data.intensity[:, :, energy_mask]
        roi_cube = self.data.intensity[y_mask][:, x_mask, :][:, :, energy_mask]
        pixel_count = int(x_mask.sum() * y_mask.sum())
        if "zest_import" in self.data.attributes:
            pixel_count = int(np.count_nonzero(np.all(np.isfinite(roi_cube), axis=2)))
        return (
            self.data.energy_meV[energy_mask],
            full_cube,
            roi_cube,
            energy_mask,
            pixel_count,
        )

    def _cached_pca_model(
        self,
        full_cube: np.ndarray,
        energy_mask: np.ndarray,
        component_count: int,
    ) -> PCAModel:
        indices = np.flatnonzero(energy_mask)
        key = (int(indices[0]), int(indices[-1]), int(component_count))
        with self._lock:
            cached = self._pca_models.get(key)
        if cached is not None:
            return cached
        model = fit_pca_model(
            full_cube.reshape(-1, full_cube.shape[2]),
            n_components=component_count,
        )
        with self._lock:
            self._pca_models[key] = model
        return model

    def _pca_analyze(
        self,
        region: str,
        roi: ROI,
        lower: float,
        upper: float,
        settings: PCASettings,
    ) -> RegionPCAAnalysis:
        energy, full_cube, roi_cube, energy_mask, pixel_count = (
            self._extract_pca_cubes(roi, lower, upper)
        )
        model = self._cached_pca_model(
            full_cube,
            energy_mask,
            settings.component_count,
        )
        result = analyze_pca_reconstruction(
            model,
            energy,
            roi_cube.reshape(-1, roi_cube.shape[2]),
            peak_count=settings.peak_count,
            min_peak_distance_meV=settings.min_peak_distance_meV,
            min_prominence_fraction=settings.min_prominence_fraction,
        )
        return RegionPCAAnalysis(
            region=region,
            roi=roi,
            pixel_count=pixel_count,
            model_sample_count=model.sample_count,
            settings=settings,
            result=result,
            recipe=self._recipe("pca", region, roi, lower, upper, settings),
        )

    def _recipe(self, mode, region, roi, lower, upper, settings) -> dict[str, Any]:
        config = {
            key: value.tolist() if isinstance(value, np.ndarray) else value
            for key, value in asdict(settings).items()
        }
        return {
            "mode": mode, "region": region, "roi": roi.as_dict(),
            "fit_min": lower, "fit_max": upper, "settings": config,
            "dataset_id": self.dataset_id,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "bootstrap": None,
        }

    def _fit(
        self,
        region: str,
        roi: ROI,
        lower: float,
        upper: float,
        settings: FitConfig | AutoFitSettings,
    ) -> RegionAnalysis:
        energy, spectrum, pixel_count = self._extract_spectrum(roi, lower, upper)
        if isinstance(settings, AutoFitSettings):
            automatic = automatic_fit_spectrum(
                energy,
                spectrum,
                n_peaks=settings.peak_count,
                fwhm_lower=settings.fwhm_lower,
                fwhm_upper=settings.fwhm_upper,
                include_baseline=settings.include_baseline,
                n_starts=settings.n_starts,
                seed=settings.seed,
            )
            return RegionAnalysis(
                region=region,
                roi=roi,
                pixel_count=pixel_count,
                config=automatic.config,
                result=automatic.result,
                fit_mode="auto",
                auto_fit=automatic,
                recipe=self._recipe("auto", region, roi, lower, upper, settings),
            )
        return RegionAnalysis(
            region=region,
            roi=roi,
            pixel_count=pixel_count,
            config=settings,
            result=fit_spectrum(energy, spectrum, settings),
            recipe=self._recipe("manual", region, roi, lower, upper, settings),
        )

    def preview_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return the selected ROI's mean raw spectrum without fitting it."""

        region = _parse_region(payload)
        roi = _parse_roi(payload.get("roi"), self.data)
        lower, upper = _parse_energy_window(payload, self.data)
        energy, intensity, pixel_count = self._extract_spectrum(roi, lower, upper)
        return {
            "region": region,
            "roi": roi.as_dict(),
            "pixel_count": pixel_count,
            "energy_meV": energy.tolist(),
            "intensity": intensity.tolist(),
        }

    def fit_from_payload(self, payload: dict[str, Any]) -> RegionAnalysis:
        region = _parse_region(payload)
        settings = _parse_fit_settings(payload)
        roi = _parse_roi(payload.get("roi"), self.data)
        lower, upper = _parse_energy_window(payload, self.data)
        analysis = self._fit(region, roi, lower, upper, settings)
        with self._lock:
            self.results[region] = analysis
        return analysis

    def pca_from_payload(self, payload: dict[str, Any]) -> RegionPCAAnalysis:
        region = _parse_region(payload)
        settings = _parse_pca_settings(payload)
        roi = _parse_roi(payload.get("roi"), self.data)
        lower, upper = _parse_energy_window(payload, self.data)
        analysis = self._pca_analyze(region, roi, lower, upper, settings)
        with self._lock:
            self.pca_results[region] = analysis
        return analysis

    def start_bootstrap(self, payload: dict[str, Any]) -> BootstrapJob:
        regions = payload.get("regions")
        if not isinstance(regions, list) or not regions:
            raise RequestValidationError("At least one region is required.")
        regions = [str(region) for region in regions]
        if any(region not in REGIONS for region in regions):
            raise RequestValidationError("Unknown region in bootstrap request.")

        settings = _parse_fit_settings(payload)
        lower, upper = _parse_energy_window(payload, self.data)
        rois_payload = payload.get("rois")
        if not isinstance(rois_payload, dict):
            raise RequestValidationError("ROIs are required for bootstrap.")
        rois = {
            region: _parse_roi(rois_payload.get(region), self.data)
            for region in regions
        }
        try:
            n_resamples = int(float(payload.get("samples", DEFAULT_RESAMPLES)))
        except (TypeError, ValueError) as error:
            raise RequestValidationError("Bootstrap sample count must be an integer.") from error
        if n_resamples < 2:
            raise RequestValidationError("Use at least two bootstrap samples.")
        block_size = _parse_block_size(payload.get("block_size"))
        workers = resolve_workers(payload.get("workers", 0), n_resamples)
        try:
            seed = int(payload.get("seed", 20260728))
        except (TypeError, ValueError) as error:
            raise RequestValidationError("Seed must be an integer.") from error

        job = BootstrapJob(
            job_id=uuid.uuid4().hex,
            regions=regions,
            total=n_resamples * len(regions),
            workers=workers,
        )
        with self._lock:
            self.jobs[job.job_id] = job
        self._executor.submit(
            self._bootstrap_worker,
            job.job_id,
            rois,
            lower,
            upper,
            settings,
            n_resamples,
            block_size,
            seed,
        )
        return job

    def _bootstrap_worker(
        self,
        job_id: str,
        rois: dict[str, ROI],
        lower: float,
        upper: float,
        settings: FitConfig | AutoFitSettings,
        n_resamples: int,
        block_size: int | None,
        seed: int,
    ) -> None:
        with self._lock:
            job = self.jobs[job_id]
            job.status = "running"

        try:
            for region_index, region in enumerate(job.regions):
                analysis = self._fit(region, rois[region], lower, upper, settings)
                completed_before = region_index * n_resamples

                def report(done: int, _total: int, offset: int = completed_before) -> None:
                    with self._lock:
                        self.jobs[job_id].completed = offset + done

                analysis.bootstrap = residual_block_bootstrap(
                    analysis.result,
                    analysis.config,
                    n_resamples=n_resamples,
                    block_size=block_size,
                    seed=seed + region_index,
                    progress=report,
                    workers=job.workers,
                    rng_scheme=INDEPENDENT_SCHEME,
                )
                analysis.recipe["bootstrap"] = {
                    "method": "residual_block", "n_resamples": n_resamples,
                    "block_size": block_size, "actual_block_size": analysis.bootstrap.block_size,
                    "seed": seed + region_index,
                    "rng_scheme": INDEPENDENT_SCHEME, "workers": job.workers, "blas_threads": 1,
                    "backend": job.backend if job.workers > 1 else "serial",
                }
                with self._lock:
                    self.results[region] = analysis

            with self._lock:
                job = self.jobs[job_id]
                job.completed = job.total
                job.status = "done"
        except Exception as error:
            with self._lock:
                job = self.jobs[job_id]
                job.status = "error"
                job.error = str(error)

    def start_pca_bootstrap(self, payload: dict[str, Any]) -> BootstrapJob:
        regions = payload.get("regions")
        if not isinstance(regions, list) or not regions:
            raise RequestValidationError("At least one region is required.")
        regions = [str(region) for region in regions]
        if any(region not in REGIONS for region in regions):
            raise RequestValidationError("Unknown region in PCA bootstrap request.")

        settings = _parse_pca_settings(payload)
        lower, upper = _parse_energy_window(payload, self.data)
        rois_payload = payload.get("rois")
        if not isinstance(rois_payload, dict):
            raise RequestValidationError("ROIs are required for PCA bootstrap.")
        rois = {
            region: _parse_roi(rois_payload.get(region), self.data)
            for region in regions
        }
        try:
            n_resamples = int(float(payload.get("samples", DEFAULT_RESAMPLES)))
        except (TypeError, ValueError) as error:
            raise RequestValidationError(
                "PCA bootstrap sample count must be an integer."
            ) from error
        if not 2 <= n_resamples <= 5000:
            raise RequestValidationError(
                "PCA bootstrap sample count must be between 2 and 5000."
            )
        block_size = _parse_block_size(payload.get("block_size"))
        if block_size is None:
            block_size = 2
        if block_size > max(self.data.shape[:2]):
            raise RequestValidationError(
                "Spatial block size cannot exceed the spectrum-image dimensions."
            )

        channels = int(np.count_nonzero((self.data.energy_meV >= lower) & (self.data.energy_meV <= upper)))
        workers = resolve_workers(payload.get("workers", 0), n_resamples,
                                  self.data.shape[0] * self.data.shape[1] * channels * 8, backend="thread")

        job = BootstrapJob(
            job_id=uuid.uuid4().hex,
            regions=regions,
            total=n_resamples * len(regions),
            analysis_type="pca",
            workers=workers,
            backend="thread",
        )
        with self._lock:
            self.jobs[job.job_id] = job
        self._executor.submit(
            self._pca_bootstrap_worker,
            job.job_id,
            rois,
            lower,
            upper,
            settings,
            n_resamples,
            block_size,
        )
        return job

    def _pca_bootstrap_worker(
        self,
        job_id: str,
        rois: dict[str, ROI],
        lower: float,
        upper: float,
        settings: PCASettings,
        n_resamples: int,
        block_size: int,
    ) -> None:
        with self._lock:
            job = self.jobs[job_id]
            job.status = "running"

        try:
            for region_index, region in enumerate(job.regions):
                analysis = self._pca_analyze(
                    region, rois[region], lower, upper, settings
                )
                energy, full_cube, roi_cube, _, _ = self._extract_pca_cubes(
                    rois[region], lower, upper
                )
                completed_before = region_index * n_resamples

                def report(done: int, _total: int, offset: int = completed_before) -> None:
                    with self._lock:
                        self.jobs[job_id].completed = offset + done

                analysis.bootstrap = spatial_block_pca_bootstrap(
                    energy,
                    full_cube,
                    roi_cube,
                    analysis.result,
                    n_resamples=n_resamples,
                    block_size_pixels=block_size,
                    seed=settings.seed + region_index,
                    progress=report,
                    workers=job.workers,
                    rng_scheme=INDEPENDENT_SCHEME,
                )
                analysis.recipe["bootstrap"] = {
                    "method": "spatial_block_pca", "n_resamples": n_resamples,
                    "block_size": block_size,
                    "actual_block_size": analysis.bootstrap.block_size_pixels,
                    "seed": settings.seed + region_index,
                    "rng_scheme": INDEPENDENT_SCHEME, "workers": job.workers, "blas_threads": 1,
                    "backend": job.backend if job.workers > 1 else "serial",
                }
                with self._lock:
                    self.pca_results[region] = analysis

            with self._lock:
                job = self.jobs[job_id]
                job.completed = job.total
                job.status = "done"
        except Exception as error:
            with self._lock:
                job = self.jobs[job_id]
                job.status = "error"
                job.error = str(error)

    def job_status(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise RequestValidationError("Bootstrap job was not found.")
            return {
                "job_id": job.job_id,
                "regions": job.regions,
                "status": job.status,
                "completed": job.completed,
                "total": job.total,
                "error": job.error,
                "analysis_type": job.analysis_type,
                "workers": job.workers,
                "backend": job.backend,
            }

    def serialized_results(self) -> dict[str, Any]:
        with self._lock:
            return {
                region: _serialize_analysis(analysis)
                for region, analysis in self.results.items()
            }

    def serialized_pca_results(self) -> dict[str, Any]:
        with self._lock:
            return {
                region: _serialize_pca_analysis(analysis)
                for region, analysis in self.pca_results.items()
            }

    def csv_export(self) -> bytes:
        with self._lock:
            analyses = list(self.results.values())
        if not analyses:
            raise RequestValidationError("Fit at least one region before exporting.")

        fieldnames = [
            "region",
            "fit_mode",
            "auto_starts_requested",
            "auto_starts_successful",
            "peak_index",
            "pixel_count",
            "roi_x_min_nm",
            "roi_x_max_nm",
            "roi_y_min_nm",
            "roi_y_max_nm",
            "center_meV",
            "center_ci95_lower_meV",
            "center_ci95_upper_meV",
            "fwhm_meV",
            "fwhm_ci95_lower_meV",
            "fwhm_ci95_upper_meV",
            "amplitude",
            "area",
            "area_ci95_lower",
            "area_ci95_upper",
            "fit_rmse",
            "fit_success",
            "bootstrap_successful",
            "bootstrap_requested",
            "bootstrap_block_size_channels",
        ]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for analysis in analyses:
            result = analysis.result
            if analysis.bootstrap is not None:
                center_low, center_high = analysis.bootstrap.center_ci95
                fwhm_low, fwhm_high = analysis.bootstrap.fwhm_ci95
                area_low, area_high = analysis.bootstrap.area_ci95
                successful = analysis.bootstrap.n_success
                requested = analysis.bootstrap.n_requested
                block = analysis.bootstrap.block_size
            else:
                center_low = center_high = np.full(result.centers_meV.shape, np.nan)
                fwhm_low = fwhm_high = np.full(result.fwhm_meV.shape, np.nan)
                area_low = area_high = np.full(result.areas.shape, np.nan)
                successful = requested = block = np.nan

            roi = analysis.roi.as_dict()
            auto_requested = (
                analysis.auto_fit.n_starts_requested
                if analysis.auto_fit is not None
                else np.nan
            )
            auto_successful = (
                analysis.auto_fit.n_starts_successful
                if analysis.auto_fit is not None
                else np.nan
            )
            for index in range(result.centers_meV.size):
                writer.writerow(
                    {
                        "region": analysis.region,
                        "fit_mode": analysis.fit_mode,
                        "auto_starts_requested": auto_requested,
                        "auto_starts_successful": auto_successful,
                        "peak_index": index + 1,
                        "pixel_count": analysis.pixel_count,
                        "roi_x_min_nm": roi["x_min"],
                        "roi_x_max_nm": roi["x_max"],
                        "roi_y_min_nm": roi["y_min"],
                        "roi_y_max_nm": roi["y_max"],
                        "center_meV": result.centers_meV[index],
                        "center_ci95_lower_meV": center_low[index],
                        "center_ci95_upper_meV": center_high[index],
                        "fwhm_meV": result.fwhm_meV[index],
                        "fwhm_ci95_lower_meV": fwhm_low[index],
                        "fwhm_ci95_upper_meV": fwhm_high[index],
                        "amplitude": result.amplitudes[index],
                        "area": result.areas[index],
                        "area_ci95_lower": area_low[index],
                        "area_ci95_upper": area_high[index],
                        "fit_rmse": result.rmse,
                        "fit_success": result.success,
                        "bootstrap_successful": successful,
                        "bootstrap_requested": requested,
                        "bootstrap_block_size_channels": block,
                    }
                )
        return stream.getvalue().encode("utf-8")

    def pca_csv_export(self) -> bytes:
        with self._lock:
            analyses = list(self.pca_results.values())
        if not analyses:
            raise RequestValidationError(
                "Run PCA peak analysis for at least one region before exporting."
            )

        fieldnames = [
            "region",
            "analysis_type",
            "peak_index",
            "pixel_count",
            "pca_training_spectra",
            "pca_component_count",
            "pca_retained_variance_percent",
            "requested_peak_count",
            "detected_peak_count",
            "min_peak_distance_meV",
            "min_prominence_percent",
            "roi_x_min_nm",
            "roi_x_max_nm",
            "roi_y_min_nm",
            "roi_y_max_nm",
            "center_meV",
            "center_ci95_lower_meV",
            "center_ci95_upper_meV",
            "half_prominence_width_meV",
            "width_ci95_lower_meV",
            "width_ci95_upper_meV",
            "prominence",
            "prominence_ci95_lower",
            "prominence_ci95_upper",
            "left_half_prominence_meV",
            "right_half_prominence_meV",
            "pca_reconstruction_rmse",
            "bootstrap_successful",
            "bootstrap_requested",
            "bootstrap_block_size_pixels",
        ]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for analysis in analyses:
            result = analysis.result
            bootstrap = analysis.bootstrap
            if bootstrap is not None:
                center_low, center_high = bootstrap.center_ci95
                width_low, width_high = bootstrap.width_ci95
                prominence_low, prominence_high = bootstrap.prominence_ci95
                successful = bootstrap.n_success
                requested = bootstrap.n_requested
                block_size = bootstrap.block_size_pixels
            else:
                shape = (result.detected_peak_count,)
                center_low = center_high = np.full(shape, np.nan)
                width_low = width_high = np.full(shape, np.nan)
                prominence_low = prominence_high = np.full(shape, np.nan)
                successful = requested = block_size = np.nan

            roi = analysis.roi.as_dict()
            for index, peak in enumerate(result.peaks):
                writer.writerow(
                    {
                        "region": analysis.region,
                        "analysis_type": "pca_nonparametric",
                        "peak_index": index + 1,
                        "pixel_count": analysis.pixel_count,
                        "pca_training_spectra": analysis.model_sample_count,
                        "pca_component_count": result.n_components,
                        "pca_retained_variance_percent": (
                            result.retained_variance_ratio * 100.0
                        ),
                        "requested_peak_count": result.requested_peak_count,
                        "detected_peak_count": result.detected_peak_count,
                        "min_peak_distance_meV": result.min_peak_distance_meV,
                        "min_prominence_percent": (
                            result.min_prominence_fraction * 100.0
                        ),
                        "roi_x_min_nm": roi["x_min"],
                        "roi_x_max_nm": roi["x_max"],
                        "roi_y_min_nm": roi["y_min"],
                        "roi_y_max_nm": roi["y_max"],
                        "center_meV": peak.center_meV,
                        "center_ci95_lower_meV": center_low[index],
                        "center_ci95_upper_meV": center_high[index],
                        "half_prominence_width_meV": peak.width_meV,
                        "width_ci95_lower_meV": width_low[index],
                        "width_ci95_upper_meV": width_high[index],
                        "prominence": peak.prominence,
                        "prominence_ci95_lower": prominence_low[index],
                        "prominence_ci95_upper": prominence_high[index],
                        "left_half_prominence_meV": peak.left_meV,
                        "right_half_prominence_meV": peak.right_meV,
                        "pca_reconstruction_rmse": result.reconstruction_rmse,
                        "bootstrap_successful": successful,
                        "bootstrap_requested": requested,
                        "bootstrap_block_size_pixels": block_size,
                    }
                )
        return stream.getvalue().encode("utf-8")


def _serialize_analysis(analysis: RegionAnalysis) -> dict[str, Any]:
    result = analysis.result
    bootstrap = analysis.bootstrap
    if bootstrap is not None:
        center_low, center_high = bootstrap.center_ci95
        fwhm_low, fwhm_high = bootstrap.fwhm_ci95
        area_low, area_high = bootstrap.area_ci95
    else:
        center_low = center_high = None
        fwhm_low = fwhm_high = None
        area_low = area_high = None

    peaks = []
    for index, center in enumerate(result.centers_meV):
        peaks.append(
            {
                "index": index + 1,
                "center_meV": float(center),
                "center_ci95": (
                    [float(center_low[index]), float(center_high[index])]
                    if bootstrap is not None
                    else None
                ),
                "fwhm_meV": float(result.fwhm_meV[index]),
                "fwhm_ci95": (
                    [float(fwhm_low[index]), float(fwhm_high[index])]
                    if bootstrap is not None
                    else None
                ),
                "amplitude": float(result.amplitudes[index]),
                "area": float(result.areas[index]),
                "area_ci95": (
                    [float(area_low[index]), float(area_high[index])]
                    if bootstrap is not None
                    else None
                ),
            }
        )
    return {
        "region": analysis.region,
        "result_id": analysis.result_id,
        "recipe": analysis.recipe,
        "fit_mode": analysis.fit_mode,
        "auto_search": (
            {
                "starts_requested": analysis.auto_fit.n_starts_requested,
                "starts_successful": analysis.auto_fit.n_starts_successful,
            }
            if analysis.auto_fit is not None
            else None
        ),
        "roi": analysis.roi.as_dict(),
        "pixel_count": analysis.pixel_count,
        "fit_success": result.success,
        "fit_message": result.message,
        "rmse": result.rmse,
        "energy_meV": result.energy_meV.tolist(),
        "intensity": result.intensity.tolist(),
        "fitted": result.fitted.tolist(),
        "baseline": result.baseline.tolist(),
        "components": result.components.tolist(),
        "residual": result.residual.tolist(),
        "peaks": peaks,
        "bootstrap": (
            {
                "successful": bootstrap.n_success,
                "requested": bootstrap.n_requested,
                "failed": bootstrap.n_failed,
                "block_size": bootstrap.block_size,
            }
            if bootstrap is not None
            else None
        ),
    }


def _serialize_pca_analysis(analysis: RegionPCAAnalysis) -> dict[str, Any]:
    result = analysis.result
    bootstrap = analysis.bootstrap
    if bootstrap is not None:
        center_low, center_high = bootstrap.center_ci95
        width_low, width_high = bootstrap.width_ci95
        prominence_low, prominence_high = bootstrap.prominence_ci95
    else:
        center_low = center_high = None
        width_low = width_high = None
        prominence_low = prominence_high = None

    peaks = []
    for index, peak in enumerate(result.peaks):
        peaks.append(
            {
                "index": index + 1,
                "center_meV": peak.center_meV,
                "center_ci95": (
                    [float(center_low[index]), float(center_high[index])]
                    if bootstrap is not None
                    else None
                ),
                "width_meV": peak.width_meV,
                "width_ci95": (
                    [float(width_low[index]), float(width_high[index])]
                    if bootstrap is not None
                    else None
                ),
                "prominence": peak.prominence,
                "prominence_ci95": (
                    [float(prominence_low[index]), float(prominence_high[index])]
                    if bootstrap is not None
                    else None
                ),
                "height": peak.height,
                "width_height": peak.width_height,
                "left_meV": peak.left_meV,
                "right_meV": peak.right_meV,
            }
        )
    return {
        "analysis_type": "pca",
        "result_id": analysis.result_id,
        "recipe": analysis.recipe,
        "region": analysis.region,
        "roi": analysis.roi.as_dict(),
        "pixel_count": analysis.pixel_count,
        "pca_training_spectra": analysis.model_sample_count,
        "component_count": result.n_components,
        "explained_variance_ratio": result.explained_variance_ratio.tolist(),
        "retained_variance_ratio": result.retained_variance_ratio,
        "reconstruction_rmse": result.reconstruction_rmse,
        "requested_peak_count": result.requested_peak_count,
        "detected_peak_count": result.detected_peak_count,
        "unresolved_peak_count": result.unresolved_peak_count,
        "min_peak_distance_meV": result.min_peak_distance_meV,
        "min_prominence_percent": result.min_prominence_fraction * 100.0,
        "energy_meV": result.energy_meV.tolist(),
        "intensity": result.intensity.tolist(),
        "reconstructed": result.reconstructed.tolist(),
        "peaks": peaks,
        "bootstrap": (
            {
                "successful": bootstrap.n_success,
                "requested": bootstrap.n_requested,
                "failed": bootstrap.n_failed,
                "block_size_pixels": bootstrap.block_size_pixels,
            }
            if bootstrap is not None
            else None
        ),
    }


def _request_json() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise RequestValidationError("Request body must be JSON.")
    return payload


def create_app(data_path: str | Path | None = None) -> Flask:
    root = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        static_folder=str(root / "web" / "static"),
        static_url_path="/static",
    )
    service = AnalysisService(data_path)
    app.extensions["eels_service"] = service
    workflow = WorkflowManager(service)
    service.workflow = workflow
    app.extensions["eels_workflow"] = workflow
    register_workflow(app, service, workflow)
    register_preferences(app, root)
    instance_id = uuid.uuid4().hex
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024
    app.config["IMPORT_ROOT"] = root / "imports"
    app.config["REGION_FIT_TIMEOUT_SECONDS"] = FIT_TIMEOUT_SECONDS

    @app.get("/health")
    def health():
        with service._lock:
            active_jobs = sum(job.status in {"queued", "running"} for job in service.jobs.values())
            source = str(service.data.source) if service.data is not None else None
        return jsonify(
            status="ok", application="eels-python-analysis", app_version=APP_VERSION, launcher_protocol=1,
            empty_startup_version="deferred_data_v1", dataset_loaded=source is not None,
            zest_import_version=ZEST_IMPORT_VERSION,
            result_format=RESULT_FORMAT,
            bootstrap_engine=BOOTSTRAP_ENGINE,
            reference_maps_version=REFERENCE_MAP_VERSION,
            region_fit_version=REGION_FIT_VERSION,
            workflow_version=WORKFLOW_VERSION,
            workflow_import_version=WORKFLOW_IMPORT_VERSION,
            workflow_revision=WORKFLOW_REVISION,
            ui_preferences_version=UI_PREFERENCES_VERSION,
            strip_data_version=STRIP_DATA_VERSION,
            localization_range_version=LOCALIZATION_RANGE_VERSION,
            amplitude_limit_version=AMPLITUDE_LIMIT_METHOD,
            candidate_score_version=CANDIDATE_SCORE_METHOD,
            ui_source_id=RUNTIME_UI_SOURCE_ID,
            project_root=str(root), pid=os.getpid(), instance_id=instance_id,
            source=source, active_jobs=active_jobs,
            busy=active_jobs > 0 or workflow.busy or service.request_lock.locked() or service.region_fit_lock.locked(),
        )

    @app.before_request
    def guard_dataset():
        if not request.path.startswith("/api/"):
            return None
        if request.host.split(":", 1)[0].lower() not in {"127.0.0.1", "localhost"}:
            return jsonify(error="只接受本机地址访问。"), 403
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin and origin != request.host_url.rstrip("/"):
            return jsonify(error="只允许本地页面发起此操作。"), 403
        if request.path == "/api/ui-preferences":
            if request.method == "POST" and service.data is None:
                return jsonify(error="请先导入数据，再保存参数；原参数记忆保持不变。"), 409
            return None  # UI drafts never mutate the dataset or completed analyses.
        if request.path.startswith("/api/jobs/"):
            return None
        if request.path.startswith("/api/workflow/jobs/"):
            return None
        if not service.request_lock.acquire(blocking=False):
            return jsonify(error="服务正在处理另一个请求，请稍后重试。"), 409
        g.eels_request_locked = True
        expected = request.headers.get("X-EELS-Dataset")
        if expected and expected != service.dataset_id and request.path != "/api/info":
            return jsonify(error="数据已在另一页面切换，请刷新页面后重试。"), 409
        with service._lock:
            active = [job for job in service.jobs.values() if job.status in {"queued", "running"}]
        if active and request.path != "/api/info":
            if request.method == "POST" or any(job.analysis_type == "import" for job in active):
                return jsonify(error="导入或不确定度任务尚未结束，请等待完成。"), 409
        if workflow.busy and request.method == "POST":
            return jsonify(error="多变量工作流正在计算，请等待或取消后再修改数据和运行分析。"), 409
        if service.data is None and request.path not in {
                "/api/info", "/api/import", "/api/workflow/import", "/api/workflow/state",
                "/api/results", "/api/pca/results"}:
            return jsonify(error="尚未导入数据，请先选择 MAT / H5 / EELS-ZEST ZIP 数据或流程 JSON。"), 409

    @app.teardown_request
    def release_dataset(_error):
        if g.pop("eels_request_locked", False):
            service.request_lock.release()

    @app.errorhandler(413)
    def upload_too_large(_error):
        return jsonify(error="文件超过 1 GB，请改用本地文件路径导入。"), 413

    @app.post("/api/import")
    def import_data():
        try:
            folder = Path(app.config["IMPORT_ROOT"]) / uuid.uuid4().hex
            upload = request.files.get("file")
            options = None
            if upload is not None:
                name = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
                if Path(name).suffix.lower() not in {".mat", ".h5", ".hdf5", ".zip"}:
                    raise ValueError("只支持 MAT、HDF5 和 EELS-ZEST ZIP 文件。")
                if Path(name).suffix.lower() == ".zip":
                    options = zest_options(json.loads(request.form.get("zest_options", "{}")))
                folder.mkdir(parents=True)
                source = folder / name
                upload.save(source)
            else:
                payload = _request_json()
                path = str(payload.get("path", "")).strip().strip('"')
                if not path:
                    raise ValueError("请选择文件或填写本地绝对路径。")
                source = Path(path)
                if not source.is_absolute() or not source.is_file():
                    raise ValueError("请输入存在的本地文件的绝对路径。")
                if source.suffix.lower() not in {".mat", ".h5", ".hdf5", ".zip"}:
                    raise ValueError("只支持 MAT、HDF5 和 EELS-ZEST ZIP 文件。")
                if source.suffix.lower() == ".zip":
                    options = zest_options(payload.get("zest_options"))
            job = service.start_import(source, folder / (source.stem + ".h5"), options)
            return jsonify(job_id=job.job_id, status=job.status)
        except (ValueError, OSError) as error:
            return jsonify(error=str(error)), 400

    @app.post("/api/export")
    def export_results_json():
        try:
            selected = _request_json().get("results")
            if not isinstance(selected, list) or not selected:
                raise ValueError("尚无可导出的结果，请先运行区域分析。")
            analyses = []
            seen = set()
            for item in selected:
                if not isinstance(item, dict) or item.get("analysis_type") not in {"pca", "gaussian"}:
                    raise ValueError("无效的结果选择。")
                collection = service.pca_results if item["analysis_type"] == "pca" else service.results
                analysis = collection.get(item.get("region"))
                if analysis is None or analysis.result_id != item.get("result_id"):
                    raise ValueError("结果已更新或不属于当前数据，请重新分析后导出。")
                if analysis.result_id not in seen:
                    analyses.append(analysis)
                    seen.add(analysis.result_id)
            serialized = [
                _serialize_pca_analysis(a) if isinstance(a, RegionPCAAnalysis) else _serialize_analysis(a)
                for a in analyses
            ]
            document = build_result_document(service.data, analyses, serialized, RUNTIME_SOFTWARE)
            return send_file(
                io.BytesIO((json_text(document) + "\n").encode("utf-8")),
                mimetype="application/json",
                as_attachment=True,
                download_name=service.data.source.with_suffix(".json").name,
            )
        except (ValueError, OSError) as error:
            return jsonify(error=str(error)), 400

    @app.get("/")
    def index() -> Response:
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/info")
    def info() -> Response:
        return jsonify({**service.info(), "app_version": APP_VERSION, "bootstrap_engine": BOOTSTRAP_ENGINE, "workflow_version": WORKFLOW_VERSION,
                        "zest_import_version": ZEST_IMPORT_VERSION,
                        "workflow_import_version": WORKFLOW_IMPORT_VERSION,
                        "workflow_revision": WORKFLOW_REVISION,
                        "ui_preferences_version": UI_PREFERENCES_VERSION,
                        "strip_data_version": STRIP_DATA_VERSION,
                        "localization_range_version": LOCALIZATION_RANGE_VERSION,
                        "amplitude_limit_version": AMPLITUDE_LIMIT_METHOD,
                        "candidate_score_version": CANDIDATE_SCORE_METHOD,
                        "max_workers": MAX_WORKERS})

    @app.get("/api/map")
    def map_data() -> Response:
        try:
            lower = float(request.args.get("fit_min", "0"))
            upper = float(request.args.get("fit_max", "120"))
            return jsonify(service.map_data(lower, upper))
        except (ValueError, RequestValidationError) as error:
            return jsonify({"error": str(error)}), 400

    @app.get("/api/reference-maps")
    def reference_maps() -> Response:
        return jsonify(service.reference_maps())

    @app.post("/api/region-fit")
    def region_fit() -> Response:
        # Snapshot only a small profile. The bounded child cannot mutate data/results.
        try:
            payload = _request_json()
            thresholds = validate_thresholds(payload.get("thresholds", {}))
            source = payload.get("source", "total_counts")
            if source == "total_counts":
                values = service.data.total_counts
                if values is None:
                    raise ValueError(service.data.attributes.get("total_counts_error", "没有保留的总计数图；请导入含 info.totint 的 H5/MAT。"))
            elif source == "spectral_contribution":
                entry = (service._reference_maps or {}).get("maps", {}).get(source, {})
                if not entry.get("available"):
                    raise ValueError(entry.get("reason", "参考谱贡献图尚未就绪；请先在参考底图中加载它。"))
                values = np.asarray(entry["values"], dtype=float)
            else:
                raise ValueError("请选择总计数或参考谱贡献作为区域拟合来源。")
            finite = np.isfinite(values)
            count = finite.sum(axis=0)
            profile = np.divide(np.where(finite, values, 0).sum(axis=0), count,
                                out=np.full(values.shape[1], np.nan), where=count > 0)
            inputs = {"x_nm": service.data.x_nm.tolist(), "y_nm": service.data.y_nm.tolist(),
                      "profile": [float(v) if np.isfinite(v) else None for v in profile],
                      "thresholds": thresholds}
            dataset_id = service.dataset_id
            if not service.region_fit_lock.acquire(blocking=False):
                return jsonify(error="已有区域拟合正在结束，请稍后重试；其他操作仍可使用。"), 409
            try:
                # The profile is now independent of the live dataset. Keep other UI
                # requests usable, and identify late replies with the snapshot ID.
                service.request_lock.release()
                g.eels_request_locked = False
                result = run_bounded_fit(inputs, timeout_seconds=app.config["REGION_FIT_TIMEOUT_SECONDS"])
                return jsonify({"dataset_id": dataset_id, "source": source, **result})
            finally:
                service.region_fit_lock.release()
        except TimeoutError as error:
            return jsonify(error=str(error)), 504
        except (ValueError, TypeError) as error:
            return jsonify(error=str(error)), 400
        except Exception:
            app.logger.exception("Contrast region fit failed")
            return jsonify(error="区域拟合发生错误；原选框保持不变，请重试。"), 500

    @app.post("/api/fit")
    def fit() -> Response:
        try:
            analysis = service.fit_from_payload(_request_json())
            return jsonify(_serialize_analysis(analysis))
        except (ValueError, RequestValidationError) as error:
            return jsonify({"error": str(error)}), 400

    @app.post("/api/pca")
    def pca() -> Response:
        try:
            analysis = service.pca_from_payload(_request_json())
            return jsonify(_serialize_pca_analysis(analysis))
        except (ValueError, RequestValidationError) as error:
            return jsonify({"error": str(error)}), 400

    @app.post("/api/preview")
    def preview() -> Response:
        try:
            return jsonify(service.preview_from_payload(_request_json()))
        except (ValueError, RequestValidationError) as error:
            return jsonify({"error": str(error)}), 400

    @app.post("/api/bootstrap")
    def bootstrap() -> Response:
        try:
            job = service.start_bootstrap(_request_json())
            return jsonify(
                {
                    "job_id": job.job_id,
                    "status": job.status,
                    "workers": job.workers,
                    "backend": job.backend,
                    "completed": job.completed,
                    "total": job.total,
                }
            )
        except (ValueError, RequestValidationError) as error:
            return jsonify({"error": str(error)}), 400

    @app.post("/api/pca/bootstrap")
    def pca_bootstrap() -> Response:
        try:
            job = service.start_pca_bootstrap(_request_json())
            return jsonify(
                {
                    "job_id": job.job_id,
                    "status": job.status,
                    "completed": job.completed,
                    "total": job.total,
                    "analysis_type": job.analysis_type,
                    "workers": job.workers,
                    "backend": job.backend,
                }
            )
        except (ValueError, RequestValidationError) as error:
            return jsonify({"error": str(error)}), 400

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str) -> Response:
        try:
            return jsonify(service.job_status(job_id))
        except RequestValidationError as error:
            return jsonify({"error": str(error)}), 404

    @app.get("/api/results")
    def results() -> Response:
        return jsonify(service.serialized_results())

    @app.get("/api/pca/results")
    def pca_results() -> Response:
        return jsonify(service.serialized_pca_results())

    @app.get("/api/export.csv")
    def export_csv() -> Response:
        try:
            contents = service.csv_export()
            return Response(
                contents,
                mimetype="text/csv",
                headers={
                    "Content-Disposition": "attachment; filename=eels_peak_fit_results.csv"
                },
            )
        except RequestValidationError as error:
            return jsonify({"error": str(error)}), 400

    @app.get("/api/pca/export.csv")
    def export_pca_csv() -> Response:
        try:
            contents = service.pca_csv_export()
            return Response(
                contents,
                mimetype="text/csv",
                headers={
                    "Content-Disposition": (
                        "attachment; filename=eels_pca_peak_results.csv"
                    )
                },
            )
        except RequestValidationError as error:
            return jsonify({"error": str(error)}), 400

    return app


def _port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            connection.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the local browser-based EELS peak fitting UI."
    )
    parser.add_argument(
        "hdf5_file",
        nargs="?",
        default=None,
        help="Optional HDF5 file; omit to open an empty UI and import data later.",
    )
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--open", action="store_true", help="Open the browser automatically.")
    arguments = parser.parse_args()

    data_path = Path(arguments.hdf5_file).expanduser().resolve() if arguments.hdf5_file else None
    if data_path is not None and not data_path.is_file():
        parser.error(f"HDF5 data file not found: {data_path}")
    if not 1 <= arguments.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    if not _port_is_available(arguments.port):
        parser.error(
            f"Port {arguments.port} is already in use. Choose another port with --port."
        )

    app = create_app(data_path)
    url = f"http://127.0.0.1:{arguments.port}"
    if arguments.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"Open {url} in a browser. Press Ctrl+C here to stop the server.")
    app.run(
        host="127.0.0.1",
        port=arguments.port,
        debug=False,
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
