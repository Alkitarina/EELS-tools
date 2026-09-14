"""Bounded, optional contrast fits for proposing manually editable EELS ROIs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import warnings

import numpy as np
from scipy.optimize import OptimizeWarning, curve_fit
from scipy.special import expit, logit, ndtr, ndtri

REGION_FIT_VERSION = "contrast_region_fit_v1"
FIT_TIMEOUT_SECONDS = 8.0
DEFAULT_THRESHOLDS = {"si_bulk_percent": 99.0, "interface_low_percent": 10.0,
                      "interface_high_percent": 90.0, "ge_bulk_percent": 99.0}


def validate_thresholds(values):
    if not isinstance(values, dict):
        raise ValueError("请提供区域边界百分比。")
    try:
        result = {key: float(values.get(key, default)) for key, default in DEFAULT_THRESHOLDS.items()}
    except (TypeError, ValueError) as error:
        raise ValueError("区域边界必须是有效百分比。") from error
    if not all(np.isfinite(v) and 0 < v < 100 for v in result.values()):
        raise ValueError("百分比必须大于 0 且小于 100；100% 平台对应无穷远。")
    si, lo, hi, ge = (result[k] for k in DEFAULT_THRESHOLDS)
    if not (si > 50 and ge > 50 and 1-si/100 <= lo/100 < .5 < hi/100 <= ge/100):
        raise ValueError("请满足 Si 截止 ≤ 界面左界 < 50% < 界面右界 ≤ Ge 起点，避免区域重叠。")
    return result


def proposed_regions(x, y, model, center, scale, thresholds):
    """Percentages are contrast plateau criteria, never chemical abundances."""
    inverse = ndtri if model == "erf" else logit
    probabilities = [1-thresholds['si_bulk_percent']/100,
                     thresholds['interface_low_percent']/100,
                     thresholds['interface_high_percent']/100,
                     thresholds['ge_bulk_percent']/100]
    positions = center + scale * inverse(np.asarray(probabilities))
    names = ['si_end_nm', 'interface_start_nm', 'interface_end_nm', 'ge_start_nm']
    boundaries = dict(zip(names, map(float, positions)))
    intervals = {'Si': (float(x[0]), positions[0]),
                 'Interface': (positions[1], positions[2]), 'Ge': (positions[3], float(x[-1]))}
    if not np.all((positions >= x[0]) & (positions <= x[-1])):
        return boundaries, None, "所选平台百分比落在图像范围外；请降低体区百分比或手动选区。"
    rois = {}
    for region, (left, right) in intervals.items():
        if left >= right or not np.any((x >= left) & (x <= right)):
            return boundaries, None, f"{region} 区域没有覆盖有效像素；请调整边界百分比或手动选区。"
        rois[region] = {'x_min': float(left), 'x_max': float(right),
                        'y_min': float(y[0]), 'y_max': float(y[-1])}
    return boundaries, rois, None


def fit_profile(x_values, profile_values, y_values, thresholds):
    thresholds = validate_thresholds(thresholds)
    x_all = np.asarray(x_values, dtype=float)
    profile_all = np.asarray(profile_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    if (x_all.ndim != 1 or profile_all.shape != x_all.shape or len(x_all) < 8
            or not np.all(np.isfinite(x_all)) or np.any(np.diff(x_all) <= 0)
            or y.ndim != 1 or not len(y) or not np.all(np.isfinite(y)) or np.any(np.diff(y) <= 0)):
        raise ValueError("拟合需要至少 8 列衬度与有效、递增的空间坐标。")
    valid = np.isfinite(profile_all)
    x, profile = x_all[valid], profile_all[valid]
    if len(x) < 8:
        raise ValueError("有效衬度不足 8 列，无法拟合。")
    edge_count = max(2, min(6, len(x)//5))
    low, high = np.mean(profile[:edge_count]), np.mean(profile[-edge_count:])
    if abs(high-low) <= max(np.max(np.abs(profile)), 1e-30)*1e-6:
        raise ValueError("两侧缺少可辨认的衬度差，无法确定界面。")
    contrast = (profile-low)/(high-low)
    span, step = float(x[-1]-x[0]), float(np.median(np.diff(x)))
    models = {}
    curve_x = np.linspace(x_all[0], x_all[-1], 301)
    for model in ('erf', 'logistic'):
        cdf = ndtr if model == 'erf' else expit
        inverse = ndtri if model == 'erf' else logit
        def transition(xx, a, b, center, scale):
            return a+(b-a)*cdf((xx-center)/scale)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', OptimizeWarning)
                p, _ = curve_fit(transition, x, contrast,
                    p0=[0, 1, float(x[np.argmin(abs(contrast-.5))]), max(step, span/(10 if model=='erf' else 18))],
                    bounds=([-.5, .5, x[1], step/8], [.5, 1.5, x[-2], span/2]),
                    maxfev=1800, x_scale=[1, 1, span, span/6])
            a, b, center, scale = map(float, p)
            residual = contrast-transition(x, *p)
            rms = float(np.sqrt(np.mean(residual**2)))
            r2 = float(1-np.sum(residual**2)/np.sum((contrast-contrast.mean())**2))
            if (b-a < .2 or rms/(b-a) > .15 or r2 < .8 or scale >= span*.49
                    or not x[1]+step*.01 < center < x[-2]-step*.01
                    or a < -.499 or a > .499 or b < .501 or b > 1.499):
                raise ValueError("过渡或两侧平台不明确，拟合不足以可靠生成区域。")
            boundaries, rois, reason = proposed_regions(x_all, y, model, center, scale, thresholds)
            i_si, i_ge = float(low+(high-low)*a), float(low+(high-low)*b)
            models[model] = {'status': 'available', 'model': model, 'center_nm': center,
                'scale_nm': scale, 'scale_name': 'sigma' if model=='erf' else 's',
                'i_si': i_si, 'i_ge': i_ge, 'rmse': float(rms*abs(high-low)), 'r_squared': r2,
                'width_10_90_nm': float(scale*(inverse(.9)-inverse(.1))),
                'boundaries': boundaries, 'rois': rois, 'apply_error': reason,
                'curve_x_nm': curve_x.tolist(), 'curve_contrast': cdf((curve_x-center)/scale).tolist(),
                'profile_contrast': ((profile-i_si)/(i_ge-i_si)).tolist()}
        except (ValueError, RuntimeError, OptimizeWarning, FloatingPointError) as error:
            models[model] = {'status': 'unavailable', 'reason': str(error)}
    return {'models': models, 'thresholds': thresholds, 'profile_x_nm': x.tolist(),
            'profile_intensity': profile.tolist(), 'valid_columns': len(x)}


def run_bounded_fit(payload, timeout_seconds=FIT_TIMEOUT_SECONDS):
    """Kill and reap a stuck optimizer process; never leave a pool/job running."""
    env = os.environ.copy()
    env.update(OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', OMP_NUM_THREADS='1')
    try:
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-m', 'eelsfit.region_fit'],
            input=json.dumps(payload, allow_nan=False), capture_output=True, text=True, encoding='utf-8',
            cwd=Path(__file__).resolve().parents[1], env=env, timeout=timeout_seconds,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("区域拟合超时，计算进程已终止；原选框保持不变，可重试。") from error
    if result.returncode:
        raise RuntimeError("区域拟合进程异常退出；原选框保持不变，可重试。")
    try:
        reply = json.loads(result.stdout)
    except (ValueError, TypeError) as error:
        raise RuntimeError("区域拟合未返回有效结果，请重试。") from error
    if 'error' in reply:
        raise ValueError(reply['error'])
    return reply


if __name__ == '__main__':
    try:
        inputs = json.load(sys.stdin)
        output = fit_profile(inputs['x_nm'], inputs['profile'], inputs['y_nm'], inputs['thresholds'])
    except Exception as error:
        output = {'error': str(error)}
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))
