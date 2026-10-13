"""Candidate peaks and two-stage / joint fits for signed interface residuals."""
import numpy as np
from scipy.integrate import trapezoid
from scipy.optimize import least_squares, lsq_linear, nnls
from scipy.signal import find_peaks, peak_widths


def peak_shapes(energy, centers, widths, shape="gaussian"):
    delta = (np.asarray(energy)[:, None] - np.asarray(centers)) / np.asarray(widths)
    if shape == "gaussian":
        return np.exp(-4 * np.log(2) * delta**2)
    if shape == "lorentzian":
        return 1 / (1 + 4 * delta**2)
    raise ValueError("线型必须为 Gaussian 或 Lorentzian。")


CANDIDATE_SCORE_METHOD = "zero_baseline_prominence_v1"


def candidates(energy, signal, count=3, prominence=.03, distance=3., polarity="positive", candidate_score=None):
    if int(count) != count or not 1 <= count <= 8 or not 0 <= prominence < 1 or distance <= 0:
        raise ValueError("候选峰数为 1–8，突出度为 [0,1)，峰距须为正数。")
    if polarity not in {"positive", "negative", "both"}:
        raise ValueError("候选峰方向须为正峰、负峰或正负峰。")
    if candidate_score not in (None, "prominence_v1", CANDIDATE_SCORE_METHOD):
        raise ValueError("未知候选峰评分规则。")
    values = np.asarray(signal)
    tolerance = 128 * np.finfo(float).eps * max(float(np.max(abs(values))), 1.)
    threshold = max(float(np.ptp(values)) * prominence, tolerance)
    found = []
    for sign in ([1, -1] if polarity == "both" else [1] if polarity == "positive" else [-1]):
        directed = sign * values
        indices, props = find_peaks(directed, prominence=threshold,
                                   distance=max(1, int(np.ceil(distance / np.median(np.diff(energy))))))
        for index, channel in enumerate(indices):
            if directed[channel] <= tolerance:
                continue
            # A shallow signed trough between positive shoulders must not borrow
            # their height. Use the same zero-referenced score for thresholding
            # and ranking; preserve the original prominence for audit/replay.
            score = min(float(props["prominences"][index]), float(directed[channel]))
            if candidate_score == CANDIDATE_SCORE_METHOD and score < threshold:
                continue
            _, heights, left, right = peak_widths(directed, [channel], rel_height=.5)
            bounds = np.interp([left[0], right[0]], np.arange(len(energy)), energy)
            found.append({"center_meV": float(energy[channel]), "height": float(values[channel]),
                          "sign": sign, "polarity": "positive" if sign > 0 else "negative",
                          "prominence": float(props["prominences"][index]),
                          "width_meV": float(bounds[1] - bounds[0]), "width_height": float(sign * heights[0]),
                          **({"selection_score": score, "score_rule": candidate_score}
                             if candidate_score == CANDIDATE_SCORE_METHOD else {})})
    output = []
    for peak in sorted(found, key=lambda p: p.get("selection_score", p["prominence"]), reverse=True):
        if all(abs(peak["center_meV"]-other["center_meV"]) >= distance for other in output):
            output.append(peak)
        if len(output) == count:
            break
    return sorted(output, key=lambda p: p["center_meV"])


AMPLITUDE_LIMIT_METHOD = "fixed_initial_local_extremum_v1"


