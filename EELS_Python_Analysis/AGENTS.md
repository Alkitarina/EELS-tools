# EELS Python Analysis

## Scope and Working Rules

- This is the Python EELS analysis project, not the MATLAB toolbox. Do not modify the original MAT data or `3D-EELS TOOLBOX` sources for changes here.
- Keep the existing Chinese Gaussian-style web layout. Web entry: `start_web.ps1`; stop with `stop_web.ps1`. Do not restore BAT as the web entry point.
- Preserve user results and running analysis sessions. A backend restart loses in-memory results; preserve/export them or start the updated server on another port.
- Default export is ONE readable UTF-8 JSON file, not ZIP. Do not include raw data arrays, full spectra, per-replicate bootstrap arrays, or source code by default.
- `/health.result_format` exposes the current JSON contract. `start_web.ps1` reuses only matching-format services with `bootstrap_engine: cpu_parallel_v1`, `reference_maps_version: reference_maps_v1`, and `region_fit_version: contrast_region_fit_v1`, leaving older sessions running on their original ports.
- Parameters must describe the actual completed analysis for each region, never the current unexecuted UI input values.
- Test with `.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v`. Browser verification is in `tests/browser_import_export.cjs` and changes the test server's active data.
- Write Chinese documentation as UTF-8. Use only exact, task-owned `.tmp_*` directories for temporary tests.

## Signal Map UI

- Consecutive imports in the same page preserve all analysis inputs (including energy bounds and Gaussian FWHM constraints) and usable ROI coordinates. Apply dataset-derived defaults only on initial page load. New data always invalidates old previews/results/uncertainty; failed imports preserve them. Keep out-of-range energy inputs and report partial coverage or fewer than eight actual channels; never silently widen a fitting window. Clip spatial bounds or use the new default interval only on an axis with no selected pixel, with a visible notice. Browser regression: `tests/browser_import_settings.cjs` with its isolated fixture server.

- Spatial rectangles support eight resize handles, edge dragging, interior translation, and redraw outside the active rectangle. Cancel/no-op clicks preserve the previous region and analysis; changed bounds invalidate only that region.
- Keep spectrum preview on pointer release, not continuous live fitting or preview while dragging. The user explicitly scoped this feature to rectangle interaction and map rendering.
- The original energy-window map uses MATLAB Parula RGB samples, full finite min/max color limits, physical x/y aspect ratio, and no grid/axis overlay. This must not change input arrays, integration bounds, analysis algorithms, or JSON recipes.
- Reference maps and candidate center lines are display-only. Changing `map-kind` or toggling `show-centers` MUST NOT change any Si/Interface/Ge ROI, active region, analysis input, cached preview, fit/PCA result or uncertainty, or request another spectrum preview. Keep switching separate from `updateMap()` and reject stale responses after dataset changes or a newer display choice.
- Reference choices: retained `EELS.info.totint` (gray, never label it HAADF), fixed 27-32 meV integral (Parula), two-reference spectral contribution (blue-white-red). Read optional `/reference_maps/total_counts` with explicit stored axis order; missing/mismatched maps stay unavailable and never fall back to another signal under the same name. Both MATLAB exporters retain `info.totint` only when its dimensions match.
- New H5 exports declare `reference_maps_schema: eels_reference_maps_v1`, `total_counts_status: available|missing_in_source|missing_in_input`, and map `source_field: EELS.info.totint` with explicit h5py storage `axis_order`. Only confirmed MAT absence means `missing_in_source`; a Python input without retained counts uses `missing_in_input`. An existing invalid source map fails conversion; it must not be silently omitted. Legacy files remain readable. Both MATLAB exporters share `write_eels_reference_maps.m`; batch input/output may be sibling folders. Keep each multi-EELS object's map under its own group. See `docs/H5数据格式与总计数.md` for the contract and single-object UI copies.
- Candidate centers use all-row-mean error-function midpoints; label as approximate candidates, not independently calibrated structural interfaces. Spectral references come from fixed left/right strips, independent of the user's selected ROIs, and use 15-45 plus 55-75 meV. These overlays do not enter peak recipes or the JSON export contract. See `docs/参考图与候选界面.md`.
- Optional contrast region fitting is off by default. Fit/preview/model/percentage/background changes MUST preserve all existing ROIs, previews and peak results. Only the explicit Apply action updates the three rectangles and invalidates changed regions; afterwards all existing manual handles/translation/redraw remain available. Defaults: Si C<=1%, Interface C=10-90%, Ge C>=99%, full y extent. Percentages are plateau contrast, not chemical abundance. Reject outside-image or empty-pixel proposals without silently clipping or inventing bulk regions. Fit both erf and logistic to all-y means of retained total counts or the cached two-reference map; do not label either source HAADF.
- `/api/region-fit` uses a small profile in a dedicated subprocess with an 8-second kill/reap deadline and bounded evaluations. The frontend recovers after 12 seconds, supports cancellation, and discards late/stale responses on input/dataset changes. Preserve active services; start the upgraded feature on a new port. Tests: `tests/test_region_fit.py` and `node tests/browser_region_fit.cjs` (isolated fixture; checks preview/apply separation, manual adjustment, both models, thresholds, failures, cancellation, timeout and responsive layout). See `docs/衬度拟合选区.md`.
- Regression: `python -m unittest tests.test_reference_maps tests.test_web -v`, and `node tests/browser_reference_maps.cjs` (starts its own isolated fixture server). The browser test checks switch preservation, unavailable maps, slow-response ordering, candidate overlays and narrow-screen layout.
- Geometry test: `node tests/test_roi_geometry.cjs`. Chrome interaction/render test: `node tests/browser_roi.cjs`, with `EELS_TEST_URL` pointing to a separate QA service (default port 8770). It runs a PCA analysis on that service; do not target a user's active analysis session.

