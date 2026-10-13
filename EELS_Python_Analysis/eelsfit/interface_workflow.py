"""Spatially resolved bulk-template subtraction from preprocessed xyE data."""
import copy
import numpy as np

from .multivariate import select_energy
from .workflow_peaks import candidates, line_fit, two_reference_nnls, residual_diagnostics, cross_validate, local_amplitude_constraint

REGION_NAMES = ("Si", "Ge", "Interface", "Core")
STRIP_DATA_VERSION = "independent_full_x_v1"


def region_masks(data, rois, core_enabled=False):
    masks = {}
    names = REGION_NAMES if core_enabled else REGION_NAMES[:3]
    for name in names:
        roi = rois.get(name)
        if not isinstance(roi, dict):
            raise ValueError(f"缺少 {name} 选区。")
        bounds = [float(roi[key]) for key in ("x_min", "x_max", "y_min", "y_max")]
        if not np.isfinite(bounds).all():
            raise ValueError("选区坐标必须是有限数值。")
        xl, xr, yl, yr = bounds
        if (xl > xr or yl > yr or xl < data.x_nm[0] or xr > data.x_nm[-1]
                or yl < data.y_nm[0] or yr > data.y_nm[-1]):
            raise ValueError(f"{name} 选区坐标倒置或超出图像范围。")
        mask = ((data.y_nm >= yl) & (data.y_nm <= yr))[:, None] & ((data.x_nm >= xl) & (data.x_nm <= xr))[None, :]
        if not mask.any():
            raise ValueError(f"{name} 选区没有像素。")
        masks[name] = mask
    if np.any(masks["Si"] & masks["Ge"]):
        raise ValueError("Si 与 Ge 体区不得重叠。")
    for name in ("Interface", "Core"):
        if name in masks and np.any(masks[name] & (masks["Si"] | masks["Ge"])):
            raise ValueError(f"{name} 区域与体区共用像素，请调整分区。")
    if core_enabled:
        # Core may cross the interface box; ordinary interface excludes all core pixels.
        masks["Interface"] = masks["Interface"] & ~masks["Core"]
        if not masks["Interface"].any():
            raise ValueError("排除位错核后普通界面没有像素，请调整分区。")
    return masks


def mean_spectrum(cube, mask, minimum=1):
    selected = np.asarray(cube)[mask]
    selected = selected[np.all(np.isfinite(selected), axis=1)]
    if len(selected) < minimum:
        raise ValueError(f"选区至少需要 {minimum} 个所有能量通道均有限的像素。")
    return np.mean(selected, axis=0), len(selected)


def make_template(energy, raw, settings):
    if settings.get("kind", "mean") == "mean":
        return raw.copy(), {"kind": "mean", "rmse": 0., "peaks": []}
    if settings.get("kind") != "parametric":
        raise ValueError("体谱模板必须为区域均谱或参数化线型。")
    peaks = candidates(energy, raw, settings.get("peak_count", 2),
                       float(settings.get("prominence", .02)), float(settings.get("distance", 4.)))
    if not peaks:
        raise ValueError("体谱中没有满足阈值的可分辨峰，无法建立参数化模板；可改用均谱。")
    fitted = line_fit(energy, raw, [peak["center_meV"] for peak in peaks],
                      shape=settings.get("shape", "gaussian"),
                      fwhm_min=float(settings.get("fwhm_min", 1.)), fwhm_max=float(settings.get("fwhm_max", 20.)),
                      center_radius=float(settings.get("center_radius", 4.)))
    if not fitted["success"]:
        raise ValueError("参数化体谱拟合未收敛，请调整模板参数或使用实测均谱。")
    return fitted["fitted"], {"kind": "parametric", "shape": fitted["shape"], "rmse": fitted["rmse"],
                             "peaks": [{"center_meV": c, "fwhm_meV": w, "amplitude": a}
                                       for c, w, a in zip(fitted["centers_meV"], fitted["fwhm_meV"], fitted["amplitudes"])]}


