from pathlib import Path
import tempfile
import unittest

import h5py
import numpy as np
from scipy.special import erf

from eelsfit.io import EELSData, load_eels_hdf5, write_eels_hdf5
from eelsfit.reference_maps import build_reference_maps, candidate_center


def synthetic_reference_data():
    x = np.linspace(0,16,65)
    y = np.arange(6,dtype=float)
    energy = np.linspace(0,100,401)
    fraction = 0.5*(1+erf((x-8)/(np.sqrt(2)*0.8)))
    si = np.exp(-0.5*((energy-60)/4)**2)+0.3*np.exp(-0.5*((energy-30)/3)**2)
    ge = np.exp(-0.5*((energy-34)/4)**2)+0.05*np.exp(-0.5*((energy-60)/3)**2)
    mask = ((energy>=15)&(energy<=45))|((energy>=55)&(energy<=75))
    si /= si[mask].sum(); ge /= ge[mask].sum()
    cube = np.broadcast_to((1-fraction[:,None])*si+fraction[:,None]*ge,(len(y),len(x),len(energy))).copy()
    counts = np.broadcast_to(100+200*fraction,(len(y),len(x))).copy()
    return EELSData(cube,x,y,energy,Path('synthetic.h5'),{},counts)


class ReferenceMapTests(unittest.TestCase):
    def test_centers_and_held_out_interface_signal(self):
        data = synthetic_reference_data()
        original = data.intensity.copy()
        result = build_reference_maps(data)
        for center in result['centers']:
            self.assertEqual(center['status'],'available')
            self.assertAlmostEqual(center['x_nm'],8,places=2)
        np.testing.assert_array_equal(data.intensity,original)
        baseline = result['maps']['spectral_contribution']['values']
        data.intensity[:,:,(data.energy_meV>45)&(data.energy_meV<55)] += 100
        np.testing.assert_allclose(build_reference_maps(data)['maps']['spectral_contribution']['values'],baseline)

    def test_missing_counts_are_not_fabricated(self):
        data = synthetic_reference_data()
        data.total_counts = None
        maps = build_reference_maps(data)
        self.assertFalse(maps['maps']['total_counts']['available'])
        self.assertNotIn('total_counts',[center['kind'] for center in maps['centers']])
        self.assertTrue(maps['maps']['spectral_contribution']['available'])

    def test_flat_or_spectrally_indistinguishable_data_has_no_spurious_center(self):
        data = synthetic_reference_data()
        data.intensity[:] = data.intensity[0,0]
        data.total_counts[:] = 4
        result = build_reference_maps(data)
        self.assertFalse(result['maps']['spectral_contribution']['available'])
        self.assertTrue(all(c['status']=='unavailable' for c in result['centers']))
        self.assertEqual(candidate_center(np.ones((6,65)),data.x_nm,slice(0,6),slice(-6,None))['status'],'unavailable')

    def test_nan_pixels_and_insufficient_energy(self):
        data = synthetic_reference_data()
        data.intensity[0,0,:] = np.nan
        result = build_reference_maps(data)
        self.assertIsNone(result['maps']['spectral_contribution']['values'][0][0])
        data.energy_meV = data.energy_meV+200
        result = build_reference_maps(data)
        self.assertFalse(result['maps']['spectral_contribution']['available'])
        self.assertFalse(result['maps']['band_27_32']['available'])

    def test_hdf5_optional_map_roundtrip_and_matlab_square_orientation(self):
        with tempfile.TemporaryDirectory(prefix='.tmp_reference_io_',dir=Path(__file__).resolve().parents[1]) as folder:
            data = synthetic_reference_data()
            path = Path(folder)/'roundtrip.h5'
            write_eels_hdf5(data,path)
            actual = load_eels_hdf5(path)
            np.testing.assert_array_equal(actual.intensity,data.intensity)
            np.testing.assert_array_equal(actual.total_counts,data.total_counts)
            self.assertEqual(actual.attributes['reference_maps_schema'],'eels_reference_maps_v1')
            self.assertEqual(actual.attributes['total_counts_status'],'available')
            square = Path(folder)/'matlab.h5'
            counts=np.arange(9).reshape(3,3)
            with h5py.File(square,'w') as f:
                f['intensity']=np.ones((10,3,3))
                f['x_nm']=np.arange(3);f['y_nm']=np.arange(3);f['energy_meV']=np.arange(10)
                d=f.create_dataset('reference_maps/total_counts',data=counts.T)
                d.attrs['axis_order']='x_nm,y_nm'
            np.testing.assert_array_equal(load_eels_hdf5(square).total_counts,counts)
            with h5py.File(square,'a') as f:
                del f['reference_maps/total_counts']
                f['reference_maps/total_counts']=np.ones((2,4))
            actual=load_eels_hdf5(square)
            self.assertIsNone(actual.total_counts)
            self.assertIn('total_counts_error',actual.attributes)

    def test_declared_total_counts_require_valid_direction_source_and_presence(self):
        with tempfile.TemporaryDirectory(prefix='.tmp_reference_schema_',dir=Path(__file__).resolve().parents[1]) as folder:
            data = synthetic_reference_data()
            for problem in ('missing_axis','unknown_axis','wrong_source','missing_dataset','complex_values'):
                with self.subTest(problem=problem):
                    path = Path(folder)/(problem+'.h5')
                    write_eels_hdf5(data,path)
                    with h5py.File(path,'a') as f:
                        counts = f['reference_maps/total_counts']
                        if problem == 'missing_axis':
                            del counts.attrs['axis_order']
                        elif problem == 'unknown_axis':
                            counts.attrs['axis_order'] = 'unrecognized'
                        elif problem == 'wrong_source':
                            counts.attrs['source_field'] = 'processed_band_integral'
                        elif problem == 'missing_dataset':
                            del f['reference_maps/total_counts']
                        else:
                            del f['reference_maps/total_counts']
                            f['reference_maps/total_counts'] = data.total_counts.astype(complex)+1j
                    actual = load_eels_hdf5(path)
                    np.testing.assert_array_equal(actual.intensity,data.intensity)
                    self.assertIsNone(actual.total_counts)
                    self.assertIn('total_counts_error',actual.attributes)
                    self.assertFalse(build_reference_maps(actual)['maps']['total_counts']['available'])
            data.total_counts = None
            path = Path(folder)/'source_without_counts.h5'
            write_eels_hdf5(data,path)
            actual = load_eels_hdf5(path)
            self.assertEqual(actual.attributes['total_counts_status'],'missing_in_input')
            self.assertIn('检查源 MAT',actual.attributes['total_counts_error'])
            data.attributes['total_counts_status'] = 'missing_in_source'
            confirmed = Path(folder)/'confirmed_missing_in_mat.h5'
            write_eels_hdf5(data,confirmed)
            self.assertIn('源 MAT 未保存',load_eels_hdf5(confirmed).attributes['total_counts_error'])

    def test_invalid_total_counts_do_not_leave_a_partial_python_export(self):
        with tempfile.TemporaryDirectory(prefix='.tmp_reference_invalid_',dir=Path(__file__).resolve().parents[1]) as folder:
            data = synthetic_reference_data()
            for index, counts in enumerate((np.ones((2,3)),np.full(data.shape[:2],np.nan),np.ones(data.shape[:2],complex))):
                data.total_counts = counts
                path = Path(folder)/f'invalid{index}.h5'
                with self.assertRaises(ValueError):
                    write_eels_hdf5(data,path)
                self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
