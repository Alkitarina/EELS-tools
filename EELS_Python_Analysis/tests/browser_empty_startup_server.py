"""Isolated blank service with synthetic H5/JSON fixtures and a pre-existing draft."""
import json
from pathlib import Path
import sys

from threadpoolctl import threadpool_limits
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eels_web import create_app
from eelsfit.exporting import json_text
from eelsfit.interface_workflow import build_profiles, fit_profile
from eelsfit.io import write_eels_hdf5
from eelsfit.multivariate import decompose
from eelsfit.runtime_paths import test_runs_dir
from eelsfit.workflow_export import export_document, software_identity
from tests.test_multivariate_workflow import synthetic_data, decomp_settings, profile_settings
from tests.test_bulk_region_workflow import fit_config


if __name__ == "__main__":
    folder = Path(sys.argv[1]).resolve()
    if folder.parent != test_runs_dir() or not folder.name.startswith(".tmp_empty_browser_"):
        raise ValueError("Use a task-owned fixture directory")
    path = folder / "synthetic.h5"
    workflow = folder / "流程.json"
    with threadpool_limits(limits=1):
        data = synthetic_data(path)
        if not path.exists(): write_eels_hdf5(data, path)
        if not workflow.exists():
            d = decompose(data, decomp_settings()); d["result_id"] = "d"
            p = build_profiles(data, d, {**profile_settings(), "bulk_scope": "global",
                "stripe_y_scope": "interface_roi", "strip_data_rule": "independent_full_x_v1"})
            p.update(result_id="p", decomposition_id="d")
            f = fit_profile(p, fit_config("Si")); f.update(result_id="f", profiles_id="p")
            document = export_document(data, dict(decomposition=d, profiles=p, fit=f), software_identity())
            workflow.write_text(json_text(document), encoding="utf-8")
    preferences = folder / "ui_preferences.json"
    if not preferences.exists():
        preferences.write_text(json.dumps({"format": "eels_ui_preferences_v1", "saved_utc": "2026-09-22T00:00:00Z",
            "writer": {"id": "previous-page", "sequence": 1}, "snapshot": {
                "dataset": {"source": str(path), "shape": {"y": 8, "x": 31, "energy": 141},
                            "x_nm": [0, 15], "y_nm": [0, 7], "energy_meV": [10, 80]},
                "controls": {"fit-mode": "pca", "pca-components": "2", "wf-rank": "2", "wf-peak-count": "7"},
                "workflow": {}, "legacy": {}, "details": {}}}), encoding="utf-8")
    app = create_app()
    app.config["IMPORT_ROOT"] = folder / "imports"
    app.config["UI_PREFERENCES_FILE"] = preferences
    with make_server("127.0.0.1", 0, app, threaded=True) as server:
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "path": str(path),
                          "workflow": str(workflow), "preferences": str(preferences)}), flush=True)
        try: server.serve_forever()
        finally: app.extensions["eels_service"].shutdown()