def align_rows(data, cube, alignment, row_slice=None):
    y = data.y_nm if row_slice is None else data.y_nm[row_slice]
    cube = cube if row_slice is None else cube[row_slice]
    if not alignment or not alignment.get("enabled"):
        return cube, data.x_nm.copy(), {"enabled": False, "axis": "x_nm", "interpolation": None}
    anchors = np.asarray(alignment.get("anchors", []), dtype=float)
    if anchors.ndim != 2 or anchors.shape[1] != 2 or len(anchors) < 1 or not np.isfinite(anchors).all():
        raise ValueError("界面配准需要 y_nm, x0_nm 位置点；一行表示直界面，多行按 y 插值。")
    anchors = anchors[np.argsort(anchors[:, 0])]
    if len(anchors) > 1 and (np.any(np.diff(anchors[:, 0]) <= 0) or anchors[0, 0] > y[0] or anchors[-1, 0] < y[-1]):
        raise ValueError("多点界面线的 y 必须唯一、递增，并覆盖本次条带的 y 像素范围；不自动外推。")
    if np.any((anchors[:, 1] < data.x_nm[0]) | (anchors[:, 1] > data.x_nm[-1])):
        raise ValueError("界面中心位置超出视场。")
    centers = np.interp(y, anchors[:, 0], anchors[:, 1])
    step = float(np.median(np.diff(data.x_nm)))
    left, right = float(np.max(data.x_nm[0] - centers)), float(np.min(data.x_nm[-1] - centers))
    grid = np.arange(np.ceil(left / step), np.floor(right / step) + 1) * step
    if grid.size < 3:
        raise ValueError("配准后各行共有范围少于 3 列；请检查界面线。")
    aligned = np.empty((len(y), len(grid), cube.shape[2]))
    for row, center in enumerate(centers):
        query = grid + center
        positions = np.clip(np.searchsorted(data.x_nm, query, side="right") - 1, 0, len(data.x_nm)-2)
        fraction = (query - data.x_nm[positions]) / (data.x_nm[positions+1] - data.x_nm[positions])
        aligned[row] = cube[row, positions] * (1-fraction[:, None]) + cube[row, positions+1] * fraction[:, None]
        aligned[row, fraction <= 1e-12] = cube[row, positions[fraction <= 1e-12]]
        aligned[row, fraction >= 1-1e-12] = cube[row, positions[fraction >= 1-1e-12]+1]
    return aligned, grid, {"enabled": True, "axis": "distance_nm", "anchors": anchors,
                           "centers_nm": centers, "interpolation": "linear_x; common_support_only",
                           "original_columns": len(data.x_nm), "retained_columns": len(grid)}


