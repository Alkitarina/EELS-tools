"""Small synthetic checks for reuse; never bootstraps the user's measurements."""
import copy
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode=True
import numpy as np
import matplotlib.pyplot as plt
import pca_data as base
from eelsfit.io import EELSData
from plot_eels import parse_current
from plots import mark
from uncertainty import complete_missing


def main():
    assert parse_current('RT1')==(0,1)
    assert parse_current('RT-3')==(0,3)
    assert parse_current('5.5mA')==(5.5,1)
    try:parse_current('eels_results_123')
    except ValueError:pass
    else:raise AssertionError('An ambiguous filename must not guess a current')
    fig,ax=plt.subplots()
    mark(ax,30,1,None,horizontal=True)
    assert len(ax.containers)==0, 'Missing SD must not be plotted as a zero-length error bar'
    mark(ax,30,1,2.5,horizontal=True)
    mark(ax,30,7,2.5,horizontal=False)
    plt.close(fig)
    energy=np.linspace(10,80,141)
    rng=np.random.default_rng(12)
    profile=np.exp(-0.5*((energy-35)/6)**2)
    cube=(1+0.02*rng.normal(size=(5,6,1)))*profile+0.0002*rng.normal(size=(5,6,len(energy)))
    model=base.fit_pca_model(cube.reshape(-1,len(energy)),2)
    restored=base.analyze_pca_reconstruction(model,energy,cube.reshape(-1,len(energy)),1,5,0.05)
    p=restored.peaks[0]
    result=dict(region='Si',mode='pca',peaks=[dict(index=1,center_meV=p.center_meV,
        center_std_meV=None,center_ci95=None,width_meV=p.width_meV,width_std_meV=None,
        width_ci95=None,prominence=p.prominence,prominence_std=None,prominence_ci95=None)],
        uncertainty=dict(status='not_computed'))
    original=copy.deepcopy(result)
    recipe=dict(region='Si',mode='pca',settings=dict(component_count=2,peak_count=1))
    data=EELSData(cube,np.arange(6.),np.arange(5.),energy,Path('synthetic.h5'),{})
    metadata={'checksum':base.data_checksum(data)}
    options=dict(compute=True,resamples=6,block_size=2,seed=20260728,workers=1)
    parent=Path(__file__).resolve().parent
    temporary=parent/'.tmp_workflow_self_check'
    if temporary.exists():raise RuntimeError(f'Existing test directory must be reviewed: {temporary}')
    temporary.mkdir()
    try:
        unchanged,origin=complete_missing(result,recipe,metadata,restored,cube,cube,temporary,'synthetic',
                                         {**options,'compute':False},base.ROOT)
        assert unchanged==original and origin=='unavailable'
        updated,origin=complete_missing(result,recipe,metadata,restored,cube,cube,temporary,'synthetic',options,base.ROOT)
        assert origin=='computed_supplement' and result==original
        assert updated['uncertainty']['n_success']>=2
        assert np.isfinite(updated['peaks'][0]['center_std_meV'])
        cached,_=complete_missing(result,recipe,metadata,restored,cube,cube,temporary,'synthetic',options,base.ROOT)
        assert cached==updated
        try:
            complete_missing(result,recipe,metadata,restored,cube,cube,temporary,'synthetic',
                             {**options,'seed':options['seed']+1},base.ROOT)
        except ValueError:pass
        else:raise AssertionError('Changed inputs must reject a cached uncertainty result')
    finally:
        resolved=temporary.resolve()
        if resolved.parent!=parent or resolved.name!='.tmp_workflow_self_check':
            raise RuntimeError(f'Refusing cleanup outside the exact test directory: {resolved}')
        shutil.rmtree(resolved)
    print('PASS: current parsing, unavailable SD, error endpoints, synthetic bootstrap, source preservation, cache reuse and mismatch rejection')


if __name__=='__main__':main()
