"""Real-data CPU/GPU benchmark; GPU is optional and stays outside production code."""

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from threadpoolctl import threadpool_limits
from eels_web import AnalysisService, PCASettings, ROI
from eelsfit.bootstrap_runtime import INDEPENDENT_SCHEME, LEGACY_SCHEME
from eelsfit.pca import PCAModel, _sample_spatial_blocks_fast, analyze_pca_reconstruction, spatial_block_pca_bootstrap
from eelsfit.pca import _pca_resample
from concurrent.futures import ThreadPoolExecutor


def gpu_sample(cp, context, rng):
    reference = context["reference"]
    spectra = _sample_spatial_blocks_fast(context["full"], 2, rng)
    roi = _sample_spatial_blocks_fast(context["roi"], 2, rng)
    matrix = spectra[cp.all(cp.isfinite(spectra), axis=1)]
    mean = cp.mean(matrix, axis=0)
    centered = matrix - mean
    centered = centered / cp.linalg.norm(centered)
    rank = min(matrix.shape[0] - 1, matrix.shape[1], reference.n_components + 8)
    directions = cp.asarray(rng.standard_normal((matrix.shape[1], rank)))
    projected = centered @ directions
    for _ in range(2):
        basis, _ = cp.linalg.qr(projected, mode="reduced")
        projected = centered @ (centered.T @ basis)
    basis, _ = cp.linalg.qr(projected, mode="reduced")
    reduced = basis.T @ centered
    # The small reduced SVD is faster on CPU; large products/QR remain on GPU.
    _, singular, loadings = np.linalg.svd(cp.asnumpy(reduced), full_matrices=False)
    ratios = singular[:reference.n_components] ** 2 / float(cp.sum(centered ** 2))
    model = PCAModel(cp.asnumpy(mean), loadings[:reference.n_components], ratios, matrix.shape[0])
    roi = roi[cp.all(cp.isfinite(roi), axis=1)]
    roi_mean = cp.asnumpy(cp.mean(roi, axis=0))
    return analyze_pca_reconstruction(model, context["energy"], np.stack([roi_mean, roi_mean]),
                                      reference.requested_peak_count, reference.min_peak_distance_meV,
                                      reference.min_prominence_fraction)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=200)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--data", default="sample.h5")
    parser.add_argument("--cpu-workers", type=int, nargs="+", default=[0, 1, 4, 8])
    parser.add_argument("--threads", type=int, default=0)
    args = parser.parse_args()
    service = AnalysisService(args.data)
    region = ROI(**service.default_rois()["Si"])
    analysis = service._pca_analyze("Si", region, 0, 120, PCASettings(5, 3, 5, .005, 20260728))
    energy, full, roi, _, _ = service._extract_pca_cubes(region, 0, 120)
    measurements = {"shape": full.shape, "samples": args.samples, "seconds": {}}
    previous = None
    for requested in args.cpu_workers:
        scheme = INDEPENDENT_SCHEME if requested else LEGACY_SCHEME
        workers = requested or 1
        start = time.perf_counter()
        result = spatial_block_pca_bootstrap(energy, full, roi, analysis.result, args.samples,
                                             seed=20260728, workers=workers, rng_scheme=scheme)
        key = f"{scheme}_{workers}_workers"
        measurements["seconds"][key] = time.perf_counter() - start
        print(key, measurements["seconds"][key], flush=True)
        if scheme == INDEPENDENT_SCHEME:
            if previous is not None:
                np.testing.assert_allclose(previous.centers_meV, result.centers_meV, rtol=0, atol=0)
                np.testing.assert_allclose(previous.widths_meV, result.widths_meV, rtol=0, atol=0)
            previous = result
    if args.threads:
        start = time.perf_counter()
        context = {"energy": energy, "full": full, "roi": roi, "reference": analysis.result,
                   "block_size": 2, "fast_sampling": True}
        children = np.random.SeedSequence(20260728).spawn(args.samples)
        with threadpool_limits(limits=1, user_api="blas"), ThreadPoolExecutor(args.threads) as pool:
            results = list(pool.map(lambda child: _pca_resample(context, np.random.default_rng(child)), children))
        measurements["seconds"][f"cpu_{args.threads}_threads"] = time.perf_counter() - start
        valid = [result for result in results if result is not None]
        if previous is not None:
            np.testing.assert_array_equal(np.array([sample[0] for sample in valid]), previous.centers_meV)
            np.testing.assert_array_equal(np.array([sample[1] for sample in valid]), previous.widths_meV)
    if args.gpu:
        import cupy as cp
        print("GPU", cp.cuda.runtime.getDeviceProperties(0)["name"], flush=True)
        start = time.perf_counter()
        context = {"full": cp.asarray(full), "roi": cp.asarray(roi), "energy": energy, "reference": analysis.result}
        children = np.random.SeedSequence(20260728).spawn(args.samples)
        with threadpool_limits(limits=1, user_api="blas"):
            gpu_sample(cp, context, np.random.default_rng(children[0]))
            cp.cuda.Stream.null.synchronize()
            measurements["gpu_cold_start_seconds"] = time.perf_counter() - start
            start = time.perf_counter()
            results = [gpu_sample(cp, context, np.random.default_rng(child)) for child in children]
            cp.cuda.Stream.null.synchronize()
            measurements["seconds"]["gpu_float64_warm"] = time.perf_counter() - start
        valid = [result for result in results if len(result.peaks) >= len(analysis.result.peaks)]
        measurements["gpu_success"] = len(valid)
        if len(valid) == previous.n_success:
            centers = np.array([[peak.center_meV for peak in result.peaks] for result in valid])
            widths = np.array([[peak.width_meV for peak in result.peaks] for result in valid])
            measurements["gpu_max_center_difference_meV"] = float(np.max(np.abs(centers - previous.centers_meV)))
            measurements["gpu_max_width_difference_meV"] = float(np.max(np.abs(widths - previous.widths_meV)))
    service.shutdown()
    from eelsfit.runtime_paths import test_runs_dir
    root = test_runs_dir() / ".tmp_bootstrap_performance"
    root.mkdir(exist_ok=True)
    (root / "benchmark.json").write_text(json.dumps(measurements, indent=2), encoding="utf-8")
    print(json.dumps(measurements, indent=2), flush=True)


if __name__ == "__main__":
    main()
