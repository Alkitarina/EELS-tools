# EELS Python Analysis

## Scope and Working Rules

- The double-click entry `打开分析界面.vbs` invokes `start_web.ps1` without any data argument. Fresh startup must show the empty web UI and allow subsequent imports; never add a startup file picker or restore a previous data path. `launch_eels_peakfit.bat` remains a separate command-line analysis tool.
- This is the Python EELS analysis project, not the MATLAB toolbox. Do not modify the original MAT data or `3D-EELS TOOLBOX` sources for changes here.
- Keep the Chinese UI in the EELS-ZEST Parallel visual style (user request 2026-10-13): pastel background, translucent cards, blue actions, compact numbered workflow steps. `web/static/workbench.css` supplies the shared theme; preserve scientific map palettes, physical aspect ratios, control IDs and method-specific state. The legacy workspace uses charts plus a parameter sidebar on desktop and stacked sections on narrow screens. Web entry: `start_web.ps1`; stop with `stop_web.ps1`. Do not restore BAT as the web entry point.
- The latest user UI stays at http://127.0.0.1:8768 (user-approved move on 2026-09-22; 8765 belongs to the separate MQDM service). Never move the latest UI to 8766/8767/etc. to avoid an occupied port. Older sessions may remain on other ports. During an update, preserve/export the old session's results before replacing the backend at 8768; do not interrupt active calculations. `-Port` is an explicit override for isolated QA/legacy services, not a new daily entry point.
- The launcher checks only the requested port. Reuse requires matching capabilities and the source identity captured when the backend started. Source mismatch, another app, or explicitly different data fails clearly without stopping anything or trying another port. The old `-MaxPortScan` argument is accepted with a deprecation warning and never enables scanning. As requested on 2026-09-22, a fresh launch without `-DataFile` opens an empty UI: never auto-load `last_launch.json`, a legacy H5, or a file picker. `-NoBrowser`/`-NonInteractive` also work without data. Explicit `-DataFile` remains optional. Empty state uses `data=None`, `dataset_loaded: false` and `source: null`, never fabricated arrays. H5/MAT and workflow JSON can both be the first import. Do not restore/save drafts or call data-dependent APIs before import. Same-project compatible running sessions are reused without clearing their current data. Optional startup metadata is history only, not input selection or session recovery. Launcher tests use isolated synthetic data and logs.
- Default export is ONE readable UTF-8 JSON file, not ZIP. Do not include raw data arrays, full spectra, per-replicate bootstrap arrays, or source code by default.
- `/health.result_format` exposes the current JSON contract. `start_web.ps1` reuses only matching-format services with `bootstrap_engine: cpu_parallel_v1`, `reference_maps_version: reference_maps_v1`, `region_fit_version: contrast_region_fit_v1`, `workflow_version: eels_interface_workflow_v1`, `workflow_revision: interface_y_stripes_v1`, `ui_preferences_version: eels_ui_preferences_v1`, `strip_data_version: independent_full_x_v1`, `empty_startup_version: deferred_data_v1`, and matching `ui_source_id`. The source identity checks only UI/backend source files, never experimental data, credentials or runtime state. It is separate from scientific export provenance.
- Parameters must describe the actual completed analysis for each region, never the current unexecuted UI input values.
- Test with `.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v`. Browser verification is in `tests/browser_import_export.cjs` and changes the test server's active data.
- Write Chinese documentation as UTF-8. Test source belongs in `tests/`; test inputs, screenshots, logs, ad hoc scripts and task backups belong in `test_runs/.tmp_<task>_<unique>/`, never loose in the project root. Use `test_runs_dir()` in Python or `tests/artifacts.cjs` in browser tests. At task completion, remove unneeded task-owned artifacts; retained disposable evidence must keep the `.tmp_` prefix. Validate exact absolute paths before cleanup and stop if cleanup fails. See `test_runs/README.md`.
- Runtime/recipe scratch uses `web_runs/.tmp_*/` and is removed when the job finishes. `web_runs/ui_preferences.json`, startup records, service logs and result archives are durable and must not be treated as disposable. Raw MAT/H5/imports stay outside test cleanup. Never run a mutating browser test against the daily UI at 8768.