def local_amplitude_constraint(energy, signal, centers, signs, settings, reference_kind):
    """Freeze each height cap from measured samples near its initial candidate.

    The neighbourhood is candidate +/- center_radius, intersected with the
    measured energy window. Negative peaks use the magnitude of the local
    trough. No smoothing, baseline shift, extrapolation or joint-fit feedback.
    Missing/null settings deliberately retain historical unbounded replay.
    """
    config = settings.get("amplitude_limit")
    if config is None:
        return None
    if not isinstance(config, dict) or config.get("method") != AMPLITUDE_LIMIT_METHOD:
        raise ValueError("未知峰高上限方法。")
    factor = config.get("factor")
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not np.isfinite(factor) or factor <= 0:
        raise ValueError("峰高上限倍数必须为有限正数。")
    radius = float(settings.get("center_radius", 3.))
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError("峰位搜索半径必须为有限正数。")
    energy, signal = np.asarray(energy), np.asarray(signal)
    centers, signs = np.asarray(centers), np.asarray(signs)
    if signs.shape != centers.shape or not np.isin(signs, [-1, 1]).all():
        raise ValueError("每个候选峰须指定 +1 或 -1 的方向。")
    neighbourhoods, locations, heights = [], [], []
    for center, sign in zip(centers, signs, strict=True):
        lo, hi = max(float(energy[0]), center-radius), min(float(energy[-1]), center+radius)
        indices = np.flatnonzero((energy >= lo) & (energy <= hi))
        if not len(indices):
            raise ValueError("候选峰邻域没有实际能量通道。")
        directed = sign * signal[indices]
        index = indices[int(np.argmax(directed))]
        height = float(sign * signal[index])
        if not np.isfinite(height) or height <= 0:
            raise ValueError("候选峰邻域没有同号实测信号，无法确定峰高上限。")
        neighbourhoods.append([lo, hi]); locations.append(float(energy[index])); heights.append(height)
    return {"method": AMPLITUDE_LIMIT_METHOD, "factor": float(factor), "reference_kind": reference_kind,
            "candidate_centers_meV": centers.copy(), "neighborhood_bounds_meV": np.asarray(neighbourhoods),
            "reference_extrema_meV": np.asarray(locations), "reference_abs_heights": np.asarray(heights),
            "amplitude_abs_upper": np.asarray(heights) * factor,
            "energy_window_meV": [float(energy[0]), float(energy[-1])]}


