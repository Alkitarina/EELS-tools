"""Reusable, verified PCA data restoration for local EELS exports.

Run with the existing EELS_Python_Analysis/.venv Python, using -B -X utf8.
Input JSON/H5 and the analysis application are read only. No Gaussian refitting.
"""

from __future__ import annotations

import argparse
import csv
import copy
import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MultipleLocator
from matplotlib.transforms import ScaledTranslation
import numpy as np
from threadpoolctl import threadpool_limits


ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "EELS_Python_Analysis"))
from eelsfit.exporting import data_checksum
from eelsfit.io import load_eels_hdf5
from eelsfit.pca import analyze_pca_reconstruction, fit_pca_model
from uncertainty import complete_missing


REGIONS = ("Si", "Ge", "Interface")
CURRENT_COLORS = {0.0: "#327C83", 4.0: "#4787A3", 5.5: "#6DAB72", 6.4: "#C3A843", 7.0: "#D38559"}
PEAK_COLORS = ("#5CAACB", "#D5AB36", "#CC7786")
PEAK_LABELS = ("E < 30 meV", "30 <= E < 48 meV", "E >= 48 meV")


def peak_band(center):
    return 0 if center < 30 else (1 if center < 48 else 2)


def save_csv(path, rows):
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def collect_data(root, output, *, sources, uncertainty=None):
    records, peak_rows, quality_rows, checks = [], [], [], []
    cache = {}
    source_entries = tuple(sources)
    expected_files = {root / source[3] for source in source_entries}
    present_files = {file for folder in {file.parent for file in expected_files}
                     for file in folder.glob("*.json")}
    if not expected_files <= present_files:
        raise ValueError("The JSON inventory has changed; review SOURCES before plotting again")
    for source_index, (sample_id, current, order, relative) in enumerate(source_entries):
        path = root / relative
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("format") != "eels_peak_results_v1":
            raise ValueError(f"Unsupported JSON format: {path}")
        if payload["units"]["energy"] != "meV":
            raise ValueError(f"Unexpected energy units: {path}")
        results = payload["results"]
        recipes = payload["reproducibility"]["analyses"]
        if sorted(r["region"] for r in results) != sorted(REGIONS):
            raise ValueError(f"Unexpected region list: {path}")
        recipe_by_id = {r["result_id"]: r for r in recipes}
        if len(recipe_by_id) != len(recipes) or set(recipe_by_id) != {r["result_id"] for r in results}:
            raise ValueError(f"Duplicate or unmatched recipe IDs: {path}")
        metadata = payload["reproducibility"]["data"]
        data = load_eels_hdf5(metadata["source"])
        if list(data.shape) != metadata["shape"] or data_checksum(data) != metadata["checksum"]:
            raise ValueError(f"Associated H5 does not match JSON: {path}")
        models = {}
        record = dict(sample_id=sample_id, current_mA=current, export_order=order,
                      source_index=source_index, source_json=relative,
                      source_h5=str(data.source), source_h5_name=data.source.name,
                      exported_utc=payload["exported_utc"], regions={})
        for region in REGIONS:
            result = copy.deepcopy(next(r for r in results if r["region"] == region))
            recipe = recipe_by_id[result["result_id"]]
            if result["mode"] != "pca" or recipe["mode"] != "pca" or not result["fit_success"]:
                raise ValueError(f"Expected successful PCA results: {path}, {region}")
            if recipe["region"] != region:
                raise ValueError("Result/recipe region mismatch")
            roi, settings = recipe["roi"], recipe["settings"]
            em = (data.energy_meV >= recipe["fit_min"]) & (data.energy_meV <= recipe["fit_max"])
            xm = (data.x_nm >= roi["x_min"]) & (data.x_nm <= roi["x_max"])
            ym = (data.y_nm >= roi["y_min"]) & (data.y_nm <= roi["y_max"])
            pixels = int(xm.sum() * ym.sum())
            if pixels != result["pixel_count"] or em.sum() < 8:
                raise ValueError(f"ROI or energy window mismatch: {path}, {region}")
            key = (recipe["fit_min"], recipe["fit_max"], settings["component_count"])
            if key not in models:
                full = data.intensity[:, :, em]
                models[key] = fit_pca_model(full.reshape(-1, full.shape[-1]), settings["component_count"])
            cube = data.intensity[ym][:, xm, :][:, :, em]
            restored = analyze_pca_reconstruction(
                models[key], data.energy_meV[em], cube.reshape(-1, cube.shape[-1]),
                settings["peak_count"], settings["min_peak_distance_meV"],
                settings["min_prominence_fraction"])
            exported_centers = np.array([p["center_meV"] for p in result["peaks"]])
            restored_centers = np.array([p.center_meV for p in restored.peaks])
            if len(exported_centers) != result["detected_peak_count"] or len(exported_centers) != len(restored_centers):
                raise ValueError(f"Restored peak count differs: {path}, {region}")
            if not np.allclose(exported_centers, restored_centers, rtol=0, atol=1e-6):
                raise ValueError(f"Restored peak centers differ: {path}, {region}")
            if not np.isclose(restored.reconstruction_rmse, result["rmse"], rtol=1e-8, atol=1e-7):
                raise ValueError(f"Restored PCA RMSE differs: {path}, {region}")
            for exported_peak, restored_peak in zip(result["peaks"], restored.peaks, strict=True):
                if exported_peak["width_kind"] != "half_prominence":
                    raise ValueError("Unexpected PCA width definition")
                if not np.isclose(exported_peak["width_meV"], restored_peak.width_meV, rtol=1e-8, atol=1e-6):
                    raise ValueError("Restored half-prominence width differs")
            result, uncertainty_origin = complete_missing(
                result, recipe, metadata, restored, data.intensity[:, :, em], cube,
                output, sample_id, uncertainty, ROOT)
            energy, raw, reconstructed = restored.energy_meV, restored.intensity, restored.reconstructed
            normalization = float(np.max(reconstructed))
            if not np.isfinite(normalization) or normalization <= 0:
                raise ValueError("Cannot normalize the PCA spectrum by a positive maximum")
            region_data = dict(energy=energy, raw=raw, reconstructed=reconstructed,
                               normalization=normalization, result=result, recipe=recipe,
                               uncertainty_origin=uncertainty_origin)
            record["regions"][region] = region_data
            base = dict(sample_id=sample_id, current_mA=current, export_order=order,
                        region=region, source_json=relative, source_h5=str(data.source),
                        result_id=result["result_id"], uncertainty_origin=uncertainty_origin)
            unc = result["uncertainty"]
            for peak in result["peaks"]:
                center, std = peak["center_meV"], peak["center_std_meV"]
                if not math.isfinite(center) or (std is not None and (not math.isfinite(std) or std < 0)):
                    raise ValueError("Invalid exported peak center or standard deviation")
                if not recipe["fit_min"] <= center <= recipe["fit_max"]:
                    raise ValueError("Peak outside its analysis window")
                ci = peak["center_ci95"] or [None, None]
                peak_rows.append({**base, "source_peak_index": peak["index"],
                                  "center_meV": center, "center_std_meV": std,
                                  "center_ci95_low_meV": ci[0], "center_ci95_high_meV": ci[1],
                                  "width_kind": peak["width_kind"], "width_meV": peak["width_meV"],
                                  "prominence": peak["prominence"],
                                  "display_energy_band": PEAK_LABELS[peak_band(center)],
                                  "uncertainty_status": unc["status"]})
            quality_rows.append({**base, "mode": result["mode"], "pixel_count": pixels,
                                 "fit_min_meV": recipe["fit_min"], "fit_max_meV": recipe["fit_max"],
                                 "pca_components": settings["component_count"],
                                 "requested_peak_count": result["requested_peak_count"],
                                 "detected_peak_count": result["detected_peak_count"],
                                 "unresolved_peak_count": result["unresolved_peak_count"],
                                 "rmse": result["rmse"],
                                 "retained_variance_ratio": result["pca"]["retained_variance_ratio"],
                                 "bootstrap_requested": unc["n_requested"],
                                 "bootstrap_success": unc["n_success"],
                                 "bootstrap_failed": unc["n_failed"],
                                 "min_peak_distance_meV": settings["min_peak_distance_meV"],
                                 "min_prominence_fraction": settings["min_prominence_fraction"],
                                 "normalization_Imax": normalization,
                                 **roi})
            delta = float(np.max(np.abs(exported_centers-restored_centers))) if len(exported_centers) else 0.0
            checks.append({**base, "h5_checksum_match": True,
                           "peak_count_match": True, "max_center_difference_meV": delta,
                           "rmse_matches": True, "half_prominence_widths_match": True})
            array_key = f"s{source_index}_{region}"
            cache[array_key + "_energy_meV"] = energy
            cache[array_key + "_raw_intensity"] = raw
            cache[array_key + "_pca_intensity"] = reconstructed
            spectrum_rows = [dict(energy_meV=float(e), raw_intensity=float(y), pca_intensity=float(z),
                                  raw_normalized=float(y/normalization), pca_normalized=float(z/normalization))
                             for e, y, z in zip(energy, raw, reconstructed, strict=True)]
            save_csv(output / "spectra" / f"{sample_id}_{region}.csv", spectrum_rows)
        records.append(record)
        print(f"Restored {sample_id}: {data.source.name}; all three regions verified", flush=True)
    save_csv(output / "peak_positions.csv", peak_rows)
    save_csv(output / "analysis_quality.csv", quality_rows)
    manifest = [{k: v for k, v in record.items() if k != "regions"} for record in records]
    (output / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    np.savez_compressed(output / "spectra_restored.npz", **cache)
    report = dict(json_file_count=len(records), region_spectrum_count=len(quality_rows),
                  exported_peak_count=len(peak_rows), all_h5_checksums_match=True,
                  max_center_difference_meV=max(check["max_center_difference_meV"] for check in checks),
                  numerical_tolerance_meV=1e-6,
                  bootstrap_recomputed=any(r['uncertainty_origin']=='computed_supplement' for r in quality_rows),
                  uncertainty_source="Original JSON when available; explicitly requested supplements are separately identified",
                  curves="Exact PCA restored from original H5, ROI and settings; no Gaussian model",
                  checks=checks)
    (output / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return records, peak_rows, quality_rows, report


def configure_style():
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Arial", "DejaVu Sans"],
        "font.size": 10, "axes.labelsize": 12, "axes.titlesize": 18,
        "axes.linewidth": 0.9, "axes.edgecolor": "#42484D", "text.color": "#22272B",
        "axes.labelcolor": "#22272B", "xtick.color": "#42484D", "ytick.color": "#42484D",
        "xtick.direction": "in", "ytick.direction": "in", "xtick.major.size": 4,
        "ytick.major.size": 4, "svg.fonttype": "none", "pdf.fonttype": 42,
        "savefig.facecolor": "white", "figure.facecolor": "white",
    })


def save_figure(fig, output, stem):
    for extension in ("png", "svg", "pdf"):
        fig.savefig(output / f"{stem}.{extension}", dpi=350, bbox_inches="tight", pad_inches=0.14)


