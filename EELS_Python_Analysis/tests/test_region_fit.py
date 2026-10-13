from pathlib import Path
import subprocess
import tempfile
from eelsfit.runtime_paths import test_runs_dir
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from scipy.special import ndtr, ndtri, expit, logit

from eelsfit.region_fit import DEFAULT_THRESHOLDS, fit_profile, run_bounded_fit, validate_thresholds
from eelsfit.io import write_eels_hdf5
from eels_web import create_app
from tests.test_reference_maps import synthetic_reference_data


class ContrastFitTests(unittest.TestCase):
    def test_analytic_erf_and_logistic_with_both_contrast_directions(self):
        x = np.linspace(-8,8,161)
        for model, cdf, inverse in [('erf',ndtr,ndtri),('logistic',expit,logit)]:
            for amplitude in [1200,-1200]:
                with self.subTest(model=model,amplitude=amplitude):
                    profile = 2000+amplitude*cdf((x-.7)/.8)
                    result = fit_profile(x,profile,[0,1,2],DEFAULT_THRESHOLDS)['models'][model]
                    self.assertEqual(result['status'],'available',result)
                    self.assertAlmostEqual(result['center_nm'],.7,places=7)
                    self.assertAlmostEqual(result['scale_nm'],.8,places=7)
                    self.assertAlmostEqual(result['width_10_90_nm'],.8*(inverse(.9)-inverse(.1)),places=7)
                    self.assertAlmostEqual(result['rois']['Si']['x_max'],.7+.8*inverse(.01),places=7)
                    self.assertAlmostEqual(result['rois']['Ge']['x_min'],.7+.8*inverse(.99),places=7)
                    for roi in result['rois'].values():
                        self.assertEqual((roi['y_min'],roi['y_max']),(0,2))
                    self.assertLess(result['rois']['Si']['x_max'],result['rois']['Interface']['x_min'])

    def test_thresholds_outside_view_do_not_invent_bulk_regions(self):
        x = np.linspace(-3,3,121)
        profile = 100+200*ndtr(x/1.5)
        fit = fit_profile(x,profile,[0,1],DEFAULT_THRESHOLDS)['models']['erf']
        self.assertEqual(fit['status'],'available')
        self.assertIsNone(fit['rois'])
        self.assertIn('范围外',fit['apply_error'])
        changed = {**DEFAULT_THRESHOLDS,'si_bulk_percent':95,'ge_bulk_percent':95}
        fitted = fit_profile(x,profile,[0,1],changed)['models']['erf']
        self.assertIsNotNone(fitted['rois'])
        self.assertAlmostEqual(fitted['center_nm'],fit['center_nm'],places=8)

    def test_missing_columns_flat_profile_and_invalid_percentages(self):
        x = np.linspace(0,16,65)
        profile = 100+200*ndtr((x-8)/.8)
        profile[20:24] = np.nan
        fit = fit_profile(x,profile,[0,1],DEFAULT_THRESHOLDS)
        self.assertEqual(fit['valid_columns'],61)
        self.assertAlmostEqual(fit['models']['erf']['center_nm'],8,places=6)
        for bad in [np.ones(65),np.full(65,np.nan)]:
            with self.assertRaises(ValueError): fit_profile(x,bad,[0,1],DEFAULT_THRESHOLDS)
        for change in [{'si_bulk_percent':100},{'ge_bulk_percent':float('nan')},
                       {'si_bulk_percent':80},{'interface_low_percent':60}, {'interface_high_percent':5}]:
            with self.assertRaises(ValueError): validate_thresholds({**DEFAULT_THRESHOLDS,**change})

    def test_timeout_kills_worker_and_next_fit_succeeds(self):
        x = np.linspace(0,16,65)
        payload = {'x_nm':x.tolist(),'profile':(100+200*ndtr((x-8)/.8)).tolist(),
                   'y_nm':[0,1],'thresholds':DEFAULT_THRESHOLDS}
        processes = []
        original = subprocess.Popen
        def start(*args,**kwargs):
            process = original(*args,**kwargs)
            processes.append(process)
            return process
        with patch('subprocess.Popen',side_effect=start):
            with self.assertRaises(TimeoutError): run_bounded_fit(payload,timeout_seconds=.001)
        self.assertEqual(len(processes),1)
        self.assertIsNotNone(processes[0].poll(), 'Timed-out optimizer must be reaped')
        self.assertEqual(run_bounded_fit(payload)['models']['erf']['status'],'available')


class RegionFitApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='.tmp_region_fit_api_',dir=test_runs_dir())
        self.path = Path(self.folder.name)/'sample.h5'
        write_eels_hdf5(synthetic_reference_data(),self.path)
        self.app = create_app(self.path)
        self.client = self.app.test_client()
        self.service = self.app.extensions['eels_service']

    def tearDown(self):
        self.service.shutdown()
        self.folder.cleanup()

    def test_fit_preserves_analysis_and_uses_requested_reference(self):
        fitted = self.client.post('/api/pca',json={'region':'Si','roi':{'x_min':0,'x_max':3,'y_min':0,'y_max':5},
            'fit_min':15,'fit_max':75,'config':{'mode':'pca','component_count':2,'peak_count':1,
                'min_peak_distance_meV':5,'min_prominence_percent':.5}})
        self.assertEqual(fitted.status_code,200,fitted.json)
        before = self.client.get('/api/pca/results').json
        response = self.client.post('/api/region-fit',json={'source':'total_counts','thresholds':DEFAULT_THRESHOLDS})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.client.get('/api/pca/results').json,before)
        self.assertEqual(response.json['dataset_id'],self.service.dataset_id)
        self.assertAlmostEqual(response.json['models']['erf']['center_nm'],8,places=5)
        self.client.get('/api/reference-maps')
        response = self.client.post('/api/region-fit',json={'source':'spectral_contribution'})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.client.get('/api/pca/results').json,before)
        self.assertEqual(response.json['source'],'spectral_contribution')

    def test_errors_timeout_stale_dataset_and_lock_recovery(self):
        with patch('eels_web.run_bounded_fit',side_effect=TimeoutError('timeout')):
            self.assertEqual(self.client.post('/api/region-fit',json={}).status_code,504)
        self.assertEqual(self.client.get('/api/info').status_code,200)
        with patch('eels_web.run_bounded_fit',side_effect=RuntimeError('worker failed')):
            with self.assertLogs('eels_web',level='ERROR'):
                self.assertEqual(self.client.post('/api/region-fit',json={}).status_code,500)
        self.assertEqual(self.client.get('/api/info').status_code,200)
        self.assertEqual(self.client.post('/api/region-fit',json={},headers={'X-EELS-Dataset':'old'}).status_code,409)
        for payload in [{'source':'unknown'},{'thresholds':{'si_bulk_percent':100}}]:
            self.assertEqual(self.client.post('/api/region-fit',json=payload).status_code,400)
        self.service.data.total_counts = None
        self.assertEqual(self.client.post('/api/region-fit',json={}).status_code,400)

    def test_waiting_fit_keeps_api_usable_and_reply_identifies_original_dataset(self):
        entered, release = threading.Event(), threading.Event()
        responses = []
        def delayed(*args,**kwargs):
            entered.set()
            release.wait(5)
            return {'models':{}}
        def request_fit():
            with self.app.test_client() as client:
                responses.append(client.post('/api/region-fit',json={}))
        original_id = self.service.dataset_id
        with patch('eels_web.run_bounded_fit',side_effect=delayed):
            thread = threading.Thread(target=request_fit)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.client.get('/api/info').status_code,200)
                self.assertEqual(self.client.post('/api/region-fit',json={}).status_code,409)
                started = self.client.post('/api/import',json={'path':str(self.path)})
                self.assertEqual(started.status_code,200,started.json)
                for _ in range(200):
                    status = self.client.get('/api/jobs/'+started.json['job_id']).json
                    if status['status'] in {'done','error'}: break
                    time.sleep(.01)
                self.assertEqual(status['status'],'done',status)
                self.assertNotEqual(self.service.dataset_id,original_id)
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(responses[0].status_code,200)
        self.assertEqual(responses[0].json['dataset_id'],original_id)