def line_fit(energy, measured, centers, *, shape="gaussian", fwhm_min=1., fwhm_max=15.,
             center_radius=3., bulk=None, max_nfev=1000, initial_model=None, peak_signs=None,
             amplitude_constraint=None):
    energy, measured, centers = np.asarray(energy), np.asarray(measured), np.asarray(centers, dtype=float)
    if (centers.ndim != 1 or not 1 <= len(centers) <= 8 or not np.isfinite(centers).all()
            or not np.all((centers > energy[0]) & (centers < energy[-1]))):
        raise ValueError("请指定能量窗内部的 1–8 个候选峰位。")
    if not np.isfinite([fwhm_min, fwhm_max, center_radius]).all() or not 0 < fwhm_min < fwhm_max or center_radius <= 0:
        raise ValueError("请输入有效的 FWHM 上下限和峰位搜索半径。")
    signs = np.ones(len(centers)) if peak_signs is None else np.asarray(peak_signs, dtype=float)
    if signs.shape != centers.shape or not np.isin(signs, [-1, 1]).all():
        raise ValueError("每个候选峰须指定 +1 或 -1 的方向。")
    order = np.argsort(centers, kind="stable")
    centers, signs = centers[order], signs[order]
    n = len(centers)
    constraint = None
    if amplitude_constraint is not None:
        constraint = dict(amplitude_constraint)
        for key in ("candidate_centers_meV", "neighborhood_bounds_meV", "reference_extrema_meV",
                    "reference_abs_heights", "amplitude_abs_upper"):
            values = np.asarray(constraint[key], dtype=float)
            if values.shape != ((n, 2) if key == "neighborhood_bounds_meV" else (n,)) or not np.isfinite(values).all():
                raise ValueError("峰高上限元数据与候选峰不匹配。")
            constraint[key] = values[order]
        if np.any(constraint["amplitude_abs_upper"] <= 0):
            raise ValueError("峰高上限必须为有限正数。")
    lower = np.maximum(energy[0] + 1e-8, centers - center_radius)
    upper = np.minimum(energy[-1] - 1e-8, centers + center_radius)
    # Keep peak identities from crossing while retaining the user-specified search radius.
    if n > 1:
        midpoints = (centers[:-1] + centers[1:]) / 2
        upper[:-1] = np.minimum(upper[:-1], midpoints - 1e-8)
        lower[1:] = np.maximum(lower[1:], midpoints + 1e-8)
    if np.any(lower >= upper):
        raise ValueError("候选峰过近或重复，无法设置独立的峰位边界。")
    bulk = np.empty((energy.size, 0)) if bulk is None else np.asarray(bulk)
    scale = max(float(np.max(abs(measured))), 1e-30)

    def evaluate(parameters):
        peaks = peak_shapes(energy, parameters[:n], parameters[n:], shape)
        # Signed peak bases permit depletion features while bulk coefficients stay nonnegative.
        basis = np.column_stack((bulk, peaks * signs))
        norms = np.maximum(np.linalg.norm(basis, axis=0), 1e-30)
        if constraint is None:
            coefficients, _ = nnls(basis / norms, measured / scale, maxiter=max(100, 10*basis.shape[1]))
        else:
            # Bound the individual signed peak magnitudes, not their cancellable sum.
            upper_coefficients = np.r_[np.full(bulk.shape[1], np.inf), constraint["amplitude_abs_upper"]]
            solved = lsq_linear(basis / norms, measured / scale,
                                bounds=(np.zeros(basis.shape[1]), upper_coefficients * norms / scale),
                                method="bvls", tol=1e-12, max_iter=max(100, 10*basis.shape[1]))
            if not solved.success:
                raise ValueError("带峰高上限的线性子问题未收敛。")
            coefficients = solved.x
        coefficients = coefficients * scale / norms
        return basis @ coefficients, coefficients, peaks, basis / norms

    def objective(parameters):
        return (evaluate(parameters)[0] - measured) / scale

    best = None
    starts = [np.r_[centers, np.full(n, fwhm_min + (fwhm_max - fwhm_min) * fraction)] for fraction in (.25, .65)]
    if initial_model is not None:
        starts.append(np.r_[initial_model["centers_meV"], initial_model["fwhm_meV"]])
    for start in starts:
        fit = least_squares(objective, start, bounds=(np.r_[lower, np.full(n, fwhm_min)],
                                                      np.r_[upper, np.full(n, fwhm_max)]),
                            max_nfev=max_nfev, ftol=1e-9, xtol=1e-9, gtol=1e-9)
        if best is None or fit.cost < best.cost:
            best = fit
    fitted, coefficients, peaks, normalized = evaluate(best.x)
    nb = bulk.shape[1]
    amplitudes = coefficients[nb:] * signs
    widths = best.x[n:]
    boundary = ((best.x[:n] - lower < 1e-4) | (upper - best.x[:n] < 1e-4)
                | (widths - fwhm_min < 1e-4) | (fwhm_max - widths < 1e-4)
                | (abs(amplitudes) < 1e-8 * scale))
    at_amplitude_limit = (np.isclose(abs(amplitudes), constraint["amplitude_abs_upper"], rtol=1e-6, atol=1e-10*scale)
                          if constraint is not None else np.zeros(n, dtype=bool))
    boundary |= at_amplitude_limit
    area_factor = np.sqrt(np.pi / (4 * np.log(2))) if shape == "gaussian" else np.pi / 2
    return {"shape": shape, "success": bool(best.success), "message": str(best.message),
            "centers_meV": best.x[:n], "fwhm_meV": widths, "amplitudes": amplitudes, "peak_signs": signs.astype(int),
            "areas": amplitudes * widths * area_factor, "bulk_weights": coefficients[:nb],
            "fitted": fitted, "peak_components": (peaks * amplitudes).T,
            "fit_residual": measured - fitted, "rmse": float(np.sqrt(np.mean((measured - fitted)**2))),
            "sse": float(np.sum((measured - fitted)**2)), "basis_condition": float(np.linalg.cond(normalized)),
            "nfev": int(best.nfev), "center_bounds": np.column_stack((lower, upper)),
            "fwhm_bounds": [fwhm_min, fwhm_max], "at_constraint_boundary": boundary, "uncertainty": None,
            **({"amplitude_constraint": constraint, "at_amplitude_limit": at_amplitude_limit} if constraint is not None else {})}


