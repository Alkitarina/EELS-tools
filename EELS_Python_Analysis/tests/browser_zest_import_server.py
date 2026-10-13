"""Isolated ZIP import UI fixture; never loads a daily analysis session."""
import json
import io
import os
from pathlib import Path
import sys
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from werkzeug.serving import make_server
from eels_web import create_app
from eelsfit.runtime_paths import test_runs_dir
from tests.test_multivariate_workflow import synthetic_data
from tests.test_zest_import import make_archive


if __name__ == '__main__':
    folder = Path(sys.argv[1]).resolve()
    if folder.parent != test_runs_dir() or not folder.name.startswith('.tmp_zest_browser_'):
        raise ValueError('Use a task-owned fixture directory')
    calibrated, old = folder/'已标定 ZEST.zip', folder/'旧版无尺寸.zip'
    make_archive(calibrated, data=synthetic_data(), producer_schema=True, counts=True)
    make_archive(old, geometry=False, parallel=False)
    app = create_app()
    app.config.update(IMPORT_ROOT=folder/'imports', UI_PREFERENCES_FILE=folder/'preferences.json')
    with make_server('127.0.0.1', 0, app, threaded=True) as server:
        fixture = dict(url=f'http://127.0.0.1:{server.server_port}', calibrated=str(calibrated), old=str(old))
        if os.environ.get('EELS_ZEST_EXAMPLE'):
            with zipfile.ZipFile(os.environ['EELS_ZEST_EXAMPLE']) as archive:
                with np.load(io.BytesIO(archive.read('maps.npz')), allow_pickle=False) as maps:
                    fixture['example_counts'] = maps['counts_source_totint'].tolist()
        print(json.dumps(fixture), flush=True)
        try:
            server.serve_forever()
        finally:
            app.extensions['eels_service'].shutdown()
