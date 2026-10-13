"""Compact JSON results and provenance, without input copies or bootstrap draws."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

import numpy as np

RESULT_FORMAT = "eels_peak_results_v1"
CHECKSUM_SCOPE = "eels_numeric_arrays_v1"
ALGORITHM_ID = "eels_peak_analysis_v1"
SOURCE_FILES = ("eels_web.py", "eelsfit/core.py", "eelsfit/pca.py", "eelsfit/bootstrap_runtime.py", "eelsfit/io.py", "eelsfit/exporting.py", "reproduce.py")


def plain(value):
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def json_text(value) -> str:
    return json.dumps(plain(value), ensure_ascii=False, indent=2, allow_nan=False)


def software_identity(root: Path) -> dict:
    """Capture at server import time so later source edits cannot relabel a run."""
    return {
        "algorithm_id": ALGORITHM_ID,
        "source_hash_scope": "sha256_utf8_lf_v1",
        "source_sha256": {
            name: hashlib.sha256((root / name).read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest()
            for name in SOURCE_FILES
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {name: version(name) for name in ("numpy", "scipy", "h5py", "Flask", "threadpoolctl")},
        "random_generator": type(np.random.default_rng(0).bit_generator).__name__,
    }


def data_checksum(data) -> dict:
    """Hash canonical numeric arrays, independent of HDF5 storage and metadata."""
    digest = hashlib.sha256()
    digest.update((CHECKSUM_SCOPE + "\n").encode("ascii"))
    for name in ("x_nm", "y_nm", "energy_meV", "intensity"):
        array = np.asarray(getattr(data, name))
        header = json.dumps({"name": name, "shape": list(array.shape)}, separators=(",", ":"))
        digest.update((header + "\n").encode("ascii"))
        # Follow logical C order even for MATLAB-transposed arrays.
        iterator = np.nditer(array, flags=["external_loop", "buffered"], order="C",
                             op_flags=["readonly"], op_dtypes=[np.dtype("<f8")], buffersize=131072)
        for chunk in iterator:
            values = np.array(chunk, dtype="<f8", copy=True)
            values[np.isnan(values)] = np.nan
            digest.update(values.tobytes(order="C"))
    return {"algorithm": "sha256", "scope": CHECKSUM_SCOPE, "value": digest.hexdigest()}


def summarize_analysis(analysis, serialized: dict) -> dict:
    mode = analysis.recipe["mode"]
    pca = mode == "pca"
    bootstrap = analysis.bootstrap
    widths = (bootstrap.widths_meV if pca else bootstrap.fwhm_meV) if bootstrap else None

    def std(samples, index):
        return float(np.std(samples[:, index], ddof=1)) if bootstrap and bootstrap.n_success > 1 else None

    peaks = []
    for index, peak in enumerate(serialized["peaks"]):
        peaks.append({
            "index": index + 1,
            "center_meV": peak["center_meV"],
            "center_std_meV": std(bootstrap.centers_meV, index) if bootstrap else None,
            "center_ci95": peak["center_ci95"],
            "width_kind": "half_prominence" if pca else "gaussian_fwhm",
            "width_meV": peak["width_meV" if pca else "fwhm_meV"],
            "width_std_meV": std(widths, index),
            "width_ci95": peak["width_ci95" if pca else "fwhm_ci95"],
            "prominence": peak.get("prominence"),
            "prominence_std": std(bootstrap.prominences, index) if bootstrap and pca else None,
            "prominence_ci95": peak.get("prominence_ci95"),
            "amplitude": peak.get("amplitude"),
            "amplitude_std": std(bootstrap.amplitudes, index) if bootstrap and not pca else None,
            "amplitude_ci95": np.percentile(bootstrap.amplitudes[:, index], [2.5, 97.5]).tolist() if bootstrap and not pca else None,
            "area": peak.get("area"),
            "area_std": std(bootstrap.areas, index) if bootstrap and not pca else None,
            "area_ci95": peak.get("area_ci95"),
        })
    return {
        "result_id": analysis.result_id, "region": analysis.region, "mode": mode,
        "pixel_count": analysis.pixel_count,
        "requested_peak_count": serialized.get("requested_peak_count", len(peaks)),
        "detected_peak_count": len(peaks),
        "unresolved_peak_count": serialized.get("unresolved_peak_count", 0),
        "fit_success": serialized.get("fit_success", True),
        "fit_message": serialized.get("fit_message"),
        "rmse": serialized["reconstruction_rmse" if pca else "rmse"],
        "pca": {
            "component_count": serialized["component_count"],
            "training_spectra": serialized["pca_training_spectra"],
            "retained_variance_ratio": serialized["retained_variance_ratio"],
            "explained_variance_ratio": serialized["explained_variance_ratio"],
        } if pca else None,
        "peaks": peaks,
        "uncertainty": {
            "status": "computed" if bootstrap else "not_computed",
            "method": "spatial_block_pca" if pca else "residual_block",
            "n_requested": bootstrap.n_requested if bootstrap else 0,
            "n_success": bootstrap.n_success if bootstrap else 0,
            "n_failed": bootstrap.n_failed if bootstrap else 0,
            "ci_percentiles": [2.5, 97.5], "std_ddof": 1,
        },
    }


def build_result_document(data, analyses, serialized_results, software: dict) -> dict:
    if not analyses or len(analyses) != len(serialized_results):
        raise ValueError("A nonempty, matching set of analyses is required.")
    recipes = []
    for analysis in analyses:
        recipes.append({
            "result_id": analysis.result_id, **analysis.recipe,
            "resolved_fit_config": plain(asdict(analysis.config)) if analysis.recipe["mode"] != "pca" else None,
        })
    return plain({
        "format": RESULT_FORMAT,
        "exported_utc": datetime.now(timezone.utc).isoformat(),
        "units": {"position": "nm", "energy": "meV", "intensity": "a.u. (unchanged input)"},
        "results": [summarize_analysis(a, r) for a, r in zip(analyses, serialized_results)],
        # Keep replay metadata at the end of the human-readable JSON document.
        "reproducibility": {
            "data": {
                "source": str(data.source),
                "original_source": data.attributes.get("original_source", data.attributes.get("source", str(data.source))),
                "shape": list(data.shape), "axis_order": "y,x,energy",
                "checksum": data_checksum(data),
                **({"input_preparation": data.attributes["zest_import"]} if "zest_import" in data.attributes else {}),
            },
            "software": software,
            "notes": [
                "复现需要保留同一份数值数据和对应版本程序。本文件不包含数据副本、源码、谱线或逐次抽样结果。",
                "PCA 宽度为半突出度宽度，高斯宽度为 FWHM；两者定义不同。",
                "不确定度仅基于成功自助样本，不包括能量校准或前期预处理的全部系统误差。",
                "固定 seed 仍需相同随机数调用过程和计算环境；不承诺跨平台逐位一致。",
            ],
            "analyses": recipes,
        },
    })
