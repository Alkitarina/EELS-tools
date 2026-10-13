"""Signed candidate selection must not borrow height from opposite-sign shoulders."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from threadpoolctl import threadpool_limits

from eelsfit.exporting import json_text
from eelsfit.interface_workflow import build_profiles, fit_profile
from eelsfit.io import write_eels_hdf5
from eelsfit.multivariate import decompose
from eelsfit.runtime_paths import test_runs_dir
from eelsfit.workflow_export import export_document, software_identity
from eelsfit.workflow_peaks import candidates, CANDIDATE_SCORE_METHOD
from reproduce_workflow import replay_workflow
from tests.test_multivariate_workflow import synthetic_data, decomp_settings, profile_settings


class CandidateScoreTests(unittest.TestCase):
    def setUp(self):
        self.energy = np.arange(11.) * 5 + 12
        self.signal = np.array([0, -10, 0, 8, 0, -.2, 0, 4, 0, -3, 0])

    def detect(self, **kwargs):
        return candidates(self.energy, self.signal, count=4, prominence=.01, distance=2,
                          polarity="both", **kwargs)

    def test_shallow_trough_cannot_displace_deeper_negative_peak(self):
        before = self.detect()
        after = self.detect(candidate_score=CANDIDATE_SCORE_METHOD)
        self.assertEqual([p['center_meV'] for p in before], [17, 27, 37, 47])
        self.assertEqual([p['center_meV'] for p in after], [17, 27, 47, 57])
        self.assertEqual([p['sign'] for p in after], [-1, 1, 1, -1])
        self.assertTrue(all(p['selection_score'] <= abs(p['height']) for p in after))
        np.testing.assert_array_equal(self.signal, [0, -10, 0, 8, 0, -.2, 0, 4, 0, -3, 0])

    def test_same_score_filters_and_ranks_with_sign_symmetry(self):
        for multiplier in [1, -1]:
            result = candidates(self.energy, multiplier*self.signal, count=8, prominence=.1,
                                distance=2, polarity="both", candidate_score=CANDIDATE_SCORE_METHOD)
            self.assertEqual([p['center_meV'] for p in result], [17, 27, 47, 57])
            self.assertEqual([p['sign'] for p in result], [multiplier*s for s in [-1, 1, 1, -1]])
            self.assertTrue(all(p['selection_score'] >= 1.8 for p in result))
        negative = candidates(self.energy, self.signal, 4, .1, 2, "negative", CANDIDATE_SCORE_METHOD)
        self.assertEqual([p['center_meV'] for p in negative], [17, 57])

    def test_legacy_replay_flat_signal_and_unknown_rule(self):
        self.assertEqual(self.detect(), self.detect(candidate_score=None))
        self.assertEqual(self.detect(), self.detect(candidate_score="prominence_v1"))
        self.assertTrue(all('selection_score' not in p for p in self.detect()))
        self.assertEqual(candidates(self.energy, np.zeros(11), candidate_score=CANDIDATE_SCORE_METHOD), [])
        with self.assertRaisesRegex(ValueError, "评分"):
            self.detect(candidate_score="typo")

    def test_completed_rule_and_candidate_scores_survive_export_replay(self):
        with threadpool_limits(limits=1), tempfile.TemporaryDirectory(prefix=".tmp_candidate_replay_", dir=test_runs_dir()) as directory:
            path = Path(directory)/"input.h5"
            data = synthetic_data(path); write_eels_hdf5(data, path)
            d = decompose(data, decomp_settings()); d['result_id'] = 'd'
            p = build_profiles(data, d, profile_settings()); p.update(result_id='p', decomposition_id='d')
            cfg = dict(selection=dict(kind='region', region='Core'), polarity='both', candidate_score=CANDIDATE_SCORE_METHOD)
            fit = fit_profile(p, cfg); fit.update(result_id='f', profiles_id='p')
            doc = export_document(data, dict(decomposition=d, profiles=p, fit=fit), software_identity())
            export = Path(directory)/'result.json'; export.write_text(json_text(doc), encoding='utf-8')
            replayed = replay_workflow(export)
            self.assertEqual(replayed['reproducibility']['steps'][-1]['settings']['candidate_score'], CANDIDATE_SCORE_METHOD)
            first = doc['results']['regions']['Core']['result']
            second = replayed['results']['regions']['Core']['result']
            self.assertTrue(first['candidates'])
            self.assertEqual(first['candidates'], second['candidates'])
            np.testing.assert_allclose(first['joint']['centers_meV'], second['joint']['centers_meV'], atol=1e-8)
            old = fit_profile(p, dict(selection=dict(kind='region', region='Core'), polarity='both'))
            self.assertNotIn('candidate_score', old['settings'])
            self.assertTrue(all('selection_score' not in c for c in old['candidates']))


if __name__ == '__main__':
    unittest.main()
