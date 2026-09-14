"""Recalculate PCA exports with one prominence override, preserving saved recipes."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'EELS_Python_Analysis'))

import numpy as np
from threadpoolctl import threadpool_limits
from eels_web import AnalysisService, PCASettings, ROI, RUNTIME_SOFTWARE, _serialize_pca_analysis
from eelsfit.bootstrap_runtime import INDEPENDENT_SCHEME, LEGACY_SCHEME, resolve_workers
from eelsfit.exporting import build_result_document, data_checksum, json_text, software_identity
from eelsfit.io import load_eels_hdf5
from eelsfit.pca import spatial_block_pca_bootstrap


def write_json(path, document):
    path.write_text(json_text(document) + '\n', encoding='utf-8')


def checked_document(path):
    doc = json.loads(path.read_text(encoding='utf-8-sig'))
    if doc.get('format') != 'eels_peak_results_v1':
        raise ValueError(f'Unsupported result format: {path}')
    results, recipes = doc['results'], doc['reproducibility']['analyses']
    result_map = {r['result_id']: r for r in results}
    ids = [r['result_id'] for r in recipes]
    if (len(result_map) != len(results) or len(set(ids)) != len(ids)
            or set(ids) != set(result_map) or not results):
        raise ValueError(f'Result/recipe IDs do not match: {path}')
    if sorted(r['region'] for r in results) != ['Ge', 'Interface', 'Si']:
        raise ValueError(f'Expected exactly three material regions: {path}')
    for recipe in recipes:
        result = result_map[recipe['result_id']]
        if recipe['mode'] != 'pca' or result['mode'] != 'pca' or recipe['region'] != result['region']:
            raise ValueError(f'Expected matching PCA analyses: {path}')
        if not result['fit_success']:
            raise ValueError(f'Unsuccessful source analysis: {path}')
    if doc['units']['energy'] != 'meV' or doc['units']['position'] != 'nm':
        raise ValueError(f'Unexpected source units: {path}')
    return doc


def software_changes(doc):
    old = doc['reproducibility']['software']
    for key in ('algorithm_id', 'source_hash_scope', 'python', 'packages', 'random_generator'):
        if old.get(key) != RUNTIME_SOFTWARE[key]:
            raise ValueError(f'Scientific software/environment differs: {key}')
    changed = [name for name in set(old['source_sha256']) | set(RUNTIME_SOFTWARE['source_sha256'])
               if old['source_sha256'].get(name) != RUNTIME_SOFTWARE['source_sha256'].get(name)]
    # Integration changes are recorded and require numeric-data plus baseline checks.
    # Changes to PCA, bootstrap, export/replay or numerical core fail closed.
    if set(changed) - {'eels_web.py', 'eelsfit/io.py'}:
        raise ValueError(f'Numerical implementation differs: {changed}')
    return sorted(changed)


def baseline_check(analysis, original):
    result = analysis.result
    old = original['peaks']
    if analysis.pixel_count != original['pixel_count'] or len(result.peaks) != len(old):
        raise ValueError('Source ROI or baseline peak count no longer reproduces')
    for field, attr, atol in (('center_meV', 'center_meV', 1e-6),
                              ('width_meV', 'width_meV', 1e-6),
                              ('prominence', 'prominence', 1e-6)):
        if not np.allclose([p[field] for p in old], [getattr(p, attr) for p in result.peaks],
                           rtol=0 if field == 'center_meV' else 1e-8, atol=atol):
            raise ValueError(f'Source baseline does not reproduce: {field}')
    if not np.isclose(result.reconstruction_rmse, original['rmse'], rtol=1e-8, atol=1e-7):
        raise ValueError('Source baseline reconstruction RMSE does not reproduce')
    pca = original['pca']
    if (result.n_components != pca['component_count']
            or analysis.model_sample_count != pca['training_spectra']
            or not np.allclose(result.explained_variance_ratio, pca['explained_variance_ratio'],
                               rtol=1e-8, atol=1e-10)):
        raise ValueError('Source PCA model does not reproduce')
    return max((abs(p['center_meV'] - q.center_meV)
                for p, q in zip(old, result.peaks, strict=True)), default=0.0)


def verify_recipe(original, new, fraction):
    expected = copy.deepcopy(original['settings'])
    expected['min_prominence_fraction'] = fraction
    if new['settings'] != expected:
        raise ValueError('Unexpected analysis setting changed')
    for key in ('mode', 'region', 'roi', 'fit_min', 'fit_max', 'resolved_fit_config'):
        if new[key] != original[key]:
            raise ValueError(f'Unexpected recipe change: {key}')
    if new['result_id'] == original['result_id'] or new['created_utc'] == original['created_utc']:
        raise ValueError('Recalculated results need fresh identity and timestamp')
    before, after = original['bootstrap'], new['bootstrap']
    if before is None:
        if after is not None:
            raise ValueError('Unrequested bootstrap added')
    elif before != after:
        raise ValueError('Saved bootstrap parameters changed')


def recalculate(path, doc, fraction):
    start = time.monotonic()
    metadata = doc['reproducibility']['data']
    data = load_eels_hdf5(metadata['source'])
    if list(data.shape) != metadata['shape'] or data_checksum(data) != metadata['checksum']:
        raise ValueError(f'H5 does not match the recorded numeric input: {path}')
    original_results = {r['result_id']: r for r in doc['results']}
    analyses, checks = [], []
    service = AnalysisService(data)  # Independent in-process state; no web server or active session.
    try:
        for recipe in doc['reproducibility']['analyses']:
            region = recipe['region']
            original = original_results[recipe['result_id']]
            roi = ROI(**recipe['roi'])
            lower, upper = recipe['fit_min'], recipe['fit_max']
            baseline = service._pca_analyze(region, roi, lower, upper, PCASettings(**recipe['settings']))
            delta = baseline_check(baseline, original)
            settings = {**recipe['settings'], 'min_prominence_fraction': fraction}
            analysis = service._pca_analyze(region, roi, lower, upper, PCASettings(**settings))
            if not np.array_equal(baseline.result.reconstructed, analysis.result.reconstructed):
                raise ValueError('A prominence-only change must preserve the PCA spectrum')
            bootstrap = recipe['bootstrap']
            print(f'{path.stem}/{region}: {len(original["peaks"])} -> {len(analysis.result.peaks)} peaks', flush=True)
            if bootstrap is not None:
                if bootstrap['method'] != 'spatial_block_pca':
                    raise ValueError('Unexpected saved bootstrap method')
                energy, full, roi_cube, _, _ = service._extract_pca_cubes(roi, lower, upper)
                scheme = bootstrap.get('rng_scheme', LEGACY_SCHEME)
                workers = resolve_workers(bootstrap.get('workers', 1), bootstrap['n_resamples'],
                                          full.nbytes + roi_cube.nbytes, backend='thread')
                if workers != bootstrap.get('workers', 1):
                    raise ValueError('Cannot preserve the saved worker count')
                if scheme == INDEPENDENT_SCHEME and bootstrap.get('blas_threads') != 1:
                    raise ValueError('Expected one BLAS thread for independent resampling')
                def report(done, total):
                    if done % 100 == 0 or done == total:
                        print(f'  {path.stem}/{region}: bootstrap {done}/{total}', flush=True)
                analysis.bootstrap = spatial_block_pca_bootstrap(
                    energy, full, roi_cube, analysis.result, n_resamples=bootstrap['n_resamples'],
                    block_size_pixels=bootstrap['block_size'], seed=bootstrap['seed'],
                    workers=workers, rng_scheme=scheme, progress=report)
                if analysis.bootstrap.n_success < 2:
                    raise ValueError('Fewer than two successful samples; SD cannot be calculated')
                if analysis.bootstrap.block_size_pixels != bootstrap['actual_block_size']:
                    raise ValueError('Spatial block size changed')
                analysis.recipe['bootstrap'] = copy.deepcopy(bootstrap)
            analyses.append(analysis)
            checks.append(dict(region=region, parent_result_id=recipe['result_id'],
                               result_id=analysis.result_id, old_peaks=len(original['peaks']),
                               new_peaks=len(analysis.result.peaks), baseline_max_center_delta_meV=delta,
                               pca_spectrum_unchanged=True))
        output = build_result_document(data, analyses, [_serialize_pca_analysis(a) for a in analyses], RUNTIME_SOFTWARE)
    finally:
        service.shutdown()
    for old, new in zip(doc['reproducibility']['analyses'], output['reproducibility']['analyses'], strict=True):
        verify_recipe(old, new, fraction)
    changes = software_changes(doc)
    output['reproducibility']['derivation'] = dict(
        method='pca_recipe_prominence_override_v1', parent_json=str(path),
        parent_exported_utc=doc['exported_utc'], min_prominence_percent=fraction * 100,
        min_prominence_fraction=fraction, integration_source_changes=changes,
        source_baseline_verified=True, source_json_unchanged=True,
        uncertainty_recomputed=any(a.bootstrap is not None for a in analyses), result_mapping=checks)
    output['reproducibility']['notes'].append(
        '由父 JSON 的实际配方重新计算，仅覆盖最小突出度；该比例乘以 PCA 谱的最大值减最小值。'
        '其余 ROI、能窗、主成分数、最大峰数、最小峰间距及抽样参数均保留；误差按新阈值重新抽样。')
    return output, dict(source_json=str(path), elapsed_seconds=time.monotonic() - start,
                        integration_source_changes=changes, regions=checks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--min-prominence-percent', type=float, required=True)
    parser.add_argument('--resume', action='store_true', help='Reuse only completed files matching saved run/output digests')
    args = parser.parse_args()
    source, target = args.input_dir.resolve(), args.output_dir.resolve()
    fraction = args.min_prominence_percent / 100.0
    if not np.isfinite(fraction) or not 0 <= fraction < 1:
        raise ValueError('Prominence percent must be in [0, 100)')
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('Use separate input and output directories, such as sibling try folders')
    inputs = {}
    for path in sorted(source.glob('*.json')):
        if json.loads(path.read_text(encoding='utf-8-sig')).get('format') == 'eels_peak_results_v1':
            doc = checked_document(path)
            software_changes(doc)
            inputs[path] = (doc, path.read_bytes())
    if not inputs:
        raise ValueError('No PCA result JSON files found')
    # Only selected source JSONs and this script are fingerprinted for resume integrity.
    config = dict(input_dir=str(source), min_prominence_fraction=fraction,
                  input_sha256={str(p): hashlib.sha256(raw).hexdigest() for p, (_, raw) in inputs.items()},
                  software=RUNTIME_SOFTWARE,
                  driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    state_path = target / '_recalculation' / 'state.json'
    if target.exists():
        if not args.resume or not state_path.is_file():
            raise ValueError(f'Output exists; select a new folder: {target}')
        state = json.loads(state_path.read_text(encoding='utf-8'))
        if state['config'] != config:
            raise ValueError('Resume inputs, parameters or software differ from the saved run')
    else:
        target.mkdir(parents=True)
        state_path.parent.mkdir()
        state = dict(format='eels_pca_recalculation_state_v1', config=config,
                     started_utc=datetime.now(timezone.utc).isoformat(), status='running', completed=[])
        write_json(state_path, state)
    completed = {r['source_json']: r for r in state['completed']}
    with threadpool_limits(limits=1, user_api='blas'):
        for path, (doc, raw) in inputs.items():
            output_path = target / path.name
            if str(path) in completed:
                saved = completed[str(path)]
                if not output_path.is_file() or hashlib.sha256(output_path.read_bytes()).hexdigest() != saved['output_sha256']:
                    raise ValueError(f'Completed output changed: {output_path}')
                print(f'Reusing verified completed file: {path.name}', flush=True)
                continue
            if output_path.exists():
                raise ValueError(f'Untracked output exists: {output_path}')
            result, report = recalculate(path, doc, fraction)
            if path.read_bytes() != raw:
                raise ValueError(f'Source JSON changed during calculation: {path}')
            with output_path.open('x', encoding='utf-8') as stream:
                stream.write(json_text(result) + '\n')
            report['output_sha256'] = hashlib.sha256(output_path.read_bytes()).hexdigest()
            state['completed'].append(report)
            write_json(state_path, state)
            print(f'Saved {output_path.name} ({report["elapsed_seconds"]:.1f} s)', flush=True)
    if software_identity(ROOT / 'EELS_Python_Analysis') != RUNTIME_SOFTWARE:
        raise ValueError('Analysis software changed while calculation was running')
    if any(p.read_bytes() != raw for p, (_, raw) in inputs.items()):
        raise ValueError('An input JSON changed during the run')
    state['status'] = 'complete'
    state['completed_utc'] = datetime.now(timezone.utc).isoformat()
    state['source_jsons_unchanged'] = True
    write_json(state_path, state)
    print(json.dumps(dict(output=str(target), files=len(inputs),
                          min_prominence_percent=args.min_prominence_percent, status='complete'), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
