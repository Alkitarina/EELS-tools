"""Synthetic-only isolated browser fixture for the multivariate workspace."""
import json
from pathlib import Path
import sys
import numpy as np
from werkzeug.serving import make_server

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from eels_web import create_app
from eelsfit.runtime_paths import test_runs_dir
from eelsfit.io import write_eels_hdf5
from tests.test_multivariate_workflow import synthetic_data

if __name__=="__main__":
    folder=Path(sys.argv[1]).resolve()
    if folder.parent != test_runs_dir() or not folder.name.startswith(".tmp_workflow_browser_"):
        raise ValueError("Use a task-owned synthetic fixture directory.")
    path=folder/"synthetic.h5"
    data=synthetic_data(path)
    if len(sys.argv)>2 and sys.argv[2]=="roi-signed":
        data.intensity -= .8*np.exp(-((data.x_nm[None,:,None]-7.5)/1.1)**2)*np.exp(-.5*((data.energy_meV[None,None,:]-38)/1.1)**2)
    if not path.exists():
        write_eels_hdf5(data,path)
    app=create_app(path);app.config["IMPORT_ROOT"]=folder/"imports"
    app.config["UI_PREFERENCES_FILE"]=folder/"ui_preferences.json"
    with make_server("127.0.0.1",0,app,threaded=True) as server:
        print(json.dumps({"url":f"http://127.0.0.1:{server.server_port}","path":str(path)}),flush=True)
        try:server.serve_forever()
        finally:app.extensions["eels_service"].shutdown()