## Signal Map UI

- ZEST counts schema v1 retains `EELS.info.totint` in `counts_source_totint` (NPZ) and `count_info.source_totint` (counts.json/pixel JSON). Import only these as the existing total-count reference, validate duplicate values/shape/exported coordinates, preserve NaN gaps and reverse together with spatial axes. ZEST `counts_total` is a fit-window sum and `counts_available` describes that sum, neither substitutes for retained source counts. Keep counts independent of the selected spectrum/energy correction, persist in H5 and compact import provenance; old ZIPs remain valid without counts.

- Data import also accepts EELS-ZEST result ZIP through the same upload/local-path dialog (`eelsfit/zest_import.py`, `eels_zest_import_v1`). Preserve `job.coords[index] = [y,x]` and parallel `pixel_indices`; never reshape sorted pixel files. Default signal is saved `raw_data - zlp_only`; model `vib_only` and raw input are explicit choices, all in saved intensity units. Default `ene_raw` must agree exactly across pixels; optional corrected axes use explicit linear interpolation over common support only. Keep missing/failed pixels as NaN. Do not import temperature uncertainty as peak uncertainty or substitute ZEST maps for retained total counts.
- The user deferred manual spatial calibration; read producer `spatial_calibration.json` schema v1 and NPZ `spatial_*` fields automatically. Validate YX pixel-center mapping, declared units and duplicate metadata; convert explicit physical units to nm and reverse descending axes together with the cube (record both operations). Also accept explicit `x_nm`/`y_nm` axes from `maps.npz` or `dataset.json`. Reject missing/pixel-only calibration without changing current data/results; never invent nm coordinates. Successful imports retain a validated H5 under `imports/`, with compact `zest_import_json` provenance and optional `reproducibility.data.input_preparation` in both result contracts. Keep generated H5 for replay. Tests: `test_zest_import.py`, `browser_zest_import.cjs` on isolated fixtures only.

- Analysis-method selection lives at the page top. PCA, Gaussian auto/manual, and the multivariate workflow show only their own controls. Display switching must preserve method-specific parameters, ROIs and completed results; never trigger fitting from a method or background switch.

## Persistent UI Parameters

- `eelsfit/ui_preferences.py` and `web/static/preferences.js` save editable UI drafts to project-local `web_runs/ui_preferences.json`, outside temporary job folders. Same-project ports share the file; the last editing page wins. Auto-save after edits and explicit Save use bounded requests and atomic UTF-8 replacement; do not overwrite unreadable/invalid files. File errors must leave analysis usable. Keep the same-page sequence guard for late page-close requests.
- Preserve selects and checkboxes as well as numbers, legacy options, global/local NMF/MCR-ALS settings, all eight ROI initialization seeds/enabled flags, four analysis ROIs, contrast thresholds and per-target fit inputs. Same source path plus matching shape/axis ranges restores geometry and preprocessing confirmation; a different input restores general options only. Imported image/reference contents and structure confirmation are excluded. See `docs/参数记忆.md`.
- Restore completed in-memory workflow results first, then overlay editable drafts. Draft values never change a completed result's actual settings or JSON recipe. Identical profile geometry/options preserve dependent results; changed profile inputs invalidate only their UI dependencies. Parameter restoration never starts fitting/decomposition. Full result/session recovery is not provided by this file.
- Browser fixture servers must override `UI_PREFERENCES_FILE` into their own task-owned directory. Tests: `tests/test_ui_preferences.py`, `tests/browser_ui_preferences.cjs` (actual server restart, fresh browser storage, draft/result separation and failure/timeout checks).

## Multivariate Interface Workflow