def build_profiles(data, decomposition, settings, progress=lambda *_: None):
    settings = {"core_enabled": False, "stripe_rows": 4, "bulk_scope": "per_strip", "alignment": {"enabled": False},
                "stripe_y_scope": "full_y",  # Missing in historical recipes: retain their full-image stripes.
                **copy.deepcopy(settings)}
    strip_rule = settings.get("strip_data_rule", "legacy_coupled_v1")
    if strip_rule not in {"legacy_coupled_v1", STRIP_DATA_VERSION}:
        raise ValueError("未知条带数据生成规则。")
    independent = strip_rule == STRIP_DATA_VERSION
    settings["template"] = {"kind": "mean", "shape": "gaussian", "peak_count": 2, "prominence": .02,
                            "distance": 4., "center_radius": 4., "fwhm_min": 1., "fwhm_max": 20.,
                            **settings.get("template", {})}
    if settings.get("regions_confirmed") is not True:
        raise ValueError("请结合空间图和参考证据确认四类分区，再建立体谱。")
    cfg = decomposition["settings"]
    energy, mask = select_energy(data, cfg["fit_min"], cfg["fit_max"])
    cube = data.intensity[:, :, mask]
    masks = region_masks(data, settings.get("rois", {}), bool(settings.get("core_enabled")))
    stripe_rows = settings.get("stripe_rows", 4)
    if int(stripe_rows) != stripe_rows or not 1 <= stripe_rows <= cube.shape[0]:
        raise ValueError("条带高度须为 1 到总行数之间的整数（单位：像素行）。")
    stripe_rows = int(stripe_rows)
    settings["stripe_rows"] = stripe_rows
    y_scope = settings["stripe_y_scope"]
    if y_scope not in {"interface_roi", "full_y"}:
        raise ValueError("未知条带 y 范围规则。")
    interface = settings["rois"]["Interface"]
    # Use the total interface's y bounds, not the mask after excluding Core pixels.
    bounds = [interface["y_min"], interface["y_max"]] if y_scope == "interface_roi" else [data.y_nm[0], data.y_nm[-1]]
    selected_rows = np.flatnonzero((data.y_nm >= bounds[0]) & (data.y_nm <= bounds[1]))
    first, last = int(selected_rows[0]), int(selected_rows[-1])+1
    core = settings["rois"].get("Core") if settings["core_enabled"] else None
    scope = settings.get("bulk_scope", "per_strip")
    if scope not in {"per_strip", "global"}:
        raise ValueError("体谱范围须为每条带或全 y 共用。")
    template_settings = settings.get("template", {})
    if not isinstance(template_settings, dict):
        raise ValueError("无效的体谱模板设置。")

    def templates_for(row_mask):
        raw, templates, metadata = [], [], {}
        for name in ("Si", "Ge"):
            spectrum, count = mean_spectrum(cube, masks[name] & row_mask[:, None], minimum=2)
            template, summary = make_template(energy, spectrum, template_settings)
            raw.append(spectrum); templates.append(template)
            metadata[name] = {"pixel_count": count, **summary}
        return np.asarray(raw), np.asarray(templates), metadata

    template_errors = []
    if independent:
        # Bulk ROIs are spectral references only. Their y extent never clips xE data.
        raw_global = np.full((2, len(energy)), np.nan)
        templates_global = np.full_like(raw_global, np.nan)
        template_metadata = {}
        for k, name in enumerate(("Si", "Ge")):
            try:
                spectrum, count = mean_spectrum(cube, masks[name], minimum=2)
                raw_global[k] = spectrum
                template_metadata[name] = {"pixel_count": count, "status": "unavailable"}
                template, summary = make_template(energy, spectrum, template_settings)
                templates_global[k] = template
                template_metadata[name].update(status="available", **summary)
            except ValueError as error:
                reason = f"{name} 体谱：{error}"
                template_errors.append(reason)
                template_metadata.setdefault(name, {"pixel_count": int(np.all(np.isfinite(cube[masks[name]]), axis=1).sum())})
                template_metadata[name].update(status="unavailable", reason=reason)
    else:
        raw_global, templates_global, template_metadata = templates_for(np.ones(cube.shape[0], dtype=bool))
    regions = {}
    for name, roi_mask in masks.items():
        try:
            measured, count = mean_spectrum(cube, roi_mask)
        except ValueError as error:
            if not independent: raise
            regions[name] = {"measured": None, "pixel_count": 0, "status": "unavailable", "reason": str(error)}
            continue
        if name in ("Si", "Ge"):
            regions[name] = {"measured": measured, "pixel_count": count, "analysis_role": "bulk_input"}
            continue
        if template_errors:
            regions[name] = {"measured": measured, "pixel_count": count, "subtraction_status": "unavailable",
                             "subtraction_reason": "；".join(template_errors)}
            continue
        weights, mixture, residual, condition = two_reference_nnls(measured, templates_global)
        regions[name] = {"measured": measured, "pixel_count": count, "weights": weights[0],
                         "bulk_sum": mixture[0], "residual": residual[0], "basis_condition": condition,
                         "diagnostics": residual_diagnostics(energy, measured, residual[0], templates_global, weights[0])}
    aligned, axis, alignment = align_rows(data, cube, settings.get("alignment"), slice(first, last))
    strips = []
    total = int(np.ceil((last-first) / stripe_rows))
    for index, start in enumerate(range(first, last, stripe_rows)):
        stop = min(last, start + stripe_rows)
        row_mask = np.arange(cube.shape[0]) >= start
        row_mask[stop:] = False
        header = {"index": index, "row_start": start, "row_stop": stop,
                  "y_min": float(data.y_nm[start]), "y_max": float(data.y_nm[stop-1]),
                  "core_overlap_rows": int(np.count_nonzero((data.y_nm[start:stop] >= core["y_min"]) &
                                           (data.y_nm[start:stop] <= core["y_max"]))) if core else 0}
        strip = dict(header)
        try:
            if not independent:
                raw, templates, metadata = templates_for(row_mask) if scope == "per_strip" else (raw_global, templates_global, template_metadata)
            samples = aligned[start-first:stop-first]
            valid = np.all(np.isfinite(samples), axis=2)
            counts = valid.sum(axis=0)
            measured = np.sum(np.where(valid[:, :, None], samples, 0), axis=0) / np.maximum(counts[:, None], 1)
            measured[counts == 0] = np.nan
            if independent:
                strip.update(measured=measured, pixel_counts=counts, measured_status="available" if np.any(counts) else "unavailable")
            weights = np.full((len(axis), 2), np.nan)
            mixture, residual = np.full_like(measured, np.nan), np.full_like(measured, np.nan)
            selected = counts > 0
            if not selected.any():
                raise ValueError("条带没有有限谱线。")
            if independent:
                if scope == "global" and template_errors:
                    raise ValueError("；".join(template_errors))
                raw, templates, metadata = templates_for(row_mask) if scope == "per_strip" else (raw_global, templates_global, template_metadata)
            weights[selected], mixture[selected], residual[selected], condition = two_reference_nnls(measured[selected], templates)
            strips.append({**strip, "status": "available", "measured": measured, "pixel_counts": counts,
                           "raw_bulk": raw, "templates": templates, "template_metadata": metadata,
                           "weights": weights, "bulk_sum": mixture, "residual": residual, "basis_condition": condition,
                           "residual_rmse": np.sqrt(np.mean(residual**2, axis=1))})
        except ValueError as error:
            strips.append({**strip, "status": "unavailable", "reason": str(error)})
        progress((index+1)/total, f"条带 {index+1}/{total}：体谱模板与逐 x 非负扣谱")
    if not independent and not any(strip["status"] == "available" for strip in strips):
        raise ValueError("全部条带缺少有效体谱或共同数据；请调整体区 / 条带高度，或显式选择全 y 共用体谱。")
    return {"settings": settings, "energy_meV": energy, "x_axis_nm": axis, "alignment": alignment,
            "regions": regions, "raw_bulk": raw_global, "templates": templates_global,
            "template_metadata": template_metadata, "strips": strips,
            "summary": {"region_pixels": {key: int(value.sum()) for key, value in masks.items()},
                        "strip_domain": {"y_scope": y_scope, "requested_y_bounds_nm": list(map(float, bounds)),
                            "row_start": first, "row_stop": last, "row_count": last-first,
                            "y_min": float(data.y_nm[first]), "y_max": float(data.y_nm[last-1]),
                            "input_x_range_nm": [float(data.x_nm[0]), float(data.x_nm[-1])],
                            "x_scope": "aligned_common_support" if alignment["enabled"] else "full_scan",
                            "core_overlap_rule": "y_pixel_centers"},
                        "available_strips": sum(strip["status"] == "available" for strip in strips),
                        **({"measured_strips": sum(s["measured_status"] == "available" for s in strips),
                            "measured_missing_strips": [{k: s[k] for k in ("index", "y_min", "y_max", "reason")}
                                                        for s in strips if s["measured_status"] != "available"],
                            "template_errors": template_errors} if independent else {}),
                        "missing_strips": [{k: s[k] for k in ("index", "y_min", "y_max", "reason")}
                                           for s in strips if s["status"] != "available"],
                        "bulk_scope": scope, "signed_input_preserved": True,
                        "template_note": "体谱从 Si / Ge 选框均谱提取，NMF/MCR 组分不充当体谱。条带实测数据独立取自界面 y 范围的完整 x 轴；体谱不可用不丢弃实测条带。" if independent else
                                         "体谱从原输入均谱提取；NMF/MCR 组分不充当体谱。区域结果使用全 y 体谱，逐 x 结果使用所选条带体谱范围。"}}