def two_reference_nnls(spectra, templates):
    """Exact two-column NNLS: compare interior, either boundary, and the origin."""
    y = np.atleast_2d(np.asarray(spectra, dtype=float))
    basis = np.asarray(templates, dtype=float).T
    if basis.shape != (y.shape[1], 2) or not np.isfinite(basis).all() or not np.isfinite(y).all():
        raise ValueError("扣谱需要两个与实测谱同能量轴的有限体谱模板。")
    norms = np.linalg.norm(basis, axis=0)
    if np.any(norms < np.finfo(float).tiny):
        raise ValueError("体谱模板为零。")
    a = basis / norms
    condition = float(np.linalg.cond(a))
    if not np.isfinite(condition) or condition > 1e8:
        raise ValueError("两个体谱模板近乎共线，无法分别确定 Si / Ge 权重。")
    gram, rhs = a.T @ a, y @ a
    unconstrained = np.linalg.solve(gram, rhs.T).T
    candidates_ = np.zeros((len(y), 4, 2))
    candidates_[:, 1, 0] = np.maximum(rhs[:, 0] / gram[0, 0], 0)
    candidates_[:, 2, 1] = np.maximum(rhs[:, 1] / gram[1, 1], 0)
    candidates_[:, 3] = unconstrained
    objective = np.einsum("nki,ij,nkj->nk", candidates_, gram, candidates_) - 2*np.einsum("nki,ni->nk", candidates_, rhs)
    objective[np.any(unconstrained < 0, axis=1), 3] = np.inf
    weights = candidates_[np.arange(len(y)), np.argmin(objective, axis=1)] / norms
    reconstructed = weights @ basis.T
    residual = y - reconstructed
    return weights, reconstructed, residual, condition


def residual_diagnostics(energy, measured, residual, templates, weights):
    norm = float(np.linalg.norm(residual))
    correlations = []
    derivatives = np.gradient(np.asarray(templates), energy, axis=1)
    for derivative in derivatives:
        correlations.append(float(residual @ derivative / max(norm*np.linalg.norm(derivative), 1e-30)))
    return {"rmse": float(np.sqrt(np.mean(residual**2))),
            "relative_residual_norm": norm / max(float(np.linalg.norm(measured)), 1e-30),
            "negative_fraction": float(np.mean(residual < 0)),
            "positive_area": float(trapezoid(np.maximum(residual, 0), energy)),
            "negative_area": float(trapezoid(np.minimum(residual, 0), energy)),
            "bulk_derivative_correlations": correlations,
            "weight_on_boundary": bool(np.any(np.asarray(weights) <= 1e-12)),
            "warnings": (["残差与体谱一阶导数明显相关；先检查能量漂移、峰位变化或模板不匹配。"]
                         if max(abs(np.array(correlations))) > .6 else [])}


