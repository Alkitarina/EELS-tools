"""Cancellable, deadline-bounded subprocess jobs; no numerical work on Flask threads."""
import copy
import json
import os
from pathlib import Path
import pickle
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

from .multivariate import decompose
from .interface_workflow import build_profiles, fit_profile, fit_regions, fit_key
from .interface_localization import localize_interface
from .workflow_import import restore_workflow, validate_document
from .runtime_paths import runtime_work_dir

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("decomposition", "profiles", "fit")


class WorkflowManager:
    def __init__(self, service):
        self.service = service
        self.dataset_id = service.dataset_id
        self.results = {}
        self.fits = {}
        self.jobs = {}
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.closed = False

    @property
    def busy(self):
        with self.lock:
            return any(job["status"] in {"queued", "running"} for job in self.jobs.values())

    def synchronize(self):
        with self.lock:
            if self.dataset_id != self.service.dataset_id:
                self.results.clear()
                self.fits.clear()
                self.dataset_id = self.service.dataset_id

    def require(self, stage, expected):
        self.synchronize()
        if stage == "fit":
            for result in self.fits.values():
                if result["result_id"] == expected:
                    return result
        result = self.results.get(stage)
        if result is None or result["result_id"] != expected:
            raise ValueError("上一步结果已失效或尚未完成，请从对应步骤重新运行。")
        return result

    def start(self, payload, timeout=180):
        self.synchronize()
        stage = payload.get("stage")
        if stage not in (*STAGES, "fit_regions", "localization", "import"):
            raise ValueError("不支持的工作流步骤。")
        settings = copy.deepcopy(payload.get("settings", {}))
        if not isinstance(settings, dict):
            raise ValueError("步骤参数必须为对象。")
        with self.lock:
            if self.closed or self.busy:
                raise ValueError("已有工作流任务正在运行，请等待或取消后重试。")
            if stage == "import":
                document = copy.deepcopy(payload.get("document"))
                validate_document(document)
                work = {"stage": stage, "data": self.service.data, "document": document,
                        "data_path": payload.get("data_path"), "filename": payload.get("filename", ""),
                        "allow_version_mismatch": payload.get("allow_version_mismatch") is True}
                parents = {}
            elif stage == "decomposition":
                work = {"stage": stage, "data": self.service.data, "settings": settings}
                parents = {}
            elif stage in ("profiles", "localization"):
                source = self.require("decomposition", payload.get("decomposition_id"))
                work = {"stage": stage, "data": self.service.data,
                        "decomposition": {"settings": source["settings"]}, "settings": settings}
                parents = {"decomposition_id": source["result_id"]}
            else:
                source = self.require("profiles", payload.get("profiles_id"))
                work = {"stage": stage, "profiles": source, "settings": settings}
                parents = {"profiles_id": source["result_id"]}
            job_id = uuid.uuid4().hex
            job = {"job_id": job_id, "stage": stage, "dataset_id": self.dataset_id,
                   "status": "queued", "progress": 0., "message": "排队中", "error": None,
                   "cancel": threading.Event(), "timeout_seconds": float(timeout)}
            self.jobs[job_id] = job
            # Retain a small job history; stage results are held separately.
            for old in list(self.jobs)[:-32]:
                if self.jobs[old]["status"] not in {"queued", "running"}:
                    del self.jobs[old]
            self.executor.submit(self._run, job, work, parents)
            return self.status(job_id)

    def status(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError("任务不存在。")
            return {key: value for key, value in self.jobs[job_id].items() if key != "cancel"}

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise ValueError("任务不存在。")
            if job["status"] in {"queued", "running"}:
                job["cancel"].set()
                job["message"] = "正在终止计算进程…"
            return self.status(job_id)

    def _run(self, job, work, parents):
        process = None
        reader = None
        started = time.monotonic()
        try:
            with self.lock:
                job["status"] = "running"
            with ExitStack() as stack:
                folder = stack.enter_context(tempfile.TemporaryDirectory(prefix=".tmp_workflow_job_", dir=runtime_work_dir()))
                path = Path(folder)
                # Only this task creates/reads the pickle. No user file is unpickled.
                with (path / "input.pkl").open("wb") as stream:
                    pickle.dump(work, stream, protocol=pickle.HIGHEST_PROTOCOL)
                environment = os.environ.copy()
                environment.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
                if job["cancel"].is_set():
                    raise InterruptedError("任务已取消；已完成的结果保持不变。")
                process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "eelsfit.workflow_jobs", str(path)],
                    cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                stack.callback(_stop_process, process)
                lines = queue.Queue()

                def read_output():
                    for line in process.stdout:
                        lines.put(line)

                reader = threading.Thread(target=read_output, daemon=True)
                reader.start()
                logs = []
                while process.poll() is None:
                    if job["cancel"].is_set():
                        raise InterruptedError("任务已取消；计算进程已终止，已完成的结果保持不变。")
                    if time.monotonic() - started > job["timeout_seconds"]:
                        raise TimeoutError("计算超时，进程已终止；请调整组分数、迭代上限或时间限制后重试。")
                    try:
                        line = lines.get(timeout=.1)
                    except queue.Empty:
                        continue
                    try:
                        update = json.loads(line)
                        if "progress" in update:
                            with self.lock:
                                job.update(progress=update["progress"], message=update["message"])
                    except (ValueError, TypeError):
                        logs.append(line[-1000:])
                process.wait()
                reader.join(timeout=2)
                if job["cancel"].is_set():
                    raise InterruptedError("任务已取消；已完成的结果保持不变。")
                if time.monotonic() - started > job["timeout_seconds"]:
                    raise TimeoutError("计算超时，结果未应用；请调整时间限制后重试。")
                if process.returncode or not (path / "result.pkl").is_file():
                    raise RuntimeError("计算进程异常退出。" + "".join(logs)[-1500:])
                with (path / "result.pkl").open("rb") as stream:
                    reply = pickle.load(stream)
                if "error" in reply:
                    raise ValueError(reply["error"])
                result = reply["result"]
                with self.service.request_lock, self.service._lock, self.lock:
                    if self.service.dataset_id != job["dataset_id"]:
                        raise ValueError("数据已切换，丢弃旧数据的迟到结果。")
                    if job["cancel"].is_set():
                        raise InterruptedError("任务已取消；已完成的结果保持不变。")
                    if time.monotonic() - started > job["timeout_seconds"]:
                        raise TimeoutError("计算超时，结果未应用。")
                    if job["stage"] == "import":
                        if not result["same_data"]:
                            self.service.data = result["data"]
                            self.service.dataset_id = uuid.uuid4().hex
                            self.service.results.clear()
                            self.service.pca_results.clear()
                            self.service._pca_models.clear()
                            self.service._reference_maps = None
                        self.dataset_id = self.service.dataset_id
                        self.results, self.fits = result["results"], result["fits"]
                        for value in (*self.results.values(), *self.fits.values()):
                            value["dataset_id"] = self.dataset_id
                        job.update(status="done", progress=1., message=result["message"],
                                   imported_dataset_id=self.dataset_id)
                        return
                    elif job["stage"] in ("fit", "fit_regions"):
                        completed = result["fits"] if job["stage"] == "fit_regions" else {fit_key(result): result}
                        for fitted in completed.values():
                            fitted.update(result_id=uuid.uuid4().hex, dataset_id=job["dataset_id"], **parents)
                        self.fits.update(completed)
                        result = list(completed.values())[-1]
                        self.results["fit"] = result
                        job["fit_result_ids"] = {name: fit["result_id"] for name, fit in completed.items()}
                    else:
                        result.update(result_id=uuid.uuid4().hex, dataset_id=job["dataset_id"], **parents)
                        self.results[job["stage"]] = result
                        if job["stage"] != "localization":
                            for later in STAGES[STAGES.index(job["stage"])+1:]:
                                self.results.pop(later, None)
                            self.fits.clear()
                        if job["stage"] == "decomposition":
                            self.results.pop("localization", None)
                    job.update(status="done", progress=1., message="完成", result_id=result["result_id"])
        except Exception as error:
            with self.lock:
                job.update(status="cancelled" if isinstance(error, InterruptedError) else "error", error=str(error), message=str(error))
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                if reader is not None:
                    reader.join(timeout=2)
                if process.stdout:
                    process.stdout.close()

    def shutdown(self):
        with self.lock:
            self.closed = True
            for job in self.jobs.values():
                job["cancel"].set()
        self.executor.shutdown(wait=True)


def _worker(folder):
    path = Path(folder).resolve()
    # Existing old sessions may still start workers in the previous location.
    # New jobs always use web_runs; both checks remain exact and project-local.
    if path.parent not in {ROOT, ROOT / "web_runs"} or not path.name.startswith(".tmp_workflow_job_"):
        raise ValueError("Worker requires its task-owned temporary folder.")

    def report(value, message):
        print(json.dumps({"progress": value, "message": message}, ensure_ascii=False), flush=True)

    try:
        with (path / "input.pkl").open("rb") as stream:
            work = pickle.load(stream)
        stage = work.pop("stage")
        function = {"decomposition": decompose, "profiles": build_profiles, "fit": fit_profile,
                    "fit_regions": fit_regions, "localization": localize_interface, "import": restore_workflow}[stage]
        reply = {"result": function(**work, progress=report)}
    except Exception as error:
        reply = {"error": str(error)}
    with (path / "result.pkl").open("wb") as stream:
        pickle.dump(reply, stream, protocol=pickle.HIGHEST_PROTOCOL)


def _stop_process(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


if __name__ == "__main__":
    _worker(sys.argv[1])
