"""Replay a compact EELS result JSON using the retained input data and program."""

from __future__ import annotations

import argparse
import json
import tempfile
import warnings
from pathlib import Path

from eels_web import (
    AnalysisService, AutoFitSettings, PCASettings, ROI, RUNTIME_SOFTWARE,
    _serialize_analysis, _serialize_pca_analysis,
)
from eelsfit.core import FitConfig, residual_block_bootstrap
from eelsfit.exporting import RESULT_FORMAT, CHECKSUM_SCOPE, build_result_document, data_checksum, json_text
from eelsfit.importing import prepare_import
from eelsfit.pca import spatial_block_pca_bootstrap
from eelsfit.bootstrap_runtime import LEGACY_SCHEME
from eelsfit.runtime_paths import runtime_work_dir


def replay(result_file: Path, data_path: Path | None = None, *, allow_version_mismatch=False) -> dict:
    result_file = Path(result_file).resolve()
    if result_file.is_dir():
        raise ValueError("For an old ZIP bundle, use its own source/reproduce.py. This script expects a result JSON.")
    manifest = json.loads(result_file.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict) or manifest.get("format") != RESULT_FORMAT:
        raise ValueError("Unsupported result JSON format.")
    metadata = manifest["reproducibility"]
    recipes = metadata["analyses"]
    results = manifest["results"]
    result_map = {item["result_id"]: item for item in results}
    recipe_ids = [item["result_id"] for item in recipes]
    if (not results or len(result_map) != len(results) or len(set(recipe_ids)) != len(recipe_ids)
            or set(recipe_ids) != set(result_map)):
        raise ValueError("Results and reproduction recipes must match one-to-one by result_id.")
    for recipe in recipes:
        result = result_map[recipe["result_id"]]
        if result["mode"] != recipe["mode"] or result["region"] != recipe["region"]:
            raise ValueError("Result mode/region does not match its reproduction recipe.")
    expected_software = metadata["software"]
    fields = ("algorithm_id", "source_hash_scope", "source_sha256", "python", "packages", "random_generator")
    mismatches = [name for name in fields if expected_software.get(name) != RUNTIME_SOFTWARE[name]]
    if mismatches:
        message = "Program/environment mismatch: " + ", ".join(mismatches)
        if not allow_version_mismatch:
            raise ValueError(message + ". Restore recorded versions, or explicitly use --allow-version-mismatch.")
        warnings.warn(message + "; exact reproduction is not guaranteed.", RuntimeWarning)
    if expected_software.get("platform") != RUNTIME_SOFTWARE["platform"]:
        warnings.warn("Platform differs; floating-point results may differ.", RuntimeWarning)

    recorded = metadata["data"]
    checksum = recorded["checksum"]
    if checksum.get("algorithm") != "sha256" or checksum.get("scope") != CHECKSUM_SCOPE:
        raise ValueError("Unsupported data checksum scheme.")
    if data_path is None:
        candidates = [recorded.get("source"), recorded.get("original_source")]
        data_path = next((Path(path) for path in candidates if isinstance(path, str) and Path(path).is_file()), None)
    if data_path is None:
        raise ValueError("Input data not found. Supply --data with the retained HDF5 or trusted toolbox MAT file.")
    with tempfile.TemporaryDirectory(prefix=".tmp_reproduce_", dir=runtime_work_dir()) as temporary:
        data = prepare_import(Path(data_path), Path(temporary) / "converted.h5")
        if data_checksum(data) != checksum:
            raise ValueError("Data checksum mismatch; refusing to reproduce changed input.")
        # A MAT conversion is temporary; keep its durable original path in the new result.
        if Path(data_path).suffix.lower() == ".mat":
            data.source = Path(data_path).resolve()
        service = AnalysisService(data)
        return _replay_recipes(service, recipes)


def _replay_recipes(service, recipes):
    analyses = []
    serialized = []
    try:
        for recipe in recipes:
            mode = recipe["mode"]
            region, roi = recipe["region"], ROI(**recipe["roi"])
            lower, upper = recipe["fit_min"], recipe["fit_max"]
            if mode == "pca":
                analysis = service._pca_analyze(region, roi, lower, upper, PCASettings(**recipe["settings"]))
            elif mode in {"manual", "auto"}:
                settings = (AutoFitSettings if mode == "auto" else FitConfig)(**recipe["settings"])
                analysis = service._fit(region, roi, lower, upper, settings)
            else:
                raise ValueError(f"Unsupported mode: {mode}")
            bootstrap = recipe["bootstrap"]
            if bootstrap:
                kwargs = {"n_resamples": bootstrap["n_resamples"], "seed": bootstrap["seed"]}
                kwargs["rng_scheme"] = bootstrap.get("rng_scheme", LEGACY_SCHEME)
                # Worker count changes scheduling, not samples. Replay uses one worker.
                kwargs["workers"] = 1
                if mode == "pca":
                    energy, full, roi_cube, _, _ = service._extract_pca_cubes(roi, lower, upper)
                    analysis.bootstrap = spatial_block_pca_bootstrap(
                        energy, full, roi_cube, analysis.result,
                        block_size_pixels=bootstrap["block_size"], **kwargs,
                    )
                else:
                    analysis.bootstrap = residual_block_bootstrap(
                        analysis.result, analysis.config, block_size=bootstrap["block_size"], **kwargs,
                    )
            analysis.recipe = {key: value for key, value in recipe.items() if key not in {"result_id", "resolved_fit_config"}}
            if bootstrap and kwargs["rng_scheme"] != LEGACY_SCHEME:
                analysis.recipe["bootstrap"] = {**bootstrap, "workers": 1, "backend": "serial", "blas_threads": 1}
            analysis.result_id = recipe["result_id"]
            analyses.append(analysis)
            serialized.append((_serialize_pca_analysis if mode == "pca" else _serialize_analysis)(analysis))
        return build_result_document(service.data, analyses, serialized, RUNTIME_SOFTWARE)
    finally:
        service.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_json", type=Path)
    parser.add_argument("--data", type=Path, help="Override the retained HDF5/MAT path after moving data.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-version-mismatch", action="store_true")
    args = parser.parse_args()
    path = args.output or args.result_json.with_name(args.result_json.stem + "_reproduced.json")
    if path.exists():
        parser.error(f"Output exists; choose another --output: {path}")
    try:
        output = replay(args.result_json, args.data, allow_version_mismatch=args.allow_version_mismatch)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json_text(output) + "\n")
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"Reproduction failed: {error}\n")
    print(f"Reproduced {len(output['results'])} regional analyses: {path}")


if __name__ == "__main__":
    main()