- See `docs/多变量界面残差工作流.md`. Scientific implementation: `eelsfit/multivariate.py`, `interface_workflow.py`, `interface_localization.py`, `workflow_peaks.py`; isolated process execution: `workflow_jobs.py`; API/UI: `workflow_api.py`, `web/static/workflow.{html,js}`, `web/static/workflow_regions.js`. The stopped PCA-reference subtraction prototype is archived in a task backup and is not part of the live algorithm.
- Start from the user's already denoised, background-subtracted xyE input. All finite pixels enter NMF/MCR-ALS within one explicitly confirmed reliable energy window. Preserve signed input for MCR-ALS and all subsequent bulk/residual calculations. NMF clipping requires the explicit policy; report it and limit it to a decomposition copy.
- Optional ROI mean initialization uses independent editable seed ROIs, at least 2 complete finite pixels per ROI. Record component indices, actual ROI bounds, seed-only negative policy and the relative 1e-8 positive floor. Seeded spectra initialize only the first start and remain free; unseeded components use NNDSVDa, later starts are random. Report whether the selected start used ROI seeds. Never overwrite analysis ROIs or relabel old results when initial settings change. Preview does not run decomposition. Replay rebuilds means from recorded input/ROIs; default JSON excludes spectra.
- Components/weight maps are exploratory, not automatic Si/Ge/defect labels or chemical abundances. Four manual ROIs; normal Interface excludes Core. Body ROIs cannot overlap each other or target regions. Background/image switches preserve all ROI coordinates; drag cancellation and no-ops preserve dependent results.
- Step 3 has two optional collapsed partition panels. Retained total counts use the existing bounded erf/logistic fit service, with adjustable plateau thresholds (defaults Si 99%, Interface 10–90%, Ge 99%) and full y by default. Preview/model/input switches never apply ROIs. Explicit Apply defaults to total Interface only, preserving body ROIs; applying all three is optional. Validate proposed ROIs before mutation. Missing total counts stay unavailable, never relabel another signal as counts or HAADF.
- Local NMF/MCR-ALS is an independent exploratory branch using the actual completed global energy window. New UI/API default to `x_expansion_percent: 100`: expand left and right by one original Interface width each, for 300% total width, with the same y range. The base is the current applied Interface ROI (fitted and optionally manually adjusted). Preserve all four analysis ROIs; this expansion affects only local decomposition/candidate exploration. Include every finite pixel and existing Core pixels in the expanded domain. Clip only to the available x extent with an explicit notice, never extrapolate. Both solvers remain selectable; preserve signed input and explicit NMF clipping policy.
- Record the original `roi`, requested/effective `analysis_roi`, per-side percentage and `range_rule: interface_x_expansion_v1` in completed local settings; expose `localization_range_version` in health/info. Missing expansion on numerical replay retains the exact historical unexpanded recipe; old backends must not advertise expanded previews. UI displays actual completed bounds separately from current draft expansion, outlines the original Interface within the wider map, and persists the expansion option. Changing base ROI or expansion makes a candidate stale until recomputed; local success/failure/cancel/timeout preserves primary profiles/fits. Replacing global decomposition invalidates local results.
- Core proposals threshold full-resolution local component weights with min–max normalization, selectable high/low direction and minimum connected pixels; use 8-connectivity. Candidates may lie outside the base Interface within the expanded domain. Preview the selected component spectrum/map, connected mask, bounding rectangle with extra-pixel count, and original processed mean spectra of that rectangle versus the remainder of the original Interface ROI (never the expanded context). Explicit Apply validates all ROIs, enables Core, and retains manual adjustment. Flat maps, empty candidates, invalid overlaps or a rectangle consuming all ordinary Interface cannot silently apply. Candidate components/thresholds are not defect labels, confidence or chemical fractions.
- Export the current local branch in optional compact `results.localization` and `reproducibility.localization`, with matching global parent ID and unique result ID; replay separately from the primary step chain. Full local arrays require explicit NPZ. Actual applied contrast/local-candidate evidence belongs to completed profile settings under `evidence.partition_assistance`, including original applied bounds and subsequent manual-adjustment flags. Later exploratory runs must not relabel the provenance of an earlier applied Core selection. Tests: `test_interface_localization.py`, `browser_interface_localization.cjs`.
- Optional registration uses declared x0(y) anchors, linear x interpolation, common support only; never silently extrapolate. Keep each y strip's xE distribution. Body templates are raw preprocessed ROI means or explicit optional parametric fits, never automatically PCA or NMF components.
- New Step 4/API profile runs set `stripe_y_scope: interface_roi` and `strip_data_rule: independent_full_x_v1`: split only original y pixel centers inside total Interface, beginning at its first selected row, with a possible shorter final stripe. Each stripe covers the full original x axis independently of all bulk/Core box bounds. Use total Interface y before Core exclusion. Registration covers only these selected rows and retains their common distance support without extrapolation. Keep original absolute half-open row_start/row_stop indices.
- The user clarified that bulk boxes are sources of average spectra, not limits on xE strips. New UI uses `bulk_scope: global`: extract one mean from the entire Si ROI and one from the entire Ge ROI, shared by subsequent strip/region subtraction; body Gaussian fits still use each full original ROI mean. Do not intersect a bulk box with strip y or require bulk y coverage. The deprecated per-strip choice must not be restored from saved preferences into the new UI. API/replay retain explicit historical per-strip settings without silently substituting references.
- Independent strip generation retains measured xE and pixel counts even if body mean/template extraction fails. Add `measured_status` for actual data availability; existing `status` continues to indicate subtraction availability. A missing template is not a missing strip. Step 4 permits measured preview for such strips while disabling unavailable residual/reconstruction views; Step 5 refuses unavailable subtraction, while valid Si/Ge mean-spectrum Gaussian fits remain available. JSON includes separate statuses/errors; NPZ retains measured arrays and omits unavailable derived arrays. Missing `strip_data_rule` in numerical replay preserves the historical coupled failure behavior. Test bulk boxes outside all strip y, template errors, API/NPZ, old/new replay, and restart option migration.
- Core strip marking uses only the completed Core ROI's y interval and inclusive original y pixel centers. At least one shared row marks both Step 4 and Step 5 menus purple, regardless of Core x displacement. Store `core_overlap_rows` (row count, not pixel count) and show that meaning in tooltips. This display rule never changes the actual Core/Interface spectrum masks.
- Preserve historical replay: absent `stripe_y_scope` means `full_y` in the numerical routine; the current API/UI explicitly selects `interface_roi`. Reject unknown rules and export the actual scope, strip domain, original row/y boundaries and core overlap rows. NPZ adds `strip_row_bounds` (half-open original indices), `strip_y_bounds_nm`, `strip_core_overlap_rows`. Older running ports must show their old y-scope capability rather than promising the new calculation. Tests: `test_interface_stripes.py`, `browser_interface_stripes.cjs`.
- NNLS body coefficients are nonnegative with no sum-to-one constraint, no energy shifting and no additional baseline. Preserve the signed residual. Candidate fits and joint body-plus-peak fits use the same energy window and bounds. Explain that reduced SSE is not independent physical validation. New-workflow uncertainties remain null unless actually computed in a future explicitly defined procedure.
- Residuals, objective/SSE/RMSE and Gaussian evaluation use only measured channels inside the completed decomposition energy window. Centers stay inside that window; Gaussian tails and FWHM may extend beyond its boundaries without extrapolated samples or outside-window penalties. Analytic `areas` still denotes the full line-shape area, not an outside-window residual calculation.
- New UI fits default to `amplitude_limit: {method: fixed_initial_local_extremum_v1, factor: 1.5}`; expose the switch and percentage per target and restore actual completed settings. Freeze each absolute peak-height upper bound from the maximum same-sign measured value within the initial candidate +/- `center_radius`, clipped to the measured window. Negative peaks use trough magnitude. Si/Ge reference their original ROI mean; Interface/Core reference their fixed pre-fit NNLS residual, shared by staged and joint fits. Never let the cap increase as joint bulk coefficients change. Bounded linear least squares constrains individual signed magnitudes and keeps bulk coefficients nonnegative. Export the reference neighbourhood/extremum, actual bound and `at_amplitude_limit`; include active height bounds in `at_constraint_boundary`. Missing/null settings preserve historical unbounded replay. Tests: `test_amplitude_limits.py`; browser coverage in `browser_bulk_regions.cjs`.
- Spectrum plots emphasize measured data with a dark thick line, total fits with thinner dashed lines, and Gaussian components with faint thin lines. Draw data above fit/components; never smooth data to improve appearance. Saved-JSON plotting is maintained separately; the repository's `eels_plot_workflow/` directory currently handles legacy PCA JSON.
- The UI defaults to positive and negative residual peaks, with positive-only/negative-only options. Signed peak bases let NNLS retain nonnegative body coefficients while negative features have negative amplitudes/areas. Fix each candidate's sign consistently in staged and joint fits; preserve it when sorting peak centers. Missing `polarity` in an older recipe means historical positive-only behavior. Negative peaks alone do not prove a mode disappeared. Tests: `test_roi_signed_workflow.py`, `browser_roi_signed.cjs` with isolated synthetic data only.
- New UI residual fits explicitly set `candidate_score: zero_baseline_prominence_v1`, advertised by `candidate_score_version`. Filter and rank with `min(original prominence, absolute height relative to zero)`; threshold remains `prominence * ptp(residual)`. Keep the signed residual unchanged and retain original `prominence` alongside `selection_score` and `score_rule` in candidates. Manual centers bypass automatic selection. Si/Ge bulk fitting is unchanged. Missing/null or `prominence_v1` preserves historical selection/replay; reject unknown rules. Distinguish the completed result's rule from the new-fit UI policy. Tests: `test_candidate_score.py`, `browser_bulk_regions.cjs`.
- Step 5 Si/Ge selections directly fit the original preprocessed ROI mean with positive Gaussian peaks (`analysis_kind: bulk_gaussian`), without bulk subtraction or silently changing Step 4 templates. Interface/Core continue signed residual and joint fitting (`analysis_kind: residual`). Preserve per-region inputs and completed results across target switches. Retain four region fits and the last strip fit per valid profiles result; successful upstream replacement/import invalidates them. Batch fitting is atomic on success; failures/cancellation/timeout preserve all prior fits.
- JSON `results.regions` always lists Si, Ge, Interface, Core with explicit completed/not_converged/no_peaks/not_analyzed/disabled status and null for missing results. Export every retained fit for the selected profiles, not only the on-screen region. Keep compact Gaussian bulk parameters plus staged/joint residual parameters. Replay accepts multiple fit steps with unique target keys/IDs and matching profiles_id; missing analysis_kind in old recipes retains residual behavior. Tests: `test_bulk_region_workflow.py`, `browser_bulk_regions.cjs`.
- `eels_interface_workflow_v1` is a separate JSON contract, handled together by `workflow_export.py` and `reproduce_workflow.py`; do not route it to legacy PCA plotting/recalculation or relabel it as `eels_peak_results_v1`. Default export is compact JSON; full derived arrays require the explicit NPZ export action. Filename follows input stem. Record actual completed settings/IDs, signed-data policy, ROI exclusions, alignment, templates, source/environment identity and numeric data checksum. Never export user file contents or spectra inside the default JSON.
- Jobs run in hidden subprocesses with bounded deadlines, cancellation, one BLAS thread and task-local temporary files. Kill and reap before cleanup; errors/stale/cancelled jobs preserve prior completed results. Import and competing analysis mutations are blocked during jobs. Restore workflow results when revisiting/reloading the same server, invalidate on successful dataset changes.
- Workflow JSON import: `workflow_import.py`, `POST /api/workflow/import`, header button “导入流程 JSON”. Accept only `eels_interface_workflow_v1`, UTF-8 (BOM allowed), maximum 8 MB; validate recipe/result IDs and numeric H5 checksum. Rebuild missing decomposition/profile/localization arrays in a cancellable bounded worker; evaluate saved Gaussian/Lorentzian parameters without re-optimizing peaks. Version mismatch requires explicit opt-in. Publish data/results atomically only on success; retain current data/results on any failure. Imported fit steps carry `restoration: saved_parameters_v1` and `import_origin`; replay evaluates their saved compact models, while newly fitted steps use the normal numerical recipe. Default JSON still excludes spectra. Tests: `test_workflow_import.py`, `browser_workflow_import.cjs` (isolated synthetic fixture).
- Tests: full Python suite, `tests/browser_workflow.cjs` (isolated synthetic fixture), plus existing reference-map/region-fit/import-setting browser regressions. Never target a user's active analysis service with mutation tests.
- Run the full Python/launcher suite without simultaneous browser fixture servers, so launcher port discovery cannot reuse a temporary browser service. Browser fixtures may run after that suite has exited.

