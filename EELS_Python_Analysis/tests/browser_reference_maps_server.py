"""Isolated fixture server; never replaces a user's running data or results."""
import json
from pathlib import Path
import sys

import numpy as np
from scipy.special import erf
from werkzeug.serving import make_server

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from eels_web import create_app
from eelsfit.runtime_paths import test_runs_dir
from eelsfit.io import write_eels_hdf5
from tests.test_reference_maps import synthetic_reference_data


def main():
    output=Path(sys.argv[1]).resolve()
    if output.parent != test_runs_dir() or not output.name.startswith('.tmp_reference_browser_'):
        raise ValueError('Use a task-local fixture directory.')
    data=synthetic_reference_data()
    data.y_nm=np.arange(21,dtype=float)
    data.intensity=np.repeat(data.intensity[:1],21,axis=0)
    counts=100+200*0.5*(1+erf((data.x_nm-8.6)/(np.sqrt(2)*0.8)))
    data.total_counts=np.repeat(counts[None,:],21,axis=0)
    paths={'counts':str(output/'counts.h5'),'legacy':str(output/'legacy.h5')}
    write_eels_hdf5(data,paths['counts'])
    data.total_counts=None
    write_eels_hdf5(data,paths['legacy'])
    app=create_app(paths['counts'])
    app.config['IMPORT_ROOT']=output/'imports'
    app.config['UI_PREFERENCES_FILE']=output/'ui_preferences.json'
    with make_server('127.0.0.1',0,app,threaded=True) as server:
        print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','paths':paths}),flush=True)
        try:
            server.serve_forever()
        finally:
            app.extensions['eels_service'].shutdown()


if __name__=='__main__':main()