def select_profile(profiles, selection, *, require_templates=True):
    if selection.get("kind", "region") == "region":
        name = selection.get("region", "Interface")
        if name not in profiles["regions"]:
            raise ValueError("指定区域不存在或位错核区域未启用。")
        region = profiles["regions"][name]
        if region.get("measured") is None:
            raise ValueError(region.get("reason", "所选区域没有有限谱线。"))
        if require_templates and not np.isfinite(profiles["templates"]).all():
            raise ValueError("参考体谱不可用：" + "；".join(profiles["summary"].get("template_errors", [])))
        return region["measured"], profiles["templates"], {"kind": "region", "region": name,
                                                                 "pixel_count": region["pixel_count"]}
    if selection.get("kind") != "strip":
        raise ValueError("请选择指定区域或条带 x 范围。")
    index = selection.get("strip", 0)
    if int(index) != index:
        raise ValueError("条带索引必须为整数。")
    index = int(index)
    if not 0 <= index < len(profiles["strips"]):
        raise ValueError("条带索引超出范围。")
    strip = profiles["strips"][index]
    if strip["status"] != "available":
        raise ValueError(strip["reason"])
    x = profiles["x_axis_nm"]
    lower, upper = float(selection.get("x_min", x[0])), float(selection.get("x_max", x[-1]))
    if not np.isfinite([lower, upper]).all() or lower > upper or lower < x[0] or upper > x[-1]:
        raise ValueError("x / 距离选择必须位于配准后的有效范围内。")
    chosen = (x >= lower) & (x <= upper) & (strip["pixel_counts"] > 0)
    if not chosen.any():
        raise ValueError("所选条带 x 范围内没有有限数据。")
    spectrum = np.average(strip["measured"][chosen], axis=0, weights=strip["pixel_counts"][chosen])
    return spectrum, strip["templates"], {"kind": "strip", "strip": index, "x_min": lower, "x_max": upper,
                                         "x_bins": int(chosen.sum()), "pixel_count": int(strip["pixel_counts"][chosen].sum())}


