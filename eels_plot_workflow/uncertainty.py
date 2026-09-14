"""Optional spatial-block PCA uncertainty, saved as a separate derived artifact."""
import copy
import json
from datetime import datetime, timezone

import numpy as np
from eelsfit.bootstrap_runtime import INDEPENDENT_SCHEME, resolve_workers
from eelsfit.exporting import software_identity
from eelsfit.pca import spatial_block_pca_bootstrap


def complete_missing(result, recipe, data_metadata, restored, full_cube, roi_cube,
                     output, sample_id, options, project_root):
    available = result['uncertainty']['status'] == 'computed'
    missing = [not available or p['center_std_meV'] is None for p in result['peaks']]
    if not any(missing):
        return result, 'original_json'
    if not options or not options.get('compute') or not result['peaks']:
        return result, 'unavailable' if all(missing) else 'partly_available'
    region = result['region']
    settings = dict(n_resamples=options['resamples'], block_size_pixels=options['block_size'],
                    seed=options['seed']+{'Si':0,'Interface':1,'Ge':2}[region],
                    rng_scheme=INDEPENDENT_SCHEME)
    settings['workers'] = resolve_workers(options['workers'], settings['n_resamples'],
                                         full_cube.nbytes+roi_cube.nbytes, backend='thread')
    identity = dict(data_checksum=data_metadata['checksum'], result=result, recipe=recipe,
                    bootstrap=settings, software=software_identity(project_root/'EELS_Python_Analysis'))
    folder = output/'uncertainty_supplements'
    folder.mkdir(exist_ok=True)
    path = folder/f'{sample_id}_{region}.json'
    cached = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    if cached is not None:
        if cached['identity'] != identity:
            raise ValueError(f'Uncertainty cache no longer matches its inputs: {path}')
        return cached['plot_result'], 'computed_supplement'
    print(f'Bootstrap {sample_id}/{region}: {settings["n_resamples"]} resamples, '
          f'{settings["workers"]} CPU workers', flush=True)
    def progress(done, total):
        if done % 100 == 0 or done == total:
            print(f'Bootstrap {sample_id}/{region}: {done}/{total}', flush=True)
    samples = spatial_block_pca_bootstrap(restored.energy_meV, full_cube, roi_cube,
                                          restored, progress=progress, **settings)
    if samples.n_success < 2:
        raise ValueError(f'Only {samples.n_success} bootstrap sample succeeded for {sample_id}/{region}; SD unavailable')
    updated = copy.deepcopy(result)
    arrays = {'center':samples.centers_meV,'width':samples.widths_meV,'prominence':samples.prominences}
    for index, peak in enumerate(updated['peaks']):
        for name, values in arrays.items():
            std_key = f'{name}_std_meV' if name != 'prominence' else 'prominence_std'
            ci_key = f'{name}_ci95'
            if peak.get(std_key) is None:
                peak[std_key] = float(np.std(values[:,index],ddof=1))
            if peak.get(ci_key) is None:
                peak[ci_key] = np.percentile(values[:,index],[2.5,97.5]).tolist()
    updated['uncertainty'] = dict(status='computed',method='spatial_block_pca',
        n_requested=samples.n_requested,n_success=samples.n_success,n_failed=samples.n_failed,
        ci_percentiles=[2.5,97.5],std_ddof=1)
    artifact = dict(format='eels_plot_uncertainty_supplement_v1',
                    created_utc=datetime.now(timezone.utc).isoformat(),identity=identity,
                    source_json_unchanged=True,plot_result=updated)
    path.write_text(json.dumps(artifact,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return updated, 'computed_supplement'
