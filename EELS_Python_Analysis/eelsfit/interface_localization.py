"""Exploratory unmixing around the total interface ROI and explicit core proposals."""
import copy

import numpy as np
from scipy import ndimage

from .io import EELSData
from .multivariate import DecompositionSettings, decompose, select_energy
from .interface_workflow import region_masks, mean_spectrum

LOCALIZATION_RANGE_VERSION = "interface_x_expansion_v1"


def interface_crop(data, roi):
    try:
        bounds = {key: float(roi[key]) for key in ("x_min", "x_max", "y_min", "y_max")}
    except (KeyError, TypeError, ValueError):
        raise ValueError("总界面区须包含完整空间坐标。") from None
    xl, xr, yl, yr = bounds.values()
    if (not np.isfinite(list(bounds.values())).all() or xl > xr or yl > yr
            or xl < data.x_nm[0] or xr > data.x_nm[-1] or yl < data.y_nm[0] or yr > data.y_nm[-1]):
        raise ValueError("总界面区坐标倒置或超出数据范围。")
    xi = np.flatnonzero((data.x_nm >= xl) & (data.x_nm <= xr))
    yi = np.flatnonzero((data.y_nm >= yl) & (data.y_nm <= yr))
    if not len(xi) or not len(yi):
        raise ValueError("总界面区没有像素，请调整选框。")
    crop = EELSData(data.intensity[yi[0]:yi[-1]+1, xi[0]:xi[-1]+1], data.x_nm[xi], data.y_nm[yi],
                    data.energy_meV, data.source, {}, None)
    return crop, bounds


def localize_interface(data, decomposition, settings, progress=lambda *_: None):
    settings = copy.deepcopy(settings)
    crop, roi = interface_crop(data, settings.get("roi", {}))
    domain = None
    # Missing expansion retains the exact historical crop and recipe on replay.
    if "x_expansion_percent" in settings:
        if settings.get("range_rule", LOCALIZATION_RANGE_VERSION) != LOCALIZATION_RANGE_VERSION:
            raise ValueError("不支持的局部分解范围规则。")
        raw_percent = settings["x_expansion_percent"]
        try:
            percent = float(raw_percent)
        except (TypeError, ValueError):
            raise ValueError("两侧扩展比例须为 0–1000%。") from None
        if isinstance(raw_percent, bool) or not np.isfinite(percent) or not 0 <= percent <= 1000:
            raise ValueError("两侧扩展比例须为 0–1000%。")
        width = roi["x_max"] - roi["x_min"]
        if percent > 0 and width <= 0:
            raise ValueError("总界面横向宽度须大于 0，才能按比例扩展。")
        padding = width * percent / 100
        requested = {**roi, "x_min": roi["x_min"]-padding, "x_max": roi["x_max"]+padding}
        actual = {**requested, "x_min": max(float(data.x_nm[0]), requested["x_min"]),
                  "x_max": min(float(data.x_nm[-1]), requested["x_max"])}
        crop, actual = interface_crop(data, actual)
        domain = {"range_rule": LOCALIZATION_RANGE_VERSION, "x_expansion_percent": percent,
                  "requested_analysis_roi": requested, "analysis_roi": actual}
    parent = decomposition["settings"]
    cfg = {"method": "mcr_als", "components": 3, "init": "nndsvda", "starts": 3,
           "seed": 20260916, "max_iter": 200, "tolerance": 1e-5, "negative_policy": "reject",
           **settings.get("decomposition", {})}
    if cfg.get("init") not in {"nndsvda", "random"}:
        raise ValueError("局部分解请选择 NNDSVDa 或随机初始化。")
    # One common reliable window; changes to unrelated controls cannot relabel this run.
    cfg.update(fit_min=parent["fit_min"], fit_max=parent["fit_max"],
               preprocessing_confirmed=parent["preprocessing_confirmed"])
    DecompositionSettings(**cfg).validate()
    result = decompose(crop, cfg, lambda p, m: progress(p, "界面邻域局部分解："+m))
    result["settings"] = {"roi": roi, "decomposition": result["settings"], "includes_existing_core": True}
    result.update(x_nm=crop.x_nm.copy(), y_nm=crop.y_nm.copy())
    result["summary"].update(scope="total_interface_roi", roi_pixels=int(len(crop.x_nm)*len(crop.y_nm)),
                             includes_existing_core=True)
    if domain:
        result["settings"].update(domain)
        left_clipped = actual["x_min"] > requested["x_min"]
        right_clipped = actual["x_max"] < requested["x_max"]
        result["summary"].update(scope="expanded_interface_roi", analysis_domain={
            "base_width_nm": width, "requested_width_factor": 1+2*percent/100,
            "actual_width_nm": actual["x_max"]-actual["x_min"],
            "left_clipped": left_clipped, "right_clipped": right_clipped})
        if left_clipped or right_clipped:
            result["summary"]["warnings"].append("扩展范围超出图像；已截到现有 x 范围，未外推数据。")
    return result


def display_indices(result):
    return tuple(np.unique(np.r_[np.arange(0, len(result[key]), max(1, int(np.ceil(len(result[key])/limit)))),
                                  len(result[key])-1]) for key, limit in (("y_nm", 180), ("x_nm", 220)))