def fit_profile(profiles, settings, progress=lambda *_: None):
    settings = {"shape": "gaussian", "peak_count": 3, "prominence": .03, "distance": 3.,
                "center_radius": 3., "fwhm_min": 1., "fwhm_max": 15., "centers": None, "polarity": "positive",
                "analysis_kind": "residual",
                **copy.deepcopy(settings)}
    measured, templates, selection = select_profile(profiles, settings.get("selection", {}),
                                                   require_templates=settings["analysis_kind"] != "bulk_gaussian")
    if settings["analysis_kind"] == "bulk_gaussian":
        if selection["kind"] != "region" or selection["region"] not in ("Si", "Ge"):
            raise ValueError("体谱高斯分峰仅适用于 Si / Ge 体区。")
        if settings["shape"] != "gaussian" or settings["polarity"] != "positive":
            raise ValueError("Si / Ge 体谱使用正振幅 Gaussian 分峰。")
        progress(.1, "对体区原始已处理均谱做高斯分峰")
        energy = profiles["energy_meV"]
        detected = candidates(energy, measured, settings["peak_count"], settings["prominence"], settings["distance"])
        centers = settings["centers"] or [peak["center_meV"] for peak in detected]
        fitted = line_fit(energy, measured, centers, shape="gaussian", center_radius=settings["center_radius"],
                          fwhm_min=settings["fwhm_min"], fwhm_max=settings["fwhm_max"],
                          amplitude_constraint=local_amplitude_constraint(energy, measured, centers, np.ones(len(centers)),
                                                                          settings, "original_roi_mean")) if centers else None
        warnings = []
        if fitted is None:
            warnings.append("当前阈值下未检测到体谱峰；可调整阈值或手动指定峰位。")
        elif not fitted["success"]:
            warnings.append("体谱高斯分峰尚未收敛，请检查参数和迭代结果。")
        if fitted is not None and np.any(fitted["at_constraint_boundary"]):
            warnings.append("部分体谱峰参数接近约束边界，请检查峰数、峰位及宽度范围。")
        if fitted is not None and np.any(fitted.get("at_amplitude_limit", False)):
            warnings.append("部分体谱峰达到邻近实测均谱峰高上限。")
        settings["selection"] = selection
        progress(1., "体区高斯分峰完成")
        return {"analysis_kind": "bulk_gaussian", "selection": selection, "settings": settings,
                "energy_meV": energy, "measured": measured, "candidates": detected, "bulk_fit": fitted,
                "diagnostics": {"warnings": warnings}, "two_stage": None, "joint": None, "comparison": None}
    if settings["analysis_kind"] != "residual":
        raise ValueError("未知分析类型。")
    progress(.1, "检查体谱扣除后的有符号残差")
    result = cross_validate(profiles["energy_meV"], measured, templates, settings)
    settings["selection"] = selection
    result.update(settings=settings, selection=selection, analysis_kind="residual")
    progress(1., "残差线型与体谱 + 界面峰联合拟合完成")
    return result


def fit_key(result):
    selection = result["selection"]
    return selection["region"] if selection["kind"] == "region" else "strip"


def fit_regions(profiles, settings, progress=lambda *_: None):
    """Fit enabled regions with independent recipes; publish the batch atomically."""
    regions = settings.get("regions")
    names = [name for name in REGION_NAMES if name in profiles["regions"]]
    if not isinstance(regions, dict) or set(regions) != set(names):
        raise ValueError("批量拟合须为每个已启用区域提供独立参数。")
    results = {}
    for index, name in enumerate(names):
        cfg = copy.deepcopy(regions[name])
        cfg["selection"] = {"kind": "region", "region": name}
        expected = "bulk_gaussian" if name in ("Si", "Ge") else "residual"
        if cfg.get("analysis_kind") != expected:
            raise ValueError(f"{name} 区域必须使用 {expected} 分析。")
        results[name] = fit_profile(profiles, cfg, lambda p, m: progress((index+p)/len(names), f"{name}：{m}"))
    return {"fits": results}
