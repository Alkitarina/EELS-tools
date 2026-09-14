import unittest

import numpy as np
from threadpoolctl import threadpool_info

from eelsfit.bootstrap_runtime import (
    INDEPENDENT_SCHEME, LEGACY_SCHEME, MAX_WORKERS, bootstrap_samples, resolve_workers,
)


def random_sample(context, rng):
    return rng.standard_normal(8) + context["array"][0, 0]


def failing_sample(context, rng):
    raise RuntimeError("deliberate worker failure")


class ParallelTests(unittest.TestCase):
    @unittest.skipIf(MAX_WORKERS < 2, "Two workers required")
    def test_ordered_streams_match_serial_threads_and_processes(self):
        # Large enough to exercise shared memory, not just pickle small inputs.
        context = {"array": np.ones((512, 512))}
        expected = bootstrap_samples(random_sample, context, 12, 21, 1, INDEPENDENT_SCHEME)
        for backend in ("thread", "process"):
            progress = []
            actual = bootstrap_samples(random_sample, context, 12, 21, 2, INDEPENDENT_SCHEME,
                                       lambda done, total: progress.append((done, total)), backend=backend)
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(progress, [(i, 12) for i in range(1, 13)])
        np.testing.assert_array_equal(context["array"], 1)

    def test_legacy_stream_and_blas_restore(self):
        context = {"array": np.zeros((1, 1))}
        rng = np.random.default_rng(21)
        expected = [random_sample(context, rng) for _ in range(4)]
        actual = bootstrap_samples(random_sample, context, 4, 21, 1, LEGACY_SCHEME)
        np.testing.assert_array_equal(actual, expected)
        before = threadpool_info()
        bootstrap_samples(random_sample, context, 4, 21, 1, INDEPENDENT_SCHEME)
        self.assertEqual(threadpool_info(), before)

    @unittest.skipIf(MAX_WORKERS < 2, "Two workers required")
    def test_worker_failure_propagates_and_next_run_works(self):
        context = {"array": np.ones((512, 512))}
        with self.assertRaisesRegex(RuntimeError, "deliberate worker failure"):
            bootstrap_samples(failing_sample, context, 4, 21, 2, INDEPENDENT_SCHEME)
        self.assertEqual(len(bootstrap_samples(random_sample, context, 4, 21, 2, INDEPENDENT_SCHEME)), 4)

    def test_validation_and_short_job_policy(self):
        self.assertEqual(resolve_workers(0, 20), 1)
        self.assertEqual(resolve_workers(0, 200), 1)
        self.assertLessEqual(resolve_workers(0, 200, backend="thread"), 4)
        for bad in (-1, 1.5, "invalid", MAX_WORKERS + 1):
            with self.assertRaises(ValueError):
                resolve_workers(bad, 200)
        with self.assertRaises(ValueError):
            bootstrap_samples(random_sample, {}, 4, 21, 1, "unknown")


if __name__ == "__main__":
    unittest.main()