## CPU Uncertainty Execution

- The user chose CPU acceleration, not production GPU execution. CuPy is optional benchmark-only tooling; do not enable GPU or reduce float64 precision automatically.
- Default resamples are 500 in the web UI, both API fallback values, and core routines (`DEFAULT_RESAMPLES`). Keep these synchronized.
- PCA uses a bounded thread pool; Gaussian bootstrap uses processes for large jobs. Automatic selection accounts for sample count, CPU count and, for PCA, a conservative memory budget. `workers: 0` in a request means automatic; exports must record the resolved count.
- New sampling uses one `SeedSequence(seed).spawn(n_resamples)[index]` per replicate, and `default_rng(child)`; worker count/completion order must never affect the RNG stream or output order. Each replicate keeps the original fitting precision/criteria. Numerical failures are retained in the requested/failed counts.
- Limit BLAS to one thread for new-style resampling; restore caller limits afterward. Do not allow unbounded queued large arrays or nested BLAS oversubscription. Processes share large read-only context arrays and close shared memory after workers exit.
- `tests/test_parallel.py`, the PCA/Gaussian parity tests, and export/replay tests cover scheduling-independent results and error handling. `tests/benchmark_uncertainty.py` measures actual data; its optional GPU comparison is not the production backend.

## JSON Contract: eels_peak_results_v1

Producer: `eelsfit/exporting.py::build_result_document`, served by `POST /api/export`.
Consumer: `reproduce.py::replay`. Both must change together when the contract changes.
The response is `application/json`, attachment `<imported file stem>.json`. Preserve Unicode, spaces and earlier dots; replace only the input extension. MAT conversion keeps the input stem. The browser derives the same name from the current dataset so existing backend sessions remain compatible; the API uses an encoded attachment header for Unicode names.

Top-level fields, in human-readable output order:

| Field | Type | Meaning |
| --- | --- | --- |
| `format` | string | Exactly `eels_peak_results_v1` |
| `exported_utc` | string | ISO 8601 timestamp with UTC offset |
| `units` | object | Position nm, energy meV, intensity unchanged input units |
| `results` | array | Compact results, one entry per retained mode/region analysis |
| `reproducibility` | object | MUST be written last; all data/version/parameter metadata |

Object key order is for human reading only; consumers must look up keys by name.

### results[]

