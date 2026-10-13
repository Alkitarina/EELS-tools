import copy
import json
from pathlib import Path
import tempfile
from eelsfit.runtime_paths import test_runs_dir
import time
import unittest

import numpy as np
from scipy.optimize import nnls
from scipy.integrate import trapezoid
from threadpoolctl import threadpool_limits

from eels_web import create_app
from eelsfit.io import EELSData, write_eels_hdf5
from eelsfit.multivariate import decompose
from eelsfit.interface_workflow import build_profiles, fit_profile, align_rows
from eelsfit.workflow_peaks import two_reference_nnls, cross_validate
from eelsfit.workflow_export import export_document, software_identity
from eelsfit.exporting import json_text
from reproduce_workflow import replay_workflow

ROOT = Path(__file__).resolve().parents[1]


def synthetic_data(path=Path("synthetic.h5"), noise=.003):
    x, y, energy = np.linspace(0, 15, 31), np.arange(8.), np.linspace(10, 80, 141)
    si = 10 * np.exp(-.5 * ((energy-28)/3)**2)
    ge = 8 * np.exp(-.5 * ((energy-65)/4)**2)
    interface = np.exp(-.5*((energy-46)/2)**2)
    center = 7.5 + .1*(y-3.5)
    fraction = 1/(1+np.exp(-(x[None, :]-center[:, None])/.5))
    local = 1.4*np.exp(-((x[None, :]-center[:, None])/1.1)**2) * (1+.7*np.exp(-((y[:, None]-3.5)/1.1)**2))
    cube = (1-fraction[:, :, None])*si + fraction[:, :, None]*ge + local[:, :, None]*interface
    cube += np.random.default_rng(18).normal(0, noise, cube.shape)
    counts = 100+300*fraction
    return EELSData(cube, x, y, energy, Path(path), {}, counts)


def decomp_settings(method="mcr_als"):
    return dict(method=method,components=3,fit_min=10.,fit_max=80.,init="nndsvda",starts=1,seed=37,
                max_iter=60,tolerance=1e-5,negative_policy="reject",preprocessing_confirmed=True)


def profile_settings():
    return {"regions_confirmed": True, "core_enabled": True, "stripe_rows": 2, "bulk_scope": "per_strip",
            "rois": {"Si": dict(x_min=0.,x_max=4.,y_min=0.,y_max=7.),
                     "Ge": dict(x_min=11.,x_max=15.,y_min=0.,y_max=7.),
                     "Interface": dict(x_min=6.,x_max=9.,y_min=0.,y_max=7.),
                     "Core": dict(x_min=7.,x_max=8.,y_min=3.,y_max=4.)},
            "alignment": {"enabled": False}, "template": {"kind": "mean"}}


class WorkflowAlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.limits = threadpool_limits(limits=1)
        self.data = synthetic_data()

    def tearDown(self):
        self.limits.restore_original_limits()

    def test_full_pixel_mcr_signed_input_and_reproducible_seed(self):
        original = self.data.intensity.copy()
        result = decompose(self.data, decomp_settings())
        repeated = decompose(self.data, decomp_settings())
        self.assertEqual(result["summary"]["valid_pixels"], 8*31)
        self.assertEqual(result["weights"].shape, (8,31,3))
        self.assertTrue((result["spectra"] >= 0).all())
        self.assertTrue((result["weights"] >= 0).all())
        np.testing.assert_array_equal(result["spectra"], repeated["spectra"])
        np.testing.assert_array_equal(self.data.intensity, original)
        self.assertLess(result["summary"]["rmse"], .025)
        np.testing.assert_allclose(trapezoid(result["spectra"], self.data.energy_meV, axis=1), 1.)

    def test_nmf_negative_policy_and_nonfinite_pixel_accounting(self):
        with self.assertRaisesRegex(ValueError, "NMF 输入"):
            decompose(self.data, decomp_settings("nmf"))
        settings = {**decomp_settings("nmf"), "negative_policy": "clip_for_decomposition"}
        self.data.intensity[0,0,0] = np.nan
        original = self.data.intensity.copy()
        result = decompose(self.data, settings)
        self.assertEqual(result["summary"]["excluded_pixels"], 1)
        self.assertTrue(np.isnan(result["weights"][0,0]).all())
        self.assertTrue(result["summary"]["warnings"])
        np.testing.assert_array_equal(self.data.intensity, original)
        with self.assertRaisesRegex(ValueError, "确认"):
            decompose(self.data, {**settings,"preprocessing_confirmed":False})
        with self.assertRaisesRegex(ValueError, "能量窗"):
            decompose(self.data, {**settings,"fit_min":-1})

    def test_two_column_nnls_matches_scipy_and_preserves_negative_residual(self):
        rng = np.random.default_rng(43)
        basis = rng.normal(.6,.5,(2,81))
        values = rng.normal(0,2,(40,81))
        coefficients, fitted, residual, _ = two_reference_nnls(values,basis)
        expected = np.array([nnls(basis.T,row)[0] for row in values])
        np.testing.assert_allclose(coefficients,expected,rtol=1e-10,atol=1e-12)
        np.testing.assert_allclose(fitted+residual,values,atol=1e-14)
        self.assertTrue(np.any(residual<0))
        with self.assertRaisesRegex(ValueError,"共线"):
            two_reference_nnls(values,np.array([basis[0],basis[0]*2]))

    def test_stripes_retain_xe_exclude_core_and_align_without_extrapolation(self):
        cfg = profile_settings()
        profiles = build_profiles(self.data,{"settings":decomp_settings()},cfg)
        self.assertEqual(len(profiles["strips"]),4)
        np.testing.assert_allclose(profiles["strips"][1]["measured"],self.data.intensity[2:4].mean(axis=0))
        self.assertEqual(profiles["summary"]["region_pixels"]["Interface"],8*7-2*3)
        self.assertGreater(profiles["regions"]["Core"]["diagnostics"]["positive_area"],
                           profiles["regions"]["Interface"]["diagnostics"]["positive_area"])
        cfg["alignment"]={"enabled":True,"anchors":[[0,7.15],[7,7.85]]}
        aligned = build_profiles(self.data,{"settings":decomp_settings()},cfg)
        self.assertEqual(aligned["alignment"]["axis"],"distance_nm")
        self.assertLess(aligned["x_axis_nm"].size,self.data.x_nm.size)
        # Raw body templates do not change just because the xE display was registered.
        np.testing.assert_array_equal(profiles["templates"],aligned["templates"])
        with self.assertRaisesRegex(ValueError,"覆盖"):
            align_rows(self.data,self.data.intensity,{"enabled":True,"anchors":[[1,7.5],[6,7.5]]})

    def test_missing_bulk_strips_are_reported_without_silent_fallback(self):
        cfg=profile_settings();cfg["rois"]["Si"]["y_min"]=2
        result=build_profiles(self.data,{"settings":decomp_settings()},cfg)
        self.assertEqual(result["strips"][0]["status"],"unavailable")
        self.assertEqual(result["summary"]["available_strips"],3)
        cfg["bulk_scope"]="global"
        self.assertEqual(build_profiles(self.data,{"settings":decomp_settings()},cfg)["summary"]["available_strips"],4)

    def test_candidate_residual_and_joint_fits_recover_interface_peak(self):
        profiles=build_profiles(self.data,{"settings":decomp_settings()},profile_settings())
        cfg={"selection":{"kind":"region","region":"Core"},"shape":"gaussian","peak_count":1,
             "prominence":.05,"distance":4.,"center_radius":3.,"fwhm_min":1.,"fwhm_max":9.}
        result=fit_profile(profiles,cfg)
        self.assertAlmostEqual(result["two_stage"]["centers_meV"][0],46.,delta=.1)
        self.assertAlmostEqual(result["joint"]["centers_meV"][0],46.,delta=.1)
        self.assertLessEqual(result["joint"]["sse"],result["two_stage"]["sse"]+1e-8)
        self.assertIsNone(result["joint"]["uncertainty"])
        cfg["template"]={"kind":"parametric"}
        settings=profile_settings();settings["template"]={"kind":"parametric","shape":"gaussian","peak_count":1,"fwhm_min":1,"fwhm_max":15}
        fitted_profiles=build_profiles(self.data,{"settings":decomp_settings()},settings)
        self.assertEqual(fitted_profiles["template_metadata"]["Si"]["kind"],"parametric")

    def test_zero_residual_does_not_produce_peak_model(self):
        e=self.data.energy_meV
        basis=np.array([np.exp(-((e-28)/3)**2),np.exp(-((e-65)/4)**2)])
        result=cross_validate(e,.7*basis[0]+.4*basis[1],basis,{"peak_count":2})
        self.assertEqual(result["candidates"],[])
        self.assertIsNone(result["joint"])

    def test_compact_export_replay_and_guards(self):
        with tempfile.TemporaryDirectory(prefix=".tmp_workflow_replay_",dir=test_runs_dir()) as folder:
            path=Path(folder)/"数据.h5";write_eels_hdf5(self.data,path)
            self.data.source=path
            d=decompose(self.data,decomp_settings());d["result_id"]="d"
            p=build_profiles(self.data,d,profile_settings());p.update(result_id="p",decomposition_id="d")
            f=fit_profile(p,{"selection":{"kind":"region","region":"Core"},"peak_count":1});f.update(result_id="f",profiles_id="p")
            doc=export_document(self.data,{"decomposition":d,"profiles":p,"fit":f},software_identity())
            result_path=Path(folder)/"结果.json";result_path.write_text(json_text(doc),encoding="utf-8")
            replayed=replay_workflow(result_path)
            np.testing.assert_allclose(replayed["results"]["fit"]["joint"]["centers_meV"],doc["results"]["fit"]["joint"]["centers_meV"],atol=1e-8)
            self.assertNotIn('"measured"',json_text(doc));self.assertNotIn('"spectra"',json_text(doc))
            changed_path=Path(folder)/"changed.h5"
            changed=synthetic_data(changed_path);changed.intensity[0,0,10]+=1;write_eels_hdf5(changed,changed_path)
            with self.assertRaisesRegex(ValueError,"校验"):
                replay_workflow(result_path,changed_path,allow_version_mismatch=True)


class WorkflowApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix=".tmp_workflow_api_",dir=test_runs_dir())
        self.path=Path(self.tmp.name)/"fixture.h5";write_eels_hdf5(synthetic_data(self.path),self.path)
        self.app=create_app(self.path);self.client=self.app.test_client();self.manager=self.app.extensions["eels_workflow"]

    def tearDown(self):
        self.app.extensions["eels_service"].shutdown();self.tmp.cleanup()

    def wait(self, job):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            result=self.manager.status(job)
            if result["status"] not in {"queued","running"}:return result
            time.sleep(.05)
        self.fail("Job did not settle before test deadline")

    def test_real_subprocess_pipeline_invalidation_and_exports(self):
        ids={}
        for stage,settings in (("decomposition",decomp_settings()),("profiles",profile_settings()),
                               ("fit",{"selection":{"kind":"region","region":"Core"},"peak_count":1})):
            response=self.client.post("/api/workflow/run",json={"stage":stage,"settings":settings,
                "decomposition_id":ids.get("decomposition"),"profiles_id":ids.get("profiles")})
            self.assertEqual(response.status_code,202,response.json)
            done=self.wait(response.json["job_id"]);self.assertEqual(done["status"],"done",done)
            ids[stage]=done["result_id"]
            self.assertEqual(self.client.get("/api/workflow/"+stage,query_string={"result_id":ids[stage]}).status_code,200)
        doc=self.client.post("/api/workflow/export",json={"result_ids":ids})
        self.assertEqual(doc.status_code,200,doc.text)
        self.assertEqual(doc.json["format"],"eels_interface_workflow_v1")
        self.assertIn("fixture.json",doc.headers["Content-Disposition"])
        arrays=self.client.get("/api/workflow/arrays",query_string={"decomposition_id":ids["decomposition"],"profiles_id":ids["profiles"]})
        self.assertEqual(arrays.status_code,200)
        response=self.client.post("/api/workflow/run",json={"stage":"decomposition","settings":decomp_settings()})
        self.assertEqual(self.wait(response.json["job_id"])["status"],"done")
        self.assertEqual(self.client.get("/api/workflow/profiles",query_string={"result_id":ids["profiles"]}).status_code,400)

    def test_cancel_timeout_and_stale_dataset_leave_service_responsive(self):
        job=self.manager.start({"stage":"decomposition","settings":decomp_settings()},timeout=.001)
        done=self.wait(job["job_id"]);self.assertEqual(done["status"],"error");self.assertIn("超时",done["error"])
        job=self.manager.start({"stage":"decomposition","settings":decomp_settings()})
        self.manager.cancel(job["job_id"]);done=self.wait(job["job_id"])
        self.assertEqual(done["status"],"cancelled")
        self.assertEqual(self.client.get("/health").status_code,200)
        stale=self.client.get("/api/workflow/state",headers={"X-EELS-Dataset":"stale"})
        self.assertEqual(stale.status_code,409)
        self.assertFalse(self.manager.busy)


if __name__=="__main__":unittest.main()