def cross_validate(energy, measured, templates, settings):
    weights, bulk_sum, residual, condition = two_reference_nnls(measured, templates)
    weights, bulk_sum, residual = weights[0], bulk_sum[0], residual[0]
    # Missing polarity preserves historical positive-only recipes; new UI explicitly requests both.
    polarity = settings.get("polarity", "positive")
    detected = candidates(energy, residual, settings.get("peak_count", 3),
                          float(settings.get("prominence", .03)), float(settings.get("distance", 3.)), polarity,
                          settings.get("candidate_score"))
    if np.ptp(residual) <= 128 * np.finfo(float).eps * max(float(np.max(abs(measured))), 1.):
        detected = []
    manual = settings.get("centers")
    centers = manual or [peak["center_meV"] for peak in detected]
    if manual:
        if not np.isfinite(centers).all():
            raise ValueError("手动候选峰位必须为有限数值。")
        values = np.interp(centers, energy, residual)
        if polarity == "both" and np.any(abs(values) <= 128*np.finfo(float).eps*max(float(np.max(abs(measured))), 1.)):
            raise ValueError("手动峰位处残差接近零，无法判断方向；请调整峰位或选择仅正峰 / 仅负峰。")
        signs = np.where(values < 0, -1, 1) if polarity == "both" else np.full(len(centers), 1 if polarity == "positive" else -1)
    else:
        signs = [peak["sign"] for peak in detected]
    diagnostics = residual_diagnostics(energy, measured, residual, templates, weights)
    output = {"candidates": detected, "weights": weights, "bulk_sum": bulk_sum, "residual": residual,
              "diagnostics": diagnostics, "basis_condition": condition, "two_stage": None, "joint": None,
              "comparison": None, "measured": measured, "templates": templates, "energy_meV": energy}
    if not centers:
        diagnostics["warnings"].append("当前阈值和方向下没有可分辨残差峰，不强制生成峰模型。")
        return output
    kwargs = {"shape": settings.get("shape", "gaussian"), "center_radius": float(settings.get("center_radius", 3.)),
              "fwhm_min": float(settings.get("fwhm_min", 1.)), "fwhm_max": float(settings.get("fwhm_max", 15.)),
              "peak_signs": signs,
              "amplitude_constraint": local_amplitude_constraint(energy, residual, centers, signs, settings,
                                                                 "fixed_nnls_signed_residual")}
    staged = line_fit(energy, residual, centers, **kwargs)
    joint = line_fit(energy, measured, centers, bulk=np.asarray(templates).T, initial_model=staged, **kwargs)
    delta = joint["centers_meV"] - staged["centers_meV"]
    ratios = np.divide(joint["amplitudes"], staged["amplitudes"], out=np.full(len(centers), np.nan),
                       where=abs(staged["amplitudes"]) > 1e-12 * max(float(np.max(abs(measured))), 1e-30))
    if np.any(staged["amplitudes"] < 0):
        diagnostics["warnings"].append("负峰表示相对体谱模板的谱权重减弱；也可能来自峰位变化、展宽或模板不匹配，不能单凭负峰认定某振动成分消失。")
    if np.any(abs(delta) > 1) or joint["basis_condition"] > 1e4:
        diagnostics["warnings"].append("联合拟合峰位明显变化或基谱病态；该候选峰对模型选择敏感。")
    if not staged["success"] or not joint["success"]:
        diagnostics["warnings"].append("至少一个峰拟合达到优化上限；结果未收敛。")
    if np.any(staged["at_constraint_boundary"]) or np.any(joint["at_constraint_boundary"]):
        diagnostics["warnings"].append("至少一个候选峰的峰位、宽度或振幅接近约束边界，参数可能未被数据充分确定。")
    if np.any(staged.get("at_amplitude_limit", False)) or np.any(joint.get("at_amplitude_limit", False)):
        diagnostics["warnings"].append("至少一个峰达到局部实测残差峰高上限；联合与非联合拟合共用固定上限，不随体谱系数变化放宽。")
    output.update(two_stage=staged, joint=joint, comparison={"center_shift_meV": delta,
        "amplitude_ratio_joint_to_staged": ratios, "bulk_only_sse": float(np.sum(residual**2)),
        "two_stage_full_sse": staged["sse"], "joint_full_sse": joint["sse"],
        "note": "同一数据的模型交叉检查，非独立验证；增加峰参数通常会降低 SSE，不能单据此确认界面模。"})
    return output
