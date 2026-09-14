"""Bounded CPU resampling with deterministic, worker-independent random streams."""

from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from contextlib import ExitStack
from dataclasses import dataclass
from multiprocessing.shared_memory import SharedMemory

import numpy as np
from threadpoolctl import threadpool_limits

LEGACY_SCHEME = "legacy_sequential_v1"
INDEPENDENT_SCHEME = "seedsequence_per_resample_v1"
BOOTSTRAP_ENGINE = "cpu_parallel_v1"
DEFAULT_RESAMPLES = 500
MAX_WORKERS = min(16, os.cpu_count() or 1)


def resolve_workers(requested, samples: int, data_bytes: int = 0, backend: str = "process") -> int:
    try:
        value = float(requested)
    except (TypeError, ValueError) as error:
        raise ValueError("Parallel workers must be an integer.") from error
    if not np.isfinite(value) or value != int(value) or not 0 <= value <= MAX_WORKERS:
        raise ValueError(f"Parallel workers must be between 0 (auto) and {MAX_WORKERS}.")
    if value:
        return min(int(value), samples)
    # Leave CPU/memory headroom; short jobs do not amortize Windows worker startup.
    memory_limit = max(1, (512 * 1024**2) // max(1, 8 * data_bytes))
    if backend == "thread":
        return min(8, max(1, (os.cpu_count() or 1) - 2), max(1, samples // 50), memory_limit)
    return min(8, max(1, (os.cpu_count() or 1) - 2), max(1, samples // 250), memory_limit)


@dataclass(frozen=True)
class _SharedArray:
    name: str
    shape: tuple
    dtype: str


_worker_function = None
_worker_context = None
_worker_memory = []
_worker_limits = None


def _initialize_worker(function, context):
    global _worker_function, _worker_context, _worker_limits
    _worker_limits = threadpool_limits(limits=1, user_api="blas")
    _worker_function = function
    _worker_context = dict(context)
    for key, value in context.items():
        if isinstance(value, _SharedArray):
            memory = SharedMemory(name=value.name)
            _worker_memory.append(memory)
            array = np.ndarray(value.shape, dtype=value.dtype, buffer=memory.buf)
            array.flags.writeable = False
            _worker_context[key] = array


def _run_sample(seed):
    return _worker_function(_worker_context, np.random.default_rng(seed))


def bootstrap_samples(function, context, count, seed, workers, rng_scheme, progress=None, backend="process"):
    """Return samples in index order; report completed work, not submission order."""
    if rng_scheme not in {LEGACY_SCHEME, INDEPENDENT_SCHEME}:
        raise ValueError(f"Unknown bootstrap RNG scheme: {rng_scheme}")
    if backend not in {"process", "thread"}:
        raise ValueError(f"Unknown CPU backend: {backend}")
    if not isinstance(workers, int) or not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"Workers must be between 1 and {MAX_WORKERS}.")
    if rng_scheme == LEGACY_SCHEME and workers != 1:
        raise ValueError("Legacy sequential sampling requires one worker.")
    results = [None] * count
    report_every = max(1, count // 100)

    def report(done):
        if progress and (done % report_every == 0 or done == count):
            progress(done, count)

    if rng_scheme == LEGACY_SCHEME:
        rng = np.random.default_rng(seed)
        for index in range(count):
            results[index] = function(context, rng)
            report(index + 1)
        return results

    seeds = np.random.SeedSequence(seed).spawn(count)
    if workers == 1:
        with threadpool_limits(limits=1, user_api="blas"):
            for index, child in enumerate(seeds):
                results[index] = function(context, np.random.default_rng(child))
                report(index + 1)
        return results

    with ExitStack() as stack:
        if backend == "thread":
            stack.enter_context(threadpool_limits(limits=1, user_api="blas"))
            pool = stack.enter_context(ThreadPoolExecutor(max_workers=workers, thread_name_prefix="eels-pca"))
            def submit(index):
                return pool.submit(function, context, np.random.default_rng(seeds[index]))
        else:
            shared_context = dict(context)
            for key, array in context.items():
                if isinstance(array, np.ndarray) and array.nbytes >= 1024 * 1024:
                    memory = SharedMemory(create=True, size=array.nbytes)
                    stack.callback(memory.unlink)
                    stack.callback(memory.close)
                    np.ndarray(array.shape, dtype=array.dtype, buffer=memory.buf)[:] = array
                    shared_context[key] = _SharedArray(memory.name, array.shape, array.dtype.str)
            pool = stack.enter_context(ProcessPoolExecutor(
                max_workers=workers, mp_context=mp.get_context("spawn"),
                initializer=_initialize_worker, initargs=(function, shared_context),
            ))
            def submit(index):
                return pool.submit(_run_sample, seeds[index])
        next_index = 0
        completed = 0
        pending = {}
        while next_index < count or pending:
            while next_index < count and len(pending) < 2 * workers:
                future = submit(next_index)
                pending[future] = next_index
                next_index += 1
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                results[pending.pop(future)] = future.result()
                completed += 1
                report(completed)
    return results