- Identity: `result_id` (opaque unique string), `region` (`Si`, `Interface`, `Ge`), `mode` (`pca`, `manual`, `auto`). Do not infer material positions from these names.
- Counts: `pixel_count`, `requested_peak_count`, `detected_peak_count`, `unresolved_peak_count`.
- Fit quality: `fit_success`, `fit_message` (string or null), `rmse`. In PCA, RMSE is reconstruction error, not Gaussian fitting error.
- `pca`: null for Gaussian modes; otherwise `component_count`, `training_spectra`, `retained_variance_ratio`, `explained_variance_ratio` (fractions, not percentages).
- `peaks`: array, ordered by energy as supplied by the numerical analysis. Keep a zero-peak region as a result with `peaks: []`; never fabricate missing peaks.
- `uncertainty`: `status` (`computed` or `not_computed`), `method` (`spatial_block_pca` or `residual_block`), `n_requested`, `n_success`, `n_failed`, `ci_percentiles: [2.5,97.5]`, `std_ddof: 1`. Method names describe the mode even when uncertainty has not been run; status is authoritative.

Each `peaks[]` entry has:

| Fields | Meaning |
| --- | --- |
| `index` | One-based peak number |
| `center_meV`, `center_std_meV`, `center_ci95` | Peak position, successful-bootstrap sample SD, 95% interval |
| `width_kind` | `half_prominence` for PCA; `gaussian_fwhm` for Gaussian modes |
| `width_meV`, `width_std_meV`, `width_ci95` | Width and uncertainty; never conflate the two width definitions |
| `prominence`, `prominence_std`, `prominence_ci95` | PCA prominence and uncertainty; null for Gaussian modes |
| `amplitude`, `amplitude_std`, `amplitude_ci95` | Gaussian amplitude and uncertainty; null for PCA |
| `area`, `area_std`, `area_ci95` | Gaussian area (intensity times meV) and uncertainty; null for PCA |

All intervals are `[lower, upper]` or null. Uncomputed/unavailable uncertainty is null, NEVER zero, a string such as `--`, or JSON NaN/Infinity. SD requires at least two successful samples; percentile intervals use successful samples only. The intervals do not include all calibration/preprocessing systematics.

### reproducibility

| Field | Contents |
| --- | --- |
| `data` | `source`: retained input path; `original_source`: origin information (normally MAT path); `shape`: `[ny,nx,nE]`; `axis_order`: `y,x,energy`; `checksum` described below |
| `software` | `algorithm_id`, `source_hash_scope`, `source_sha256`, `python`, `platform`, `packages`, `random_generator` |
| `notes` | Human-readable scope and interpretation notes |
| `analyses` | Actual per-result recipes; one-to-one with `results` by `result_id` |

`software.algorithm_id` is `eels_peak_analysis_v1`. `packages` records NumPy, SciPy, h5py, Flask versions. `random_generator` records the concrete NumPy bit generator, currently obtained from `default_rng` rather than assumed. Source fingerprints cover only the explicit files in `SOURCE_FILES`; no broad repository/data-tree hashing. Hash scope `sha256_utf8_lf_v1`: decode source as UTF-8-SIG, normalize universal newlines to LF, encode UTF-8, SHA-256. `RUNTIME_SOFTWARE` is captured at server module import, not relabeled during export after a source edit.

Each `reproducibility.analyses[]` contains:

- `result_id`, `mode`, `region`: must match its result; IDs must be unique in both arrays.
- `roi`: inclusive pixel-center bounds `x_min`, `x_max`, `y_min`, `y_max`, in nm.
- `fit_min`, `fit_max`: requested inclusive energy bounds in meV; select existing channels, do not resample.
- `dataset_id`, `created_utc`: provenance from the actual run.
- `settings`: normalized actual settings, described below.
- `bootstrap`: null if not run; otherwise `method`, `n_resamples`, `block_size`, `actual_block_size`, `seed`, and the optional execution fields below.
- `resolved_fit_config`: null for PCA; Gaussian `FitConfig` actually selected for the baseline fit/bootstrap.

Settings by mode:

| Mode | Required settings |
| --- | --- |
| `pca` | `component_count`, `peak_count`, `min_peak_distance_meV`, `min_prominence_fraction`, `seed` |
| `auto` | `peak_count`, `fwhm_lower`, `fwhm_upper`, `include_baseline`, `n_starts`, `seed` |
| `manual` | `centers`, `center_lower`, `center_upper`, `fwhm_lower`, `fwhm_upper`, `include_baseline`, `max_nfev` |

Bounds/centers in serialized Gaussian settings are numeric arrays, not UI comma-separated strings. PCA prominence is a FRACTION in recipes (UI is percent). Gaussian widths are FWHM in meV, not sigma.

