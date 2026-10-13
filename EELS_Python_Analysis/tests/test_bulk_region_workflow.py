"""Bulk fits and independent four-region result retention / replay."""
import copy
import json
from pathlib import Path
import tempfile
from eelsfit.runtime_paths import test_runs_dir
import time
import unittest
from unittest.mock import patch

import numpy as np
from threadpoolctl import threadpool_limits

from eels_web import create_app
from eelsfit.interface_workflow import build_profiles, fit_profile, fit_regions
from eelsfit.io import write_eels_hdf5
from eelsfit.multivariate import decompose
from eelsfit.workflow_export import export_document, software_identity
from eelsfit.exporting import json_text
from reproduce_workflow import replay_workflow
from tests.test_multivariate_workflow import synthetic_data, decomp_settings, profile_settings

ROOT = Path(__file__).resolve().parents[1]


def fit_config(name):
    bulk = name in ("Si", "Ge")
    return {"selection": {"kind": "region", "region": name}, "analysis_kind": "bulk_gaussian" if bulk else "residual",
            "shape": "gaussian", "polarity": "positive" if bulk else "both", "peak_count": 1 if bulk else 2,
            "centers": [28.] if name == "Si" else [65.] if name == "Ge" else None,
            "fwhm_min": 1., "fwhm_max": 14., "prominence": .05, "center_radius": 3.}


def signed_data(path):
    data = synthetic_data(path)
    data.intensity -= .8*np.exp(-((data.x_nm[None,:,None]-7.5)/1.1)**2)*np.exp(-.5*((data.energy_meV[None,None,:]-38)/1.1)**2)
    return data


class BulkRegionAlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.limits = threadpool_limits(limits=1)
        self.data = signed_data(Path("synthetic.h5"))
        self.profiles = build_profiles(self.data, {"settings": decomp_settings()}, profile_settings())

    def tearDown(self):
        self.limits.restore_original_limits()

    def test_bulk_fits_original_mean_and_never_subtracts_a_template(self):
        profiles = copy.deepcopy(self.profiles)
        profiles["templates"][:] = 0  # A subtraction would fail or change the answer.
        e = profiles["energy_meV"]
        mean = 6*np.exp(-4*np.log(2)*((e-26)/5)**2)+2*np.exp(-4*np.log(2)*((e-42)/7)**2)
        profiles["regions"]["Si"]["measured"] = mean
        with patch("eelsfit.interface_workflow.cross_validate", side_effect=AssertionError("Bulk subtraction called")):
            result = fit_profile(profiles, {**fit_config("Si"), "centers": [26,42], "peak_count": 2})
        np.testing.assert_allclose(result["bulk_fit"]["centers_meV"], [26,42], atol=1e-5)
        np.testing.assert_allclose(result["bulk_fit"]["fwhm_meV"], [5,7], atol=1e-5)
        np.testing.assert_allclose(result["bulk_fit"]["amplitudes"], [6,2], atol=1e-5)
        np.testing.assert_array_equal(result["measured"], mean)
        self.assertNotIn("residual", result)
        self.assertNotIn("weights", result)
        self.assertIsNone(result["bulk_fit"]["uncertainty"])

    def test_four_region_batch_retains_bulk_and_signed_residual_models(self):
        settings = {"regions": {name: fit_config(name) for name in ("Si","Ge","Interface","Core")}}
        results = fit_regions(self.profiles, settings)["fits"]
        for name, center in (("Si",28), ("Ge",65)):
            self.assertAlmostEqual(results[name]["bulk_fit"]["centers_meV"][0], center, delta=.02)
            self.assertEqual(results[name]["analysis_kind"], "bulk_gaussian")
        for name in ("Interface", "Core"):
            self.assertTrue((results[name]["two_stage"]["amplitudes"] < 0).any())
            self.assertTrue((results[name]["joint"]["bulk_weights"] >= 0).all())
        with self.assertRaisesRegex(ValueError, "每个"):
            fit_regions(self.profiles, {"regions": {"Si": fit_config("Si")}})

    def test_export_and_replay_all_four_actual_recipes(self):
        with tempfile.TemporaryDirectory(prefix=".tmp_bulk_regions_replay_", dir=test_runs_dir()) as folder:
            data = signed_data(Path(folder)/"四区.h5");write_eels_hdf5(data, data.source)
            d = decompose(data,decomp_settings());d["result_id"]="d"
            p = build_profiles(data,d,profile_settings());p.update(result_id="p",decomposition_id="d")
            fits = fit_regions(p, {"regions":{name:fit_config(name) for name in ("Si","Ge","Interface","Core")}})["fits"]
            for name, result in fits.items():result.update(result_id=name,profiles_id="p")
            doc = export_document(data,dict(decomposition=d,profiles=p,fits=fits,fit=fits["Si"]),software_identity())
            path = Path(folder)/"四区.json";path.write_text(json_text(doc),encoding="utf-8")
            replayed = replay_workflow(path)
            self.assertEqual(len(doc["reproducibility"]["steps"]),6)
            self.assertEqual(doc["results"]["fit"]["selection"]["region"],"Si")
            for name in fits:
                self.assertEqual(doc["results"]["regions"][name]["status"],"completed")
                key = "bulk_fit" if name in ("Si","Ge") else "two_stage"
                np.testing.assert_allclose(replayed["results"]["regions"][name]["result"][key]["centers_meV"],
                                           doc["results"]["regions"][name]["result"][key]["centers_meV"],atol=1e-8)
            self.assertNotIn('"measured"',json_text(doc))
            self.assertNotIn('"peak_components"',json_text(doc))
            tampered=copy.deepcopy(doc);tampered["reproducibility"]["steps"][-1]["target_key"]="Core"
            path.write_text(json_text(tampered),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"目标"):
                replay_workflow(path)

    def test_partial_export_explicitly_marks_uncomputed_and_disabled_regions(self):
        d = decompose(self.data,decomp_settings());d["result_id"]="d"
        cfg = profile_settings();cfg["core_enabled"]=False
        p = build_profiles(self.data,d,cfg);p.update(result_id="p",decomposition_id="d")
        fit=fit_profile(p,fit_config("Si"));fit.update(result_id="f",profiles_id="p")
        doc=export_document(self.data,dict(decomposition=d,profiles=p,fit=fit),software_identity())
        regions=doc["results"]["regions"]
        self.assertEqual(regions["Si"]["status"],"completed")
        self.assertEqual(regions["Ge"]["status"],"not_analyzed")
        self.assertIsNone(regions["Ge"]["result"])
        self.assertEqual(regions["Core"]["status"],"disabled")


class BulkRegionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix=".tmp_bulk_regions_api_",dir=test_runs_dir())
        path=Path(self.tmp.name)/"four.h5";write_eels_hdf5(signed_data(path),path)
        self.app=create_app(path);self.client=self.app.test_client();self.manager=self.app.extensions["eels_workflow"]

    def tearDown(self):
        self.app.extensions["eels_service"].shutdown();self.tmp.cleanup()

    def wait(self, job):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            result=self.manager.status(job["job_id"])
            if result["status"] not in ("queued","running"):return result
            time.sleep(.03)
        self.fail("Fixture job did not settle")

    def run_stage(self, stage, settings):
        result=self.client.post("/api/workflow/run",json={"stage":stage,"settings":settings,
            "decomposition_id":self.manager.results.get("decomposition",{}).get("result_id"),
            "profiles_id":self.manager.results.get("profiles",{}).get("result_id")})
        self.assertEqual(result.status_code,202,result.text)
        return self.wait(result.json)

    def test_independent_retention_batch_failure_and_invalidation(self):
        for stage,settings in (("decomposition",decomp_settings()),("profiles",profile_settings())):
            self.assertEqual(self.run_stage(stage,settings)["status"],"done")
        self.assertEqual(self.run_stage("fit",fit_config("Si"))["status"],"done")
        si_id=self.manager.fits["Si"]["result_id"]
        self.assertEqual(self.run_stage("fit",fit_config("Ge"))["status"],"done")
        self.assertEqual(self.client.get("/api/workflow/fit",query_string={"result_id":si_id}).status_code,200)
        preview=self.client.post("/api/workflow/preview",json={"profiles_id":self.manager.results["profiles"]["result_id"],
            "selection":{"kind":"region","region":"Si"},"analysis_kind":"bulk_gaussian"})
        self.assertEqual(preview.status_code,200);self.assertNotIn("residual",preview.json)
        batch={"regions":{name:fit_config(name) for name in ("Si","Ge","Interface","Core")}}
        self.assertEqual(self.run_stage("fit_regions",batch)["status"],"done")
        prior={key:fit["result_id"] for key,fit in self.manager.fits.items()}
        broken=copy.deepcopy(batch);broken["regions"]["Core"]["fwhm_max"]=.01
        self.assertEqual(self.run_stage("fit_regions",broken)["status"],"error")
        self.assertEqual({key:fit["result_id"] for key,fit in self.manager.fits.items()},prior)
        payload={"stage":"fit_regions","settings":batch,"profiles_id":self.manager.results["profiles"]["result_id"]}
        timed=self.wait(self.manager.start(payload,timeout=.001));self.assertEqual(timed["status"],"error")
        self.assertEqual({key:fit["result_id"] for key,fit in self.manager.fits.items()},prior)
        state=self.client.get("/api/workflow/state").json;self.assertEqual(state["fit_result_ids"],prior)
        ids={stage:self.manager.results[stage]["result_id"] for stage in ("decomposition","profiles")}
        doc=self.client.post("/api/workflow/export",json={"result_ids":ids})
        self.assertEqual(doc.status_code,200,doc.text)
        self.assertEqual(len(doc.json["results"]["regions"]),4)
        self.assertTrue(all(region["status"]=="completed" for region in doc.json["results"]["regions"].values()))
        self.assertEqual(self.run_stage("profiles",profile_settings())["status"],"done")
        self.assertEqual(self.manager.fits,{})
        self.assertEqual(self.client.get("/api/workflow/fit",query_string={"result_id":prior["Ge"]}).status_code,400)


if __name__=="__main__":unittest.main()