## Signal Map UI (legacy PCA / Gaussian)

- Consecutive imports in the same page preserve all analysis inputs (including energy bounds and Gaussian FWHM constraints) and usable ROI coordinates. Apply dataset-derived defaults only on initial page load. New data always invalidates old previews/results/uncertainty; failed imports preserve them. Keep out-of-range energy inputs and report partial coverage or fewer than eight actual channels; never silently widen a fitting window. Clip spatial bounds or use the new default interval only on an axis with no selected pixel, with a visible notice. Browser regression: `tests/browser_import_settings.cjs` with its isolated fixture server.

- Spatial rectangles support eight resize handles, edge dragging, interior translation, and redraw outside the active rectangle. Cancel/no-op clicks preserve the previous region and analysis; changed bounds invalidate only that region.
- Keep spectrum preview on pointer release, not continuous live fitting or preview while dragging. The user explicitly scoped this feature to rectangle interaction and map rendering.
- The original energy-window map uses MATLAB Parula RGB samples, full finite min/max color limits, physical x/y aspect ratio, and no grid/axis overlay. This must not change input arrays, integration bounds, analysis algorithms, or JSON recipes.
- Reference maps and candidate center lines are display-only. Changing `map-kind` or toggling `show-centers` MUST NOT change any Si/Interface/Ge ROI, active region, analysis input, cached preview, fit/PCA result or uncertainty, or request another spectrum preview. Keep switching separate from `updateMap()` and reject stale responses after dataset changes or a newer display choice.
- Reference choices: retained `EELS.info.totint` (gray, never label it HAADF), fixed 27-32 meV integral (Parula), two-reference spectral contribution (blue-white-red). Read optional `/reference_maps/total_counts` with explicit stored axis order; missing/mismatched maps stay unavailable and never fall back to another signal under the same name. Both MATLAB exporters retain `info.totint` only when its dimensions match.
- New H5 exports declare `reference_maps_schema: eels_reference_maps_v1`, `total_counts_status: available|missing_in_source|missing_in_input`, and map `source_field: EELS.info.totint` with explicit h5py storage `axis_order`. Only confirmed MAT absence means `missing_in_source`; a Python input without retained counts uses `missing_in_input`. An existing invalid source map fails conversion; it must not be silently omitted. Legacy files remain readable. Both MATLAB exporters share `write_eels_reference_maps.m`; batch input/output may be sibling folders. Keep each multi-EELS object's map under its own group. See `docs/H5数据格式与总计数.md` for the contract and single-object UI copies.
- Candidate centers use all-row-mean error-function midpoints; label as approximate candidates, not independently calibrated structural interfaces. Spectral references come from fixed left/right strips, independent of the user's selected ROIs, and use 15-45 plus 55-75 meV. These overlays do not enter peak recipes or the JSON export contract. See `docs/参考图与候选界面.md`.
- Optional contrast region fitting is off by default. Fit/preview/model/percentage/background changes MUST preserve all existing ROIs, previews and peak results. Only the explicit Apply action updates the three rectangles and invalidates changed regions; afterwards all existing manual handles/translation/redraw remain available. Defaults: Si C<=1%, Interface C=10-90%, Ge C>=99%, full y extent. Percentages are plateau contrast, not chemical abundance. Reject outside-image or empty-pixel proposals without silently clipping or inventing bulk regions. Fit both erf and logistic to all-y means of retained total counts or the cached two-reference map; do not label either source HAADF.
- `/api/region-fit` uses a small profile in a dedicated subprocess with an 8-second kill/reap deadline and bounded evaluations. The frontend recovers after 12 seconds, supports cancellation, and discards late/stale responses on input/dataset changes. Preserve/export existing results before upgrading the daily service at 8768. Tests: `tests/test_region_fit.py` and `node tests/browser_region_fit.cjs` (isolated fixture; checks preview/apply separation, manual adjustment, both models, thresholds, failures, cancellation, timeout and responsive layout). See `docs/衬度拟合选区.md`.
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
