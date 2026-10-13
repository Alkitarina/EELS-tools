"""Isolated synthetic datasets/server for browser_import_settings.cjs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import h5py
import numpy as np
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eels_web import create_app
from eelsfit.runtime_paths import test_runs_dir


def main() -> None:
    output = Path(sys.argv[1]).resolve()
    if output.parent != test_runs_dir() or not output.name.startswith(".tmp_import_settings_"):
        raise ValueError("Use a task-local fixture directory.")
    energy = np.linspace(-20, 150, 341)
    x = np.arange(11, dtype=float)
    y = np.arange(7, dtype=float)
    fixtures = {
        "initial": (energy, x, y, 12, 1),
        "compatible": (energy, x, y, 40, 3),
        "partial": (np.linspace(20, 70, 101), x, y, 8, 2),
        "disjoint": (np.linspace(200, 300, 201), x, y, 6, 1),
        "sparse": (np.r_[np.arange(8), np.arange(90, 151)], x, y, 6, 1),
        "spatial": (energy, x + 20, np.arange(2, 5, dtype=float), 6, 1),
    }
    paths = {}
    for name, (e, xs, ys, zlp, scale) in fixtures.items():
        target = output / (name + ".h5")
        spectrum = 10 * np.exp(-0.5 * ((e - 35) / 5) ** 2)
        spectrum += 6 * np.exp(-0.5 * ((e - 60) / 4) ** 2) + 0.3
        cube = scale * (spectrum[None, None, :] +
                        np.arange(xs.size)[None, :, None] * 0.01 +
                        np.arange(ys.size)[:, None, None] * 0.02)
        with h5py.File(target, "x") as handle:
            for key, values in {"intensity": cube, "x_nm": xs, "y_nm": ys,
                                "energy_meV": e}.items():
                handle.create_dataset(key, data=values)
            handle.attrs["zlp_fwhm_meV"] = zlp
        paths[name] = str(target)
    paths["invalid"] = str(output / "invalid.h5")
    with h5py.File(paths["invalid"], "x"):
        pass
    app = create_app(paths["initial"])
    app.config["IMPORT_ROOT"] = output / "imports"
    app.config["UI_PREFERENCES_FILE"] = output / "ui_preferences.json"
    with make_server("127.0.0.1", 0, app, threaded=True) as server:
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "paths": paths}), flush=True)
        try:
            server.serve_forever()
        finally:
            app.extensions["eels_service"].shutdown()


if __name__ == "__main__":
    main()
