"""Display-only reference maps and operational transition centers.

No ROI, input array, peak analysis, or result recipe is changed here.
"""
from __future__ import annotations

import warnings
import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit
from scipy.special import erf

from .io import EELSData

REFERENCE_MAP_VERSION = "reference_maps_v1"


def _matrix(values):
    return [[float(v) if np.isfinite(v) else None for v in row] for row in values]


def _mean(values, axis):
    finite = np.isfinite(values)
    count = finite.sum(axis=axis)
    return np.divide(np.where(finite, values, 0).sum(axis=axis), count,
                     out=np.full(np.shape(count), np.nan), where=count > 0)


def _reference_columns(nx):
    if nx < 8:
        raise ValueError("至少需要 8 列像素才能从两侧建立参考谱。")
    if nx >= 20:
        return slice(2, 8), slice(nx - 8, nx - 2)
    width = max(2, nx // 4)
    return slice(0, width), slice(nx - width, nx)


def _transition(x, center, sigma, offset, amplitude):
    return offset + amplitude * 0.5 * (1 + erf((x-center)/(np.sqrt(2)*sigma)))


def candidate_center(values, x, left, right):
    """Fit the all-row mean, rejecting flat/poor/unresolved transitions."""
    profile = _mean(values, axis=0)
    if len(x) < 8 or not np.all(np.isfinite(profile)):
        return {"status": "unavailable", "reason": "候选中心需要至少 8 列完整有效的衬度。"}
    low, high = float(profile[left].mean()), float(profile[right].mean())
    scale = max(float(np.max(np.abs(profile))), 1e-30)
    if abs(high-low) <= scale * 1e-6:
        return {"status": "unavailable", "reason": "两侧没有可辨认的衬度差，未标候选中心。"}
    contrast = (profile-low)/(high-low)
    step = float(np.median(np.diff(x)))
    span = float(x[-1]-x[0])
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", OptimizeWarning)
            p, _ = curve_fit(_transition, x, contrast,
                             p0=[float(np.median(x)), max(step,span/10), 0, 1],
                             bounds=([x[1],step/4,-0.3,0.4], [x[-2],span/2,0.3,1.6]),
                             maxfev=3000)
        rmse = float(np.sqrt(np.mean((contrast-_transition(x,*p))**2)))
        if rmse > 0.15 or p[1] >= span*0.49 or not x[1]+step*0.05 < p[0] < x[-2]-step*0.05:
            raise ValueError("衬度过渡不清楚，未标候选中心。")
        return {"status": "available", "x_nm": float(p[0]), "rmse": rmse,
                "method": "all_rows_mean_erf_midpoint", "sigma_nm": float(p[1])}
    except (ValueError, RuntimeError, OptimizeWarning, FloatingPointError):
        return {"status": "unavailable", "reason": "衬度过渡拟合不可靠，未标候选中心。"}


def _nnls_two(refs, spectra):
    coef = np.linalg.lstsq(refs, spectra.T, rcond=None)[0].T
    invalid = np.any(coef < 0, axis=1)
    if invalid.any():
        data = spectra[invalid]
        c0 = np.column_stack((np.maximum(data @ refs[:,0]/(refs[:,0]@refs[:,0]),0), np.zeros(len(data))))
        c1 = np.column_stack((np.zeros(len(data)),np.maximum(data @ refs[:,1]/(refs[:,1]@refs[:,1]),0)))
        e0 = np.sum((data-c0@refs.T)**2,axis=1)
        e1 = np.sum((data-c1@refs.T)**2,axis=1)
        coef[invalid] = np.where((e0 <= e1)[:,None],c0,c1)
    return coef


def spectral_contribution(data, left, right):
    energy = data.energy_meV
    if energy[0] > 15 or energy[-1] < 75:
        raise ValueError("参考谱图需要完整覆盖 15–75 meV；不会扩大或改动分析能窗。")
    lower = (energy >= 15)&(energy <= 45)
    upper = (energy >= 55)&(energy <= 75)
    if lower.sum() < 8 or upper.sum() < 8:
        raise ValueError("15–45 或 55–75 meV 内不足 8 个通道，无法建立参考谱图。")
    selection = lower | upper
    refs = np.stack((_mean(data.intensity[:,left,:][:,:,selection], axis=(0,1)),
                     _mean(data.intensity[:,right,:][:,:,selection], axis=(0,1))),axis=1)
    sums = refs.sum(axis=0)
    if not np.all(np.isfinite(refs)) or np.any(sums <= 0):
        raise ValueError("两侧参考谱缺少有效正信号，无法计算相对贡献。")
    refs = refs / sums
    if np.linalg.cond(refs) > 1e4:
        raise ValueError("两侧参考谱过于相似，无法可靠区分贡献。")
    # Bounded chunks avoid a second complete spectrum-image copy.
    pixel_count = data.shape[0]*data.shape[1]
    channels = np.flatnonzero(selection)
    fraction = np.full(pixel_count,np.nan)
    for start in range(0,pixel_count,2048):
        rows, columns = np.divmod(np.arange(start,min(start+2048,pixel_count)),data.shape[1])
        spectra = data.intensity[rows[:,None],columns[:,None],channels[None,:]]
        valid = np.all(np.isfinite(spectra),axis=1)
        coef = _nnls_two(refs,spectra[valid])
        total = coef.sum(axis=1)
        weight = np.divide(coef[:,1],total,out=np.full(len(coef),np.nan),where=total>0)
        fraction[start:start+len(spectra)][valid] = weight
    if not np.any(np.isfinite(fraction)):
        raise ValueError("当前数据没有可用的参考谱贡献。")
    return fraction.reshape(data.shape[:2])


def build_reference_maps(data: EELSData):
    maps = {}
    centers = []
    try:
        left,right = _reference_columns(data.shape[1])
    except ValueError:
        left,right = slice(0,1),slice(-1,None)

    def add(kind, label, values=None, *, reason=None, palette="parula", description="", center=False):
        entry = {"kind":kind,"label":label,"available":values is not None,
                 "description":description,"palette":palette}
        if values is not None:
            entry.update(values=_matrix(values),x_nm=data.x_nm.tolist(),y_nm=data.y_nm.tolist())
            if center:
                result = candidate_center(values,data.x_nm,left,right)
                centers.append({"kind":kind,"label": "总计数中心" if kind=="total_counts" else "光谱中心",**result})
        else:
            entry["reason"] = reason
        maps[kind] = entry

    counts = data.total_counts
    if counts is not None and (counts.shape != data.shape[:2] or not np.any(np.isfinite(counts))):
        counts = None
    add("total_counts","EELS 总计数图",counts,
        reason=data.attributes.get("total_counts_error", "此 HDF5 未保存总计数图；导入对应 MAT 后可读取 info.totint。"),
        palette="gray",description="源文件保留的 EELS.info.totint 总计数衬度（非 HAADF）",center=True)
    energy = data.energy_meV
    mask = (energy>=27)&(energy<=32)
    if energy[0]<=27 and energy[-1]>=32 and mask.sum()>=2:
        band = np.trapezoid(data.intensity[:,:,mask],energy[mask],axis=2)
        if np.any(np.isfinite(band)):
            add("band_27_32","27–32 meV 参考图",band,description="固定 27–32 meV 积分；分析能窗不变")
        else:
            add("band_27_32","27–32 meV 参考图",reason="该能窗没有有效信号。")
    else:
        add("band_27_32","27–32 meV 参考图",reason="数据未完整覆盖 27–32 meV 或通道不足。")
    try:
        left,right = _reference_columns(data.shape[1])
        fraction = spectral_contribution(data,left,right)
        add("spectral_contribution","Si/Ge 参考谱贡献图",fraction,palette="diverging",
            description="两侧参考谱非负分解：15–45 与 55–75 meV（避开 45–55 meV）",center=True)
        maps["spectral_contribution"]["reference_x_nm"] = [
            data.x_nm[left][[0,-1]].tolist(),data.x_nm[right][[0,-1]].tolist()]
    except (ValueError, np.linalg.LinAlgError) as error:
        add("spectral_contribution","Si/Ge 参考谱贡献图",reason=str(error))
    return {"version":REFERENCE_MAP_VERSION,"maps":maps,"centers":centers,
            "note":"候选中心为沿 y 平均的过渡中点，未独立验证为结构界面。参考谱取图像左右两侧，不使用或修改 Si／界面／Ge 选框；贡献比例不是原子成分。"}
