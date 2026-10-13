"""Exercise fixed-port launch/reuse/conflict/stop behavior on synthetic data."""

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
from eelsfit.runtime_paths import test_runs_dir
import threading
import time
import unittest
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import h5py
import numpy as np


class OtherApplication(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(getattr(self.server, "health_payload",
                                  {"status": "ok", "application": "different-application"})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@unittest.skipUnless(os.name == "nt", "PowerShell launcher is Windows-specific")
class LauncherTests(unittest.TestCase):
    def test_fixed_port_conflicts_reuse_and_identity_checked_stop(self):
        root = Path(__file__).resolve().parents[1]
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        other = ThreadingHTTPServer(("127.0.0.1", 0), OtherApplication)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        running_ports = set()
        with tempfile.TemporaryDirectory(prefix=".tmp_launcher_", dir=test_runs_dir()) as temporary:
            temp = Path(temporary)
            data = temp / "启动 data.h5"
            with h5py.File(data, "w") as handle:
                handle["intensity"] = np.ones((3, 4, 17))
                handle["x_nm"] = np.arange(4)
                handle["y_nm"] = np.arange(3)
                handle["energy_meV"] = np.arange(17)

            def run(script, *args):
                # Detached children can retain pipe handles on Windows. Capture to a file.
                output_path = temp / (uuid.uuid4().hex + ".log")
                with output_path.open("wb") as stream:
                    result = subprocess.run(
                        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                         "-File", str(root / script), *map(str, args)],
                        # Exercise a non-project cwd without a hidden Windows console
                        # retaining a directory handle to the disposable fixture folder.
                        cwd=root.parent, stdout=stream, stderr=subprocess.STDOUT, timeout=90,
                    )
                return result.returncode, output_path.read_bytes().decode("utf-8", errors="replace")

            def health(port):
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                    return json.load(response)

            try:
                # A compatible session elsewhere must never pull the requested UI away.
                port = other.server_port
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    separate_port = probe.getsockname()[1]
                code, output = run("start_web.ps1", "-NoBrowser", "-Port", separate_port,
                                   "-DataFile", data, "-LogDir", temp / "logs")
                self.assertEqual(code, 0, output)
                running_ports.add(separate_port)
                separate = health(separate_port)
                args = ["-NoBrowser", "-Port", port, "-DataFile", data,
                        "-LogDir", temp / "logs", "-MaxPortScan", 5]
                saved_path = temp / "logs" / "last_launch.json"
                saved_before_failure = saved_path.read_bytes()
                code, output = run("start_web.ps1", *args)
                self.assertNotEqual(code, 0, output)
                self.assertIn("occupied by another application", output)
                self.assertNotIn("EELS_WEB_URL=", output)
                self.assertEqual(saved_path.read_bytes(), saved_before_failure)
                self.assertEqual(health(separate_port)["instance_id"], separate["instance_id"])
                code, _ = run("stop_web.ps1", "-Port", port)
                self.assertNotEqual(code, 0)
                self.assertEqual(health(port)["application"], "different-application")
                # Even matching capabilities do not make old source code reusable.
                other.health_payload = dict(separate, ui_source_id="0" * 64)
                code, output = run("start_web.ps1", *args)
                self.assertNotEqual(code, 0, output)
                self.assertIn("older EELS backend", output)
                self.assertNotIn("EELS_WEB_URL=", output)
                self.assertEqual(health(separate_port)["instance_id"], separate["instance_id"])
                self.assertEqual(saved_path.read_bytes(), saved_before_failure)
                other.shutdown()
                other.server_close()
                thread.join(timeout=3)
                code, output = run("start_web.ps1", *args)
                self.assertEqual(code, 0, output)
                self.assertIn(f"EELS_WEB_URL=http://127.0.0.1:{port}", output)
                running_ports.add(port)
                first = health(port)
                self.assertNotEqual(first["instance_id"], separate["instance_id"])
                self.assertEqual(first["workflow_version"], "eels_interface_workflow_v1")
                self.assertEqual(first["workflow_revision"], "interface_y_stripes_v1")
                self.assertEqual(first["ui_preferences_version"], "eels_ui_preferences_v1")
                self.assertEqual(first["strip_data_version"], "independent_full_x_v1")
                self.assertRegex(first["ui_source_id"], r"^[0-9a-f]{64}$")
                self.assertEqual(Path(first["source"]), data)
                saved = json.loads(saved_path.read_text(encoding="utf-8-sig"))
                self.assertEqual(saved["format"], "eels_startup_v1")
                self.assertEqual(Path(saved["project_root"]), root)
                self.assertEqual(Path(saved["data_file"]), data)
                self.assertIn("EELS_WEB_PID=" + str(first["pid"]), output)
                code, output = run("start_web.ps1", *args)
                self.assertEqual(code, 0, output)
                self.assertIn("Reusing existing EELS server", output)
                self.assertEqual(health(port)["instance_id"], first["instance_id"])
                plain_args = ["-NoBrowser", "-Port", port, "-LogDir", temp / "logs"]
                code, output = run("start_web.ps1", *plain_args)
                self.assertEqual(code, 0, output)
                self.assertIn("Reusing existing EELS server", output)
                self.assertEqual(health(port)["instance_id"], first["instance_id"])
                saved_before_failure = saved_path.read_bytes()
                code, _ = run("start_web.ps1", *plain_args, "-DataFile", temp / "missing.h5")
                self.assertNotEqual(code, 0)
                self.assertEqual(health(port)["pid"], first["pid"])
                self.assertEqual(saved_path.read_bytes(), saved_before_failure)
                other_data = temp / "different.h5"
                shutil.copy2(data, other_data)
                code, output = run("start_web.ps1", *plain_args, "-DataFile", other_data)
                self.assertNotEqual(code, 0, output)
                self.assertIn("already serves different data", output)
                self.assertEqual(health(port)["instance_id"], first["instance_id"])
                self.assertEqual(saved_path.read_bytes(), saved_before_failure)
                code, output = run("stop_web.ps1", "-Port", port)
                self.assertEqual(code, 0, output)
                with self.assertRaises(OSError):
                    health(port)
                running_ports.remove(port)
                # A fresh launch ignores even a valid saved path and starts empty.
                code, output = run("start_web.ps1", *plain_args)
                self.assertEqual(code, 0, output)
                self.assertIn("No data loaded", output)
                match = re.search(r"EELS_WEB_URL=(http://127\.0\.0\.1:(\d+))", output)
                self.assertIsNotNone(match, output)
                self.assertEqual(int(match.group(2)), port)
                running_ports.add(port)
                self.assertIsNone(health(port)["source"])
                self.assertFalse(health(port)["dataset_loaded"])
                self.assertEqual(health(port)["empty_startup_version"], "deferred_data_v1")
                self.assertEqual(saved_path.read_bytes(), saved_before_failure)
                self.assertNotEqual(health(port)["instance_id"], first["instance_id"])
                code, output = run("stop_web.ps1", "-Port", port)
                self.assertEqual(code, 0, output)
                running_ports.remove(port)
            finally:
                for owned_port in running_ports:
                    run("stop_web.ps1", "-Port", owned_port, "-Force")
                other.shutdown()
                other.server_close()
                thread.join(timeout=3)
                time.sleep(0.15)

    def test_saved_paths_and_malformed_history_are_ignored_on_empty_start(self):
        root = Path(__file__).resolve().parents[1]
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        with tempfile.TemporaryDirectory(prefix=".tmp_launcher_empty_", dir=test_runs_dir()) as temporary:
            folder = Path(temporary)
            logs = folder / "logs"
            logs.mkdir()
            state_path = logs / "last_launch.json"
            invalid_h5 = folder / "不要加载.h5"
            invalid_h5.write_bytes(b"Startup must never open this file")
            common = {"format": "eels_startup_v1", "project_root": str(root)}
            cases = [None, "{broken json", dict(common, data_file=str(invalid_h5)),
                     dict(common, data_file=str(folder / "missing.h5"))]
            for index, payload in enumerate(cases):
                with self.subTest(case=index), socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                if payload is not None:
                    state_path.write_text(payload if isinstance(payload, str) else
                                          json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                prior = state_path.read_bytes() if state_path.exists() else None
                output_path = folder / f"launch_{index}.log"
                try:
                    with output_path.open("wb") as stream:
                        result = subprocess.run(
                            [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-File", str(root / "start_web.ps1"), "-Port", str(port),
                             "-LogDir", str(logs), "-NoBrowser"],
                            cwd=root.parent, stdout=stream, stderr=subprocess.STDOUT, timeout=30)
                    output = output_path.read_text(encoding="utf-8", errors="replace")
                    self.assertEqual(result.returncode, 0, output)
                    self.assertIn("No data loaded", output)
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                        health = json.load(response)
                    self.assertIsNone(health["source"])
                    self.assertFalse(health["dataset_loaded"])
                    self.assertEqual(state_path.read_bytes() if state_path.exists() else None, prior)
                finally:
                    subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                    "-File", str(root / "stop_web.ps1"), "-Port", str(port)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            time.sleep(.15)


if __name__ == "__main__":
    unittest.main()
