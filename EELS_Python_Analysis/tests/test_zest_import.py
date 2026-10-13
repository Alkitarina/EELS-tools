"""ZIP geometry, signal semantics, atomic imports and analysis/replay coverage."""
import copy
import gzip
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
import zipfile

import numpy as np
from threadpoolctl import threadpool_limits

from eelsfit.io import EELSData, load_eels_hdf5
from eelsfit.importing import prepare_import
from eelsfit.runtime_paths import test_runs_dir
from eelsfit.zest_import import read_zest_archive, VERSION


def make_archive(path, *, geometry=True, parallel=True, missing=(), shifted=False,
                 mutate=None, prefix="", dataset_json=False, data=None, producer_schema=False, unit="nm",
                 counts=False, count_sources=("maps", "pixels", "document"), mutate_counts=None):
    energy = np.linspace(0., 100., 101)
    if data is None:
        values = np.array([[10*np.exp(-.5*((energy-45)/5)**2) + .1*y + .02*x - .3
                           for x in range(5)] for y in range(3)])
        data = EELSData(values, np.arange(5)*.5, np.arange(3)*.25, energy, Path(path), {})
    energy, values = data.energy_meV, data.intensity
    ny, nx, _ = values.shape
    # Legacy ZEST iterates y first; this deliberately differs from C order.
    coords = [[y, x] for x in range(nx) for y in range(ny)]
    ids = [i for i in range(len(coords)) if i not in missing]
    maps = {"R2": np.ones((ny, nx)), "coords": np.array([coords[i] for i in ids])}
    if parallel:
        maps["pixel_indices"] = np.array(ids)
    if geometry and not dataset_json and not producer_schema:
        maps.update(x_nm=data.x_nm, y_nm=data.y_nm)
    spatial = None
    if producer_schema:
        factor = {"nm": 1., "um": 1000., "pixel": 1.}[unit]
        calibrated = unit != "pixel"
        spatial = dict(schema_version=1,shape=[ny,nx],axis_order="YX",pixel_index_base=0,
                       coordinate_reference="pixel_center",status="calibrated" if calibrated else "unavailable")
        maps.update(spatial_schema_version=1,spatial_axis_order="YX",spatial_pixel_index_base=0,
                    spatial_coordinate_reference="pixel_center",spatial_status=spatial['status'])
        for name,axis in (("x",data.x_nm),("y",data.y_nm)):
            spatial[name]=dict(values=(axis/factor).tolist(),unit=unit,calibrated=calibrated,
                               source=name+'_axis',unit_source='metadata',origin_source='axis_values')
            maps['spatial_'+name]=axis/factor
            maps['spatial_'+name+'_unit']=unit
        maps['spatial_positions_yx']=np.array([[data.y_nm[coords[i][0]]/factor,data.x_nm[coords[i][1]]/factor] for i in ids])
    pixels = {}
    for i in ids:
        y, x = coords[i]
        zlp = 20*np.exp(-.5*(energy/3)**2)
        pixels[i] = {"ene_raw": energy.tolist(),
                     "ene_corrected": (energy + (i%3)*.1 if shifted else energy).tolist(),
                     "raw_data": (values[y,x]+zlp).tolist(), "zlp_only": zlp.tolist(),
                     "vib_only": (values[y,x]*.9).tolist(), "norm_val": 987654.,
                     "settings": {"software_version": "fixture"},
                     "unrelated_statistic": {"__zest_float__": "nan"}}
        if parallel:
            pixels[i].update(pixel_y=y+1, pixel_x=x+1)
    count_document = None
    if counts:
        if data.total_counts is None:
            data.total_counts = np.arange(ny*nx, dtype=float).reshape(ny,nx)*13 + 1000
        source_map = np.full((ny,nx),np.nan)
        rows = []
        for i in ids:
            y,x = coords[i]
            value = data.total_counts[y,x]
            source_map[y,x] = value
            info = dict(schema_version=1,source_totint=float(value) if np.isfinite(value) else None)
            rows.append(dict(file=f"pixels/{i:06d}.json.gz",result_index=i,pixel_y=y+1,pixel_x=x+1,count_info=copy.deepcopy(info)))
            if "pixels" in count_sources:
                pixels[i]["count_info"] = info
        if "maps" in count_sources:
            maps.update(counts_schema_version=1,counts_source_totint=source_map,
                        counts_total=np.full((ny,nx),123.),counts_available=np.zeros((ny,nx),dtype=bool))
        if "document" in count_sources:
            count_document = dict(schema_version=1,shape=[ny,nx],pixels=rows)
            if mutate_counts:
                mutate_counts(count_document)
    summary = {"job": {"id": "fixture", "coords": coords,
                        "status": "cancelled" if missing else "completed"}}
    if mutate:
        mutate(pixels, maps, summary)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **maps)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(prefix+"summary.json", json.dumps(summary))
        archive.writestr(prefix+"maps.npz", buffer.getvalue())
        if count_document is not None:
            archive.writestr(prefix+"counts.json",json.dumps(count_document))
        if spatial is not None:
            archive.writestr(prefix+"spatial_calibration.json",json.dumps(spatial))
        if dataset_json and geometry:
            archive.writestr(prefix+"dataset.json", json.dumps({"x_nm": data.x_nm.tolist(), "y_nm": data.y_nm.tolist()}))
        for i, pixel in pixels.items():
            archive.writestr(prefix+f"pixels/{i:06d}.json.gz", gzip.compress(json.dumps(pixel).encode()))
    return data


class ZestImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".tmp_zest_unit_", dir=test_runs_dir())
        self.folder = Path(self.tmp.name)
        self.source = self.folder / "测试 7.2.zip"
        self.output = self.folder / "converted.h5"
        self.limits = threadpool_limits(limits=1)

    def tearDown(self):
        self.limits.restore_original_limits()
        self.tmp.cleanup()

    def test_old_and_parallel_orientation_signed_signal_scale_and_provenance(self):
        for parallel in (False, True):
            with self.subTest(parallel=parallel):
                expected = make_archive(self.source, parallel=parallel)
                result = read_zest_archive(self.source)
                np.testing.assert_allclose(result.intensity, expected.intensity, atol=1e-14)
                np.testing.assert_array_equal(result.energy_meV, expected.energy_meV)
                self.assertLess(result.intensity.min(), 0)
                self.assertEqual(result.provenance["valid_pixels"], 15)
                self.assertFalse(result.provenance["refit"])
        loaded = prepare_import(self.source, self.output)
        np.testing.assert_allclose(loaded.intensity, expected.intensity, atol=1e-14)
        np.testing.assert_array_equal(loaded.x_nm, expected.x_nm)
        np.testing.assert_array_equal(loaded.y_nm, expected.y_nm)
        self.assertEqual(loaded.attributes["zest_import"], result.provenance)
        self.assertEqual(loaded.attributes["original_source"], str(self.source))
        self.assertIsNone(loaded.total_counts)
        with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
            prepare_import(self.source, self.output)

    def test_explicit_model_and_raw_choices_never_use_norm_val(self):
        expected = make_archive(self.source)
        model = read_zest_archive(self.source, {"signal": "vib_only"})
        raw = read_zest_archive(self.source, {"signal": "raw_data"})
        np.testing.assert_allclose(model.intensity, expected.intensity*.9)
        np.testing.assert_allclose(raw.intensity, expected.intensity + 20*np.exp(-.5*(expected.energy_meV/3)**2))

    def test_source_counts_roundtrip_all_signals_and_available_reference(self):
        from eelsfit.reference_maps import build_reference_maps
        expected = make_archive(self.source, counts=True, prefix="export/")
        for signal in ("raw_minus_zlp", "vib_only", "raw_data"):
            result = read_zest_archive(self.source, {"signal":signal,"energy_axis":"corrected"})
            np.testing.assert_array_equal(result.total_counts,expected.total_counts)
            self.assertEqual(result.provenance['total_counts_valid_pixels'],15)
        loaded = prepare_import(self.source,self.output)
        np.testing.assert_array_equal(loaded.total_counts,expected.total_counts)
        self.assertEqual(loaded.attributes['total_counts_status'],'available')
        reference = build_reference_maps(loaded)['maps']['total_counts']
        self.assertTrue(reference['available'])
        np.testing.assert_array_equal(reference['values'],expected.total_counts)

    def test_individual_count_sources_and_missing_source_are_supported(self):
        for sources in (("maps",),("pixels",),("document",)):
            with self.subTest(sources=sources):
                expected = make_archive(self.source,counts=True,count_sources=sources)
                np.testing.assert_array_equal(read_zest_archive(self.source).total_counts,expected.total_counts)
        expected.total_counts[:] = np.nan
        make_archive(self.source,data=expected,counts=True)
        result = read_zest_archive(self.source)
        self.assertIsNone(result.total_counts)  # counts_total is deliberately nonzero.
        self.assertEqual(result.provenance['total_counts_status'],'missing_in_input')

    def test_counts_partial_export_failed_spectrum_and_axis_reversal(self):
        expected = make_archive(self.source,counts=True)
        expected.total_counts[0,1] = np.nan
        expected.total_counts[1,2] = 0
        descending = copy.deepcopy(expected)
        descending.x_nm = descending.x_nm[::-1]
        descending.y_nm = descending.y_nm[::-1]
        descending.intensity = descending.intensity[::-1,::-1,:]
        descending.total_counts = descending.total_counts[::-1,::-1]
        make_archive(self.source,data=descending,counts=True,missing=(0,),
                     mutate=lambda p,m,s:p[2].update(error="fit failed"))
        result = read_zest_archive(self.source)
        expected.total_counts[-1,-1] = np.nan
        np.testing.assert_array_equal(result.total_counts,expected.total_counts)
        self.assertTrue(np.isnan(result.intensity[0,-1]).all())
        self.assertTrue(np.isfinite(result.total_counts[0,-1]))
        loaded = prepare_import(self.source,self.output)
        np.testing.assert_array_equal(loaded.total_counts,expected.total_counts)

    def test_invalid_or_conflicting_counts_fail_without_publishing(self):
        mutations = [
            lambda p,m,s:m.update(counts_schema_version=2),
            lambda p,m,s:m.update(counts_source_totint=np.ones((5,3))),
            lambda p,m,s:m.update(counts_source_totint=np.ones((3,5),dtype=complex)),
            lambda p,m,s:m['counts_source_totint'].__setitem__((0,0),np.inf),
            lambda p,m,s:p[0]['count_info'].update(source_totint=42.),
            lambda p,m,s:p[0]['count_info'].update(source_totint=None),
            lambda p,m,s:p[0]['count_info'].update(source_totint=True),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                make_archive(self.source,counts=True,mutate=mutate)
                with self.assertRaises(ValueError):
                    prepare_import(self.source,self.output)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.output.with_name(self.output.name+'.writing').exists())
        for change in (lambda d:d.update(schema_version=2),
                       lambda d:d['pixels'][0].update(pixel_x=5),
                       lambda d:d['pixels'][0]['count_info'].update(source_totint=99),
                       lambda d:d['pixels'].pop()):
            make_archive(self.source,counts=True,mutate_counts=change)
            with self.assertRaises(ValueError):
                prepare_import(self.source,self.output)
        make_archive(self.source,counts=True,missing=(0,),
                     mutate=lambda p,m,s:m['counts_source_totint'].__setitem__((0,0),123.))
        with self.assertRaisesRegex(ValueError,'未导出像素'):
            prepare_import(self.source,self.output)

    def test_corrected_axes_interpolate_only_shared_support(self):
        expected = make_archive(self.source, shifted=True)
        result = read_zest_archive(self.source, {"energy_axis": "corrected"})
        np.testing.assert_array_equal(result.energy_meV, expected.energy_meV[1:])
        np.testing.assert_allclose(result.intensity[1,0], np.interp(result.energy_meV, expected.energy_meV+.1, expected.intensity[1,0]))

    def test_partial_parallel_export_keeps_original_indices_and_nan_gaps(self):
        expected = make_archive(self.source, missing=(0,4,11))
        result = read_zest_archive(self.source)
        self.assertEqual(result.provenance["missing_pixels"], 3)
        self.assertTrue(np.isnan(result.intensity[0,0]).all())
        self.assertTrue(np.isnan(result.intensity[1,1]).all())
        self.assertTrue(np.isnan(result.intensity[2,3]).all())
        np.testing.assert_allclose(result.intensity[2,4], expected.intensity[2,4])

    def test_explicit_failed_pixel_stays_missing(self):
        make_archive(self.source, mutate=lambda p,m,s: p.update({2: {"error": "fit failed"}}))
        result = read_zest_archive(self.source)
        self.assertTrue(np.isnan(result.intensity[2,0]).all())
        self.assertEqual(result.provenance["failed_pixels"], [{"index": 2, "reason": "fit failed"}])

    def test_partial_data_has_valid_browser_json_and_reports_actual_pixel_count(self):
        from eels_web import create_app
        make_archive(self.source,missing=(0,4,11),producer_schema=True)
        prepare_import(self.source,self.output)
        app=create_app(self.output)
        client=app.test_client()
        try:
            response=client.get('/api/map?fit_min=20&fit_max=70')
            self.assertEqual(response.status_code,200,response.text)
            values=json.loads(response.text,parse_constant=lambda value:self.fail('Nonstandard JSON: '+value))['values']
            self.assertIsNone(values[0][0])
            self.assertIsNotNone(values[2][4])
            payload=dict(region='Si',roi=dict(x_min=0,x_max=2,y_min=0,y_max=.5),fit_min=20,fit_max=70)
            preview=client.post('/api/preview',json=payload)
            self.assertEqual(preview.status_code,200,preview.text)
            self.assertEqual(preview.json['pixel_count'],12)
            payload['roi']=dict(x_min=0,x_max=0,y_min=0,y_max=0)
            empty=client.post('/api/preview',json=payload)
            self.assertEqual(empty.status_code,400)
            self.assertIn('没有有效谱线',empty.json['error'])
        finally:
            app.extensions['eels_service'].shutdown()

    def test_missing_calibration_is_readable_but_cannot_be_claimed_as_nm(self):
        make_archive(self.source, geometry=False)
        result = read_zest_archive(self.source)
        self.assertIsNone(result.x_nm)
        self.assertEqual(result.intensity.shape, (3,5,101))
        with self.assertRaisesRegex(ValueError, "空间标定"):
            prepare_import(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_wrapped_archive_dataset_geometry_and_no_extraction(self):
        expected = make_archive(self.source, prefix="export/", dataset_json=True)
        result = read_zest_archive(self.source)
        np.testing.assert_array_equal(result.x_nm, expected.x_nm)
        self.assertEqual(list(self.folder.iterdir()), [self.source])

    def test_actual_producer_spatial_schema_units_and_descending_axes(self):
        expected = make_archive(self.source)
        descending = copy.deepcopy(expected)
        descending.x_nm = descending.x_nm[::-1]
        descending.y_nm = descending.y_nm[::-1]
        descending.intensity = descending.intensity[::-1,::-1,:]
        for unit in ('nm','um'):
            with self.subTest(unit=unit):
                make_archive(self.source,data=descending,producer_schema=True,unit=unit)
                result = read_zest_archive(self.source)
                np.testing.assert_array_equal(result.x_nm,expected.x_nm)
                np.testing.assert_array_equal(result.y_nm,expected.y_nm)
                np.testing.assert_allclose(result.intensity,expected.intensity,atol=1e-14)
                self.assertEqual(result.provenance['spatial_axes_reversed'],['x','y'])
                self.assertEqual(result.provenance['spatial_metadata']['x']['unit'],unit)

    def test_producer_pixel_only_metadata_is_not_nm(self):
        make_archive(self.source,producer_schema=True,unit='pixel')
        result = read_zest_archive(self.source)
        self.assertIsNone(result.x_nm)
        with self.assertRaisesRegex(ValueError,'空间标定'):
            prepare_import(self.source,self.output)

    def test_conflicting_producer_metadata_or_pixel_positions_fail(self):
        for mutate in (lambda p,m,s: m['spatial_x'].__setitem__(0,-99),
                       lambda p,m,s: m['spatial_positions_yx'].__setitem__(0,[9.,8.])):
            with self.subTest(mutate=mutate):
                make_archive(self.source,producer_schema=True,mutate=mutate)
                with self.assertRaisesRegex(ValueError,'空间|坐标'):
                    prepare_import(self.source,self.output)

    def test_invalid_archives_fail_without_publishing(self):
        mutations = [
            lambda p,m,s: s['job']['coords'].__setitem__(1, s['job']['coords'][0]),
            lambda p,m,s: p[0].update(pixel_y=99),
            lambda p,m,s: m.update(pixel_indices=np.arange(15)[::-1]),
            lambda p,m,s: p[1].update(ene_raw=(np.arange(101)+.1).tolist()),
            lambda p,m,s: p[0]['raw_data'].__setitem__(0, {"__zest_float__":"nan"}),
            lambda p,m,s: m.update(x_nm=np.arange(4)),
            lambda p,m,s: p.pop(3),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                make_archive(self.source, mutate=mutate)
                with self.assertRaises(ValueError):
                    prepare_import(self.source, self.output)
                self.assertFalse(self.output.exists())

    def test_api_upload_path_analysis_export_replay_and_failure_atomicity(self):
        from eels_web import create_app
        from reproduce import replay
        make_archive(self.source,producer_schema=True)
        app = create_app()
        app.config.update(IMPORT_ROOT=self.folder/'imports', UI_PREFERENCES_FILE=self.folder/'preferences.json')
        client = app.test_client()
        service = app.extensions['eels_service']
        def wait(response):
            self.assertEqual(response.status_code, 200, response.text)
            end = time.monotonic()+20
            while time.monotonic() < end:
                job = client.get('/api/jobs/'+response.json['job_id']).json
                if job['status'] not in {'queued','running'}:
                    return job
                time.sleep(.02)
            self.fail('Import timeout')
        try:
            with self.source.open('rb') as stream:
                status = wait(client.post('/api/import', data={'file': (stream,self.source.name),
                    'zest_options': json.dumps({'signal':'raw_minus_zlp','energy_axis':'raw'})}))
            self.assertEqual(status['status'], 'done', status)
            info = client.get('/api/info').json
            self.assertEqual(info['input_preparation']['version'], VERSION)
            selections = []
            for mode, region in [('manual','Si'),('auto','Ge'),('pca','Interface')]:
                payload = dict(region=region, roi=dict(x_min=0,x_max=2,y_min=0,y_max=.5),fit_min=20,fit_max=70,
                    config=dict(mode=mode,centers='45',center_lower='35',center_upper='55',fwhm_lower='4',fwhm_upper='18',
                    include_baseline=True,peak_count=1,search_starts=2,component_count=1,min_peak_distance_meV=5,min_prominence_percent=.5,seed=18))
                response = client.post('/api/pca' if mode=='pca' else '/api/fit',json=payload)
                self.assertEqual(response.status_code,200,response.text)
                selections.append(dict(region=region,result_id=response.json['result_id'],analysis_type='pca' if mode=='pca' else 'gaussian'))
            exported = client.post('/api/export',json={'results':selections})
            self.assertEqual(exported.status_code,200,exported.text)
            original = exported.json
            self.assertEqual(original['reproducibility']['data']['input_preparation']['options']['signal'],'raw_minus_zlp')
            result_file = self.folder/'result.json'
            result_file.write_bytes(exported.data)
            reproduced = replay(result_file)
            self.assertEqual(original['reproducibility']['data']['input_preparation'], reproduced['reproducibility']['data']['input_preparation'])
            for a,b in zip(original['results'],reproduced['results']):
                np.testing.assert_allclose([p['center_meV'] for p in a['peaks']],[p['center_meV'] for p in b['peaks']])
                self.assertEqual(a['uncertainty']['status'],'not_computed')
            prior = service.data
            ids = [r.result_id for r in service.results.values()]
            missing = self.folder/'old.zip'
            make_archive(missing,geometry=False)
            failed = wait(client.post('/api/import',json={'path':str(missing)}))
            self.assertEqual(failed['status'],'error')
            self.assertIn('空间标定',failed['error'])
            self.assertIs(service.data,prior)
            self.assertEqual(ids,[r.result_id for r in service.results.values()])
            for bad in ({'signal':[]},{'energy_axis':None},'bad'):
                self.assertEqual(client.post('/api/import',json={'path':str(self.source),'zest_options':bad}).status_code,400)
            status = wait(client.post('/api/import',json={'path':str(self.source),'zest_options':{'signal':'vib_only'}}))
            self.assertEqual(status['status'],'done',status)
            self.assertFalse(service.results)
            self.assertFalse(service.pca_results)
        finally:
            service.shutdown()

    def test_multivariate_export_and_replay_keep_zest_provenance(self):
        from tests.test_multivariate_workflow import synthetic_data, decomp_settings, profile_settings
        from eelsfit.multivariate import decompose
        from eelsfit.interface_workflow import build_profiles
        from eelsfit.workflow_export import export_document, software_identity
        from reproduce_workflow import replay_workflow
        make_archive(self.source, data=synthetic_data())
        data = prepare_import(self.source, self.output)
        d = decompose(data, decomp_settings()); d['result_id']='d'
        p = build_profiles(data,d,profile_settings()); p.update(result_id='p',decomposition_id='d')
        document = export_document(data,dict(decomposition=d,profiles=p),software_identity())
        result_file = self.folder/'workflow.json'
        result_file.write_text(json.dumps(document),encoding='utf-8')
        reproduced = replay_workflow(result_file)
        self.assertEqual(document['reproducibility']['data']['input_preparation'], reproduced['reproducibility']['data']['input_preparation'])
        self.assertEqual(document['reproducibility']['data']['checksum'],reproduced['reproducibility']['data']['checksum'])


if __name__ == '__main__':
    unittest.main()