Bootstrap details:

- PCA: spatial block edge length in pixels; reseed from saved `bootstrap.seed`, resample full cube and ROI, and recompute randomized PCA each iteration.
- Gaussian: circular residual block length in energy channels. `block_size` may be null for auto selection; `actual_block_size` records the chosen length. Refit using the selected Gaussian model and its fitted initial parameters.
- Save the ACTUAL per-region seed (`base seed + region index` in a multi-region job), not only the job's base seed. Replay the stored seed directly; do not add the index again.
- Auto-fit search seed (`settings.seed`) and residual-bootstrap seed (`bootstrap.seed`) may differ. Preserve both.

New optional `bootstrap` fields (still JSON format v1):

| Field | Meaning |
| --- | --- |
| `rng_scheme` | `seedsequence_per_resample_v1` for new runs; missing means `legacy_sequential_v1`, the original continuous RNG stream |
| `workers` | Actual worker count, an integer >= 1 |
| `backend` | `thread` for parallel PCA, `process` for parallel Gaussian, `serial` for one worker |
| `blas_threads` | 1 for new-style resampling; missing legacy values preserve the previous behavior |

Replay MUST dispatch on `rng_scheme` and reject unknown values. Legacy streams require one worker. New-style replay deliberately uses one worker and writes `workers: 1, backend: serial, blas_threads: 1` to its new recipe; it must reproduce the same per-replicate samples as parallel execution. CPU parallelism is execution metadata, not a different scientific peak model. Software fingerprints now also include `eelsfit/bootstrap_runtime.py` and the `threadpoolctl` package version. Missing/new software identities still require the explicit version-mismatch override as before.

### Numeric Checksum

`checksum = {"algorithm":"sha256","scope":"eels_numeric_arrays_v1","value":"<64 lowercase hex characters>"}`.

The checksum is computed from the LOADED analysis data, not from whatever the path contains at export time:

1. Hash ASCII `eels_numeric_arrays_v1\n`.
2. In order `x_nm`, `y_nm`, `energy_meV`, `intensity`, hash a compact ASCII JSON header `{"name":"...","shape":[...]}` followed by newline.
3. Hash that array as little-endian float64 bytes in logical C order, canonicalizing NaN payloads to NumPy's standard NaN. Keep infinities and signed zeros unchanged. No delimiter is appended after array bytes.

Intensity is logically `[y,x,energy]`; axes are one-dimensional. HDF5 compression, MATLAB's reversed disk dimensions, file names and metadata do not affect the checksum after loading. Actual coordinate or intensity changes do affect it. Keep this algorithm synchronized with replay; changes require a NEW checksum scope.

## Replay and Compatibility

```powershell
.\.venv\Scripts\python.exe reproduce.py 'C:\results\eels_results.json'
.\.venv\Scripts\python.exe reproduce.py 'C:\results\eels_results.json' --data 'D:\moved\data.h5'
```

- Input can be retained HDF5 or trusted toolbox MAT via `--data`; MAT needs local MATLAB and toolbox conversion, followed by the same numeric checksum check.
- Missing input never triggers fabricated data. Checksum mismatch must be rejected even with `--allow-version-mismatch`.
- Unknown `format`, unknown checksum scheme, duplicate/mismatched result/recipe IDs or mismatched mode/region must be rejected.
- By default, mismatched algorithm/source/Python/package/RNG identity blocks replay. `--allow-version-mismatch` explicitly downgrades these checks to warnings, not a promise of identical results. Platform differences also warn.
- Replay writes `<name>_reproduced.json` with the same current JSON contract; never overwrite an existing output. `--output` can select another path.
- Old ZIP exports retain their own `source/reproduce.py`; do not reinterpret their format as this JSON contract. Legacy CSV endpoints are not reproducible result files.
- JSON exports are NOT self-contained: users must retain input data and the relevant program version. No raw-source/data embedding to bypass that tradeoff.
- Additive optional fields may remain v1. Breaking field meanings/types or replay semantics require a new format/algorithm identifier and explicit compatibility handling.
- Any export change needs round-trip tests for all three modes, bootstrap seeds, null uncertainty, zero detected peaks, moved/changed input, and version mismatch. Keep this file and user docs synchronized.
