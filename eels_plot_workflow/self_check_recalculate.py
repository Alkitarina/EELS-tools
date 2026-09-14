"""Synthetic weak-peak check for prominence override and fresh uncertainty."""
import copy
import json
from pathlib import Path
import shutil

import recalculate_pca as task
from eelsfit.io import EELSData, write_eels_hdf5


def main():
    parent = Path(__file__).resolve().parent
    temporary = parent / '.tmp_recalculate_self_check'
    if temporary.exists():
        raise RuntimeError(f'Existing test directory must be reviewed: {temporary}')
    temporary.mkdir()
    try:
        energy = task.np.linspace(10, 80, 281)
        rng = task.np.random.default_rng(61)
        profile = task.np.exp(-0.5 * ((energy - 35) / 3) ** 2)
        profile += 0.003 * task.np.exp(-0.5 * ((energy - 60) / 1.5) ** 2)
        cube = (1 + 0.01 * rng.normal(size=(5, 6, 1))) * profile
        cube += 1e-5 * rng.normal(size=cube.shape)
        data_path = temporary / 'synthetic.h5'
        data = EELSData(cube, task.np.arange(6.), task.np.arange(5.), energy, data_path, {})
        write_eels_hdf5(data, data_path)
        service = task.AnalysisService(task.load_eels_hdf5(data_path))
        analyses = []
        with task.threadpool_limits(limits=1, user_api='blas'):
            try:
                for index, region in enumerate(('Si', 'Interface', 'Ge')):
                    roi = task.ROI(0, 5, 0, 4)
                    settings = task.PCASettings(2, 3, 5., 0.005, 61)
                    a = service._pca_analyze(region, roi, 10., 80., settings)
                    assert len(a.result.peaks) == 1, 'Weak peak should fall below the initial 0.5% threshold'
                    e, full, local, _, _ = service._extract_pca_cubes(roi, 10., 80.)
                    a.bootstrap = task.spatial_block_pca_bootstrap(
                        e, full, local, a.result, n_resamples=8, block_size_pixels=2,
                        seed=101 + index, workers=1, rng_scheme=task.INDEPENDENT_SCHEME)
                    a.recipe['bootstrap'] = dict(method='spatial_block_pca', n_resamples=8,
                        block_size=2, actual_block_size=2, seed=101 + index,
                        rng_scheme=task.INDEPENDENT_SCHEME, workers=1, blas_threads=1, backend='serial')
                    analyses.append(a)
                doc = task.build_result_document(service.data, analyses,
                    [task._serialize_pca_analysis(a) for a in analyses], task.RUNTIME_SOFTWARE)
            finally:
                service.shutdown()
            path = temporary / 'RT.json'
            task.write_json(path, doc)
            original_bytes = path.read_bytes()
            original = copy.deepcopy(doc)
            result, report = task.recalculate(path, doc, 0.1 / 100.)
        assert doc == original and path.read_bytes() == original_bytes
        assert all(c['pca_spectrum_unchanged'] for c in report['regions'])
        for old, new in zip(original['results'], result['results'], strict=True):
            assert len(old['peaks']) == 1 and len(new['peaks']) == 2
            assert abs(new['peaks'][1]['center_meV'] - 60) < 0.1
            assert new['uncertainty']['n_requested'] == 8 and new['uncertainty']['n_success'] >= 2
            assert all(task.np.isfinite(p['center_std_meV']) for p in new['peaks'])
        for old, new in zip(original['reproducibility']['analyses'], result['reproducibility']['analyses'], strict=True):
            task.verify_recipe(old, new, 0.001)
        without_bootstrap = copy.deepcopy(original)
        for recipe in without_bootstrap['reproducibility']['analyses']:
            recipe['bootstrap'] = None
        for record in without_bootstrap['results']:
            record['uncertainty'].update(status='not_computed', n_requested=0, n_success=0, n_failed=0)
            for peak in record['peaks']:
                for key in list(peak):
                    if '_std' in key or '_ci95' in key:
                        peak[key] = None
        with task.threadpool_limits(limits=1, user_api='blas'):
            no_error_result, _ = task.recalculate(path, without_bootstrap, 0.001)
        assert no_error_result['reproducibility']['derivation']['uncertainty_recomputed'] is False
        assert all(p['center_std_meV'] is None for r in no_error_result['results'] for p in r['peaks'])
        # Explicit mutation must be rejected by the guard, including the actual region seed.
        bad = copy.deepcopy(result['reproducibility']['analyses'][0])
        bad['bootstrap']['seed'] += 1
        try:
            task.verify_recipe(original['reproducibility']['analyses'][0], bad, 0.001)
        except ValueError:
            pass
        else:
            raise AssertionError('Changed regional bootstrap seed must be rejected')
        json.loads(task.json_text(result))
    finally:
        resolved = temporary.resolve()
        if resolved.parent != parent or resolved.name != '.tmp_recalculate_self_check':
            raise RuntimeError(f'Refusing cleanup outside exact test directory: {resolved}')
        shutil.rmtree(resolved)
    print('PASS: 0.1% weak-peak detection, fresh bootstrap, retained recipes/seeds, source preservation, seed-mutation rejection')


if __name__ == '__main__':
    main()
