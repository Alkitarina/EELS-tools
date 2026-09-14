"""Exercise real Windows launch/reuse/port-scan/stop behavior on synthetic data."""

import json
import os
import re
import subprocess
import tempfile
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
        body = b'{"status":"ok","application":"different-application"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@unittest.skipUnless(os.name == "nt", "PowerShell launcher is Windows-specific")
class LauncherTests(unittest.TestCase):
    def test_port_conflict_reuse_and_identity_checked_stop(self):
        root = Path(__file__).resolve().parents[1]
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        other = ThreadingHTTPServer(("127.0.0.1", 0), OtherApplication)
        thread = threading.Thread(target=other.serve_forever, daemon=True)
        thread.start()
        url = None
        with tempfile.TemporaryDirectory(prefix=".tmp_launcher_", dir=root) as temporary:
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
                        cwd=temp, stdout=stream, stderr=subprocess.STDOUT, timeout=90,
                    )
                return result.returncode, output_path.read_bytes().decode("utf-8", errors="replace")

            def health():
                with urllib.request.urlopen(url + "/health", timeout=3) as response:
                    return json.load(response)

            try:
                args = ["-NoBrowser", "-Port", other.server_port, "-DataFile", data,
                        "-LogDir", temp / "logs", "-MaxPortScan", 5]
                code, output = run("start_web.ps1", *args)
                self.assertEqual(code, 0, output)
                match = re.search(r"EELS_WEB_URL=(http://127\.0\.0\.1:(\d+))", output)
                self.assertIsNotNone(match, output)
                url, port = match.group(1), int(match.group(2))
                self.assertGreater(port, other.server_port)
                first = health()
                self.assertEqual(Path(first["source"]), data)
                self.assertIn("EELS_WEB_PID=" + str(first["pid"]), output)
                code, output = run("start_web.ps1", *args)
                self.assertEqual(code, 0, output)
                self.assertIn("Reusing existing EELS server", output)
                self.assertEqual(health()["instance_id"], first["instance_id"])
                code, _ = run("stop_web.ps1", "-Port", other.server_port)
                self.assertNotEqual(code, 0)
                with urllib.request.urlopen(f"http://127.0.0.1:{other.server_port}/health") as response:
                    self.assertEqual(response.status, 200)
                code, _ = run("start_web.ps1", "-NoBrowser", "-DataFile", temp / "missing.h5")
                self.assertNotEqual(code, 0)
                self.assertEqual(health()["pid"], first["pid"])
                code, output = run("stop_web.ps1", "-Port", port)
                self.assertEqual(code, 0, output)
                with self.assertRaises(OSError):
                    health()
                url = None
            finally:
                if url:
                    run("stop_web.ps1", "-Port", port, "-Force")
                other.shutdown()
                other.server_close()
                thread.join(timeout=3)
                time.sleep(0.15)


if __name__ == "__main__":
    unittest.main()