def local_view(result):
    yi, xi = display_indices(result)
    return {key: result[key] for key in ("result_id", "decomposition_id", "settings", "summary", "energy_meV", "spectra")} | {
        "x_nm": result["x_nm"][xi], "y_nm": result["y_nm"][yi],
        "pixel_x_nm": result["x_nm"], "pixel_y_nm": result["y_nm"],
        "weights": result["weights"][np.ix_(yi, xi)],
        "display_downsampled": len(xi)*len(yi) < len(result["x_nm"])*len(result["y_nm"])}


def core_proposal(data, result, settings):
    """Threshold full-resolution weights, then preview one connected region's bounding ROI."""
    component, percent = settings.get("component", 0), float(settings.get("threshold_percent", 80))
    minimum, direction = settings.get("min_pixels", 2), settings.get("direction", "high")
    choice = settings.get("candidate", 0)
    if (not isinstance(component, int) or isinstance(component, bool) or not 0 <= component < result["weights"].shape[2]
            or not np.isfinite(percent) or not 0 <= percent <= 100 or direction not in {"high", "low"}
            or not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1
            or not isinstance(choice, int) or isinstance(choice, bool) or choice < 0):
        raise ValueError("请检查局部组分、权重阈值、最小连通像素数和候选区。")
    roi = result["settings"]["roi"]
    rois = copy.deepcopy(settings.get("rois", {}))
    if rois.get("Interface") != roi:
        raise ValueError("总界面选框已变化，请在新范围内重新做局部分解。")
    values = result["weights"][:, :, component]
    finite = np.isfinite(values)
    valid = values[finite]
    low, high = float(valid.min()), float(valid.max())
    if high-low <= 128*np.finfo(float).eps*max(abs(low), abs(high), 1e-30):
        raise ValueError("该组分没有可辨认的空间权重差，无法据此定位候选位错核。")
    threshold = low + (high-low)*percent/100
    selected = finite & ((values >= threshold) if direction == "high" else (values <= threshold))
    labels, _ = ndimage.label(selected, structure=np.ones((3, 3), dtype=int))
    counts = np.bincount(labels.ravel())
    ids = sorted((int(i) for i in np.flatnonzero(counts >= minimum) if i), key=lambda i: (-counts[i], i))
    boxes = ndimage.find_objects(labels)
    candidates = []
    for i in ids[:50]:
        ys, xs = boxes[i-1]
        candidates.append({"label": i, "pixel_count": int(counts[i]),
                           "roi": {"x_min": float(result["x_nm"][xs.start]), "x_max": float(result["x_nm"][xs.stop-1]),
                                   "y_min": float(result["y_nm"][ys.start]), "y_max": float(result["y_nm"][ys.stop-1])}})
    recipe = {"component": component, "threshold_percent": percent, "direction": direction,
              "min_pixels": minimum, "candidate": choice, "connectivity": 8, "normalization": "(weight-min)/(max-min)"}
    output = {"localization_id": result["result_id"], "settings": recipe, "candidates": candidates,
              "candidate_count": len(ids), "threshold_weight": threshold, "weight_range": [low, high],
              "selected": None, "apply_error": "当前阈值下没有满足像素数的连通区域。"}
    if not candidates:
        return output
    if choice >= len(candidates):
        raise ValueError("候选区序号已失效，请重新预览。")
    candidate = candidates[choice]
    chosen = labels == candidate["label"]
    bounding = candidate["roi"]
    rectangle = ((result["x_nm"] >= bounding["x_min"]) & (result["x_nm"] <= bounding["x_max"]))[None, :] & (
                 (result["y_nm"] >= bounding["y_min"]) & (result["y_nm"] <= bounding["y_max"]))[:, None]
    candidate = {**candidate, "rectangle_pixels": int(rectangle.sum()),
                 "extra_rectangle_pixels": int(rectangle.sum())-candidate["pixel_count"]}
    rois["Core"] = bounding
    apply_error = None
    try:
        region_masks(data, rois, core_enabled=True)
    except ValueError as error:
        apply_error = str(error)
    crop, _ = interface_crop(data, result["settings"].get("analysis_roi", roi))
    cfg = result["settings"]["decomposition"]
    energy, mask = select_energy(crop, cfg["fit_min"], cfg["fit_max"])
    cube = crop.intensity[:, :, mask]
    spectra = {}
    # The context window may include bulk pixels. Ordinary Interface is still the
    # user's original interface ROI, excluding the proposed core rectangle.
    interface = ((crop.x_nm >= roi["x_min"]) & (crop.x_nm <= roi["x_max"]))[None, :] & (
                 (crop.y_nm >= roi["y_min"]) & (crop.y_nm <= roi["y_max"]))[:, None]
    for name, region in (("Core", rectangle), ("Interface", interface & ~rectangle)):
        try:
            mean, count = mean_spectrum(cube, region)
            spectra[name] = {"spectrum": mean, "pixel_count": count}
        except ValueError:
            spectra[name] = None
    yi, xi = display_indices(result)
    # Aggregate occupancy so a single selected pixel remains visible in a reduced preview.
    display_mask = np.zeros((len(yi), len(xi)), dtype=bool)
    rows, columns = np.nonzero(chosen)
    display_mask[np.minimum(np.searchsorted(yi, rows), len(yi)-1),
                 np.minimum(np.searchsorted(xi, columns), len(xi)-1)] = True
    output.update(selected=candidate, apply_error=apply_error, preview_mask=display_mask,
                  energy_meV=energy, region_spectra=spectra)
    return output
