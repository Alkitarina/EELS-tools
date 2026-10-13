"""Full-image NMF and nonnegative MCR-ALS for exploratory EELS unmixing."""
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.integrate import trapezoid

VERSION = "eels_interface_workflow_v1"
FEATURE_REVISION = "interface_y_stripes_v1"


@dataclass(frozen=True)
class DecompositionSettings:
    method: str = "mcr_als"
    components: int = 3
    fit_min: float = 10.
    fit_max: float = 80.
    init: str = "nndsvda"
    starts: int = 3
    seed: int = 20260915
    max_iter: int = 200
    tolerance: float = 1e-5
    negative_policy: str = "reject"
    preprocessing_confirmed: bool = False
    seed_rois: list = field(default_factory=list)
    seed_negative_policy: str = "reject"

    def validate(self, require_confirmation=True):
        if self.method not in {"nmf", "mcr_als"} or self.init not in {"nndsvda", "random", "roi"}:
            raise ValueError("请选择 NMF / MCR-ALS 和有效的初始化方式。")
        for value, lo, hi, name in ((self.components, 2, 8, "组分数"), (self.starts, 1, 12, "起点数"),
                                    (self.max_iter, 5, 2000, "迭代上限"), (self.seed, 0, 2**32-1, "种子")):
            if int(value) != value or not lo <= value <= hi:
                raise ValueError(f"{name} 必须为 {lo}–{hi} 的整数。")
        if not np.isfinite([self.fit_min, self.fit_max, self.tolerance]).all() or self.fit_min >= self.fit_max:
            raise ValueError("能量窗和收敛阈值必须是有效数值。")
        if not 1e-9 <= self.tolerance <= .01 or self.negative_policy not in {"reject", "clip_for_decomposition"}:
            raise ValueError("无效的收敛阈值或负值处理选项。")
        if self.seed_negative_policy not in {"reject", "clip_initial"}:
            raise ValueError("无效的 ROI 初值负值处理方式。")
        if self.init == "roi" and (not isinstance(self.seed_rois, list) or not 1 <= len(self.seed_rois) <= self.components):
            raise ValueError("请为至少一个组分指定 ROI；ROI 数不得超过组分数。")
        if require_confirmation and self.preprocessing_confirmed is not True:
            raise ValueError("请确认输入已降噪、扣背景，并核对可靠能量窗。此流程不自动重复预处理。")


def select_energy(data, lower, upper):
    energy = np.asarray(data.energy_meV)
    if not np.isfinite([lower, upper]).all() or lower >= upper:
        raise ValueError("请输入有效的统一能量窗。")
    if lower < energy[0] or upper > energy[-1]:
        raise ValueError("统一能量窗必须完全位于输入能量轴内；请明确调整边界。")
    mask = (energy >= lower) & (energy <= upper)
    if mask.sum() < 8:
        raise ValueError("统一能量窗至少需要 8 个通道。")
    return energy[mask], mask


def roi_initial_spectra(data, settings, *, require_confirmation=True):
    """Recompute editable H0 from declared ROIs; never replace or modify the input."""
    settings = settings if isinstance(settings, DecompositionSettings) else DecompositionSettings(**settings)
    settings.validate(require_confirmation=require_confirmation)
    if settings.init != "roi":
        raise ValueError("请先选择 ROI 平均谱初始化。")
    energy, mask = select_energy(data, settings.fit_min, settings.fit_max)
    cube = data.intensity[:, :, mask]
    finite = np.isfinite(cube).all(axis=2)
    used, spectra, means, metadata, warnings = set(), [], [], [], []
    for seed in settings.seed_rois:
        component = seed.get("component")
        if not isinstance(component, int) or isinstance(component, bool) or not 0 <= component < settings.components or component in used:
            raise ValueError("ROI 初值的组分索引必须唯一且位于组分数范围内。")
        used.add(component)
        roi = seed.get("roi", {})
        try:
            xl, xr, yl, yr = [float(roi[key]) for key in ("x_min", "x_max", "y_min", "y_max")]
        except (KeyError, TypeError, ValueError):
            raise ValueError("ROI 初值须包含四个有效空间坐标。") from None
        if (not np.isfinite([xl, xr, yl, yr]).all() or xl > xr or yl > yr
                or xl < data.x_nm[0] or xr > data.x_nm[-1] or yl < data.y_nm[0] or yr > data.y_nm[-1]):
            raise ValueError("ROI 初值坐标倒置或超出数据范围。")
        spatial = ((data.x_nm >= xl) & (data.x_nm <= xr))[None, :] & ((data.y_nm >= yl) & (data.y_nm <= yr))[:, None]
        selected = spatial & finite
        count = int(selected.sum())
        if count < 2:
            raise ValueError(f"初值 {component+1} 的 ROI 至少需要 2 个完整有限像素。")
        mean = cube[selected].mean(axis=0)
        scale = max(float(np.max(abs(mean))), np.finfo(float).tiny)
        significant = int((mean < -128*np.finfo(float).eps*scale).sum())
        if significant and settings.seed_negative_policy != "clip_initial":
            raise ValueError(f"初值 {component+1} 的 ROI 均谱含 {significant} 个负通道；请显式选择仅初值截零，原数据与残差保持原值。")
        initial = np.maximum(mean, 0)
        if not np.any(initial > 0):
            raise ValueError(f"初值 {component+1} 的 ROI 没有可用正信号。")
        # A tiny declared floor prevents multiplicative NMF from locking zero channels forever.
        floor = float(initial.max()) * 1e-8
        initial = np.maximum(initial, floor)
        spectra.append(initial); means.append(mean)
        item = {"component": component, "roi": {"x_min": xl, "x_max": xr, "y_min": yl, "y_max": yr},
                "pixel_count": count, "excluded_pixels": int(spatial.sum())-count,
                "negative_channels": int((mean < 0).sum()), "initial_floor": floor}
        metadata.append(item)
        if item["negative_channels"]:
            warnings.append(f"初值 {component+1}：仅初始化均谱的 {item['negative_channels']} 个负通道截零。")
    if len(spectra) > 1:
        unit = np.asarray(spectra) / np.linalg.norm(spectra, axis=1)[:, None]
        if np.max((unit @ unit.T)[np.triu_indices(len(unit), 1)]) > .995:
            warnings.append("至少两条 ROI 初值谱几乎相同；请检查选区和组分数，结果可能无法区分。")
    return {"energy_meV": energy, "mean_spectra": np.asarray(means), "initial_spectra": np.asarray(spectra),
            "metadata": metadata, "warnings": warnings}


def _initialize(matrix, rank, rng, mode):
    scale = max(float(np.mean(np.maximum(matrix, 0))), np.finfo(float).eps)
    if mode == "random":
        return (rng.uniform(.1, 1, (matrix.shape[0], rank)) * np.sqrt(scale / rank),
                rng.uniform(.1, 1, (rank, matrix.shape[1])) * np.sqrt(scale / rank))
    # NNDSVDa uses positive data only to obtain a starting estimate for signed MCR input.
    u, s, vt = np.linalg.svd(np.maximum(matrix, 0), full_matrices=False)
    w, h = np.zeros((matrix.shape[0], rank)), np.zeros((rank, matrix.shape[1]))
    w[:, 0], h[0] = np.sqrt(s[0]) * abs(u[:, 0]), np.sqrt(s[0]) * abs(vt[0])
    for j in range(1, rank):
        up, un = np.maximum(u[:, j], 0), np.maximum(-u[:, j], 0)
        vp, vn = np.maximum(vt[j], 0), np.maximum(-vt[j], 0)
        plus, minus = np.linalg.norm(up) * np.linalg.norm(vp), np.linalg.norm(un) * np.linalg.norm(vn)
        a, b, strength = (up, vp, plus) if plus >= minus else (un, vn, minus)
        if strength > np.finfo(float).eps:
            w[:, j] = np.sqrt(s[j] * strength) * a / np.linalg.norm(a)
            h[j] = np.sqrt(s[j] * strength) * b / np.linalg.norm(b)
    w[w == 0], h[h == 0] = scale, scale
    return w, h


def _nnls_rows(rhs, gram, initial, tolerance=1e-7, max_iter=500):
    """Warm-start batched coordinate descent, checking the NNLS KKT residual."""
    values = np.maximum(initial.copy(), 0)
    scale = max(float(np.max(abs(rhs))), np.finfo(float).tiny)
    for iteration in range(max_iter):
        for j in range(gram.shape[0]):
            if gram[j, j] > np.finfo(float).tiny:
                values[:, j] = np.maximum(0, values[:, j] + (rhs[:, j] - values @ gram[:, j]) / gram[j, j])
        if iteration % 5 == 0 or iteration == max_iter - 1:
            gradient = values @ gram - rhs
            projected = np.where(values > 0, gradient, np.minimum(gradient, 0))
            if np.max(abs(projected)) <= tolerance * scale:
                return values, True
    return values, False


def decompose(data, settings, progress=lambda *_: None):
    settings = settings if isinstance(settings, DecompositionSettings) else DecompositionSettings(**settings)
    settings.validate()
    energy, mask = select_energy(data, settings.fit_min, settings.fit_max)
    matrix = data.intensity[:, :, mask].reshape(-1, energy.size)
    valid = np.all(np.isfinite(matrix), axis=1)
    original = matrix[valid]
    x = original.copy()
    if x.shape[0] < settings.components or energy.size < settings.components:
        raise ValueError("有限像素数或能量通道数少于组分数。")
    amplitude = float(np.max(abs(x)))
    if amplitude <= np.finfo(float).tiny or not np.any(x > 0):
        raise ValueError("此能量窗没有可用于非负组分分解的正信号。")
    negative = x < 0
    negative_count = int(negative.sum())
    significant_negative = int((x < -128 * np.finfo(float).eps * amplitude).sum())
    warnings = []
    seeds = roi_initial_spectra(data, settings) if settings.init == "roi" else None
    if seeds:
        warnings.extend(seeds["warnings"])
        warnings.append("ROI 均谱仅用于首个起点，之后谱形自由更新；其余组分用 NNDSVDa，其余起点随机初始化。")
    if (~valid).any():
        warnings.append(f"排除了 {int((~valid).sum())} 个含非有限数值的像素；其权重图保留空白。")
    if settings.method == "nmf":
        if significant_negative and settings.negative_policy != "clip_for_decomposition":
            raise ValueError(f"NMF 输入含 {significant_negative} 个负值；请选择保留负值的 MCR-ALS，或显式启用仅分解时截断。")
        if negative_count:
            warnings.append(f"NMF 分解副本的 {negative_count} 个负值截为 0；后续体谱与残差仍使用原始已处理数据。")
        x = np.maximum(x, 0)
    elif negative_count:
        warnings.append("MCR-ALS 保留输入负值，约束谱组分及空间权重非负；负值进入最小二乘误差。")
    x = x / amplitude
    norm2 = float(np.sum(x*x))
    trials, best = [], None
    children = np.random.SeedSequence(settings.seed).spawn(settings.starts)
    for start, child in enumerate(children):
        rng = np.random.default_rng(child)
        mode = settings.init if start == 0 else "random"
        w, h = _initialize(x, settings.components, rng, "nndsvda" if mode == "roi" else mode)
        if mode == "roi":
            for initial, item in zip(seeds["initial_spectra"], seeds["metadata"]):
                h[item["component"]] = initial / np.linalg.norm(initial)
        previous = None
        converged = False
        inner_incomplete = 0
        history = []
        for iteration in range(1, settings.max_iter + 1):
            if settings.method == "nmf":
                h *= np.maximum(w.T @ x, 0) / np.maximum((w.T @ w) @ h, 1e-30)
                w *= np.maximum(x @ h.T, 0) / np.maximum(w @ (h @ h.T), 1e-30)
            else:
                w, ok_w = _nnls_rows(x @ h.T, h @ h.T, w)
                ht, ok_h = _nnls_rows(x.T @ w, w.T @ w, h.T)
                h = ht.T
                inner_incomplete += int(not (ok_w and ok_h))
            norms = np.linalg.norm(h, axis=1)
            if np.any(norms < 1e-20):
                # An unused component stays zero; no synthetic spectrum is introduced.
                norms = np.maximum(norms, 1e-20)
            h /= norms[:, None]
            w *= norms
            # Frobenius objective without allocating another full image matrix.
            sse = max(0., norm2 - 2 * float(np.sum((x @ h.T) * w)) + float(np.sum((w.T @ w) * (h @ h.T))))
            history.append(sse)
            progress((start + iteration / settings.max_iter) / settings.starts,
                     f"{settings.method}：起点 {start+1}/{settings.starts}，迭代 {iteration}")
            improvement = None if previous is None else (previous - sse) / max(previous, 1e-30)
            if iteration >= 5 and improvement is not None and -1e-9 <= improvement < settings.tolerance:
                converged = True
                break
            previous = sse
        rmse = float(np.sqrt(sse / x.size) * amplitude)
        trials.append({"start": start, "initialization": settings.init if start == 0 else "random",
                       "iterations": iteration, "converged": converged, "rmse": rmse,
                       "inexact_nnls_iterations": inner_incomplete})
        if best is None or rmse < best[0]:
            best = (rmse, w.copy(), h.copy() * amplitude, start, history)
    rmse, w, h, chosen, history = best
    if not trials[chosen]["converged"]:
        warnings.append("所选起点达到迭代上限，尚未满足收敛阈值；请检查增加迭代后的稳定性。")
    if trials[chosen]["inexact_nnls_iterations"]:
        warnings.append("部分内部 NNLS 达到迭代上限；可增加起点并检查重建误差及组分稳定性。")
    areas = trapezoid(h, energy, axis=1)
    if np.any(areas <= 1e-30):
        warnings.append("至少一个组分塌缩为零；不应解释其空间分布，建议检查更小组分数。")
    safe_areas = np.where(areas > 1e-30, areas, 1.)
    h /= safe_areas[:, None]
    w *= safe_areas
    centroids = np.sum(h * energy, axis=1) / np.maximum(np.sum(h, axis=1), 1e-30)
    order = np.argsort(centroids, kind="stable")
    h, w = h[order], w[:, order]
    maps = np.full((matrix.shape[0], settings.components), np.nan)
    maps[valid] = w
    reconstructed_error = np.full(matrix.shape[0], np.nan)
    valid_indices = np.flatnonzero(valid)
    for lo in range(0, x.shape[0], 2048):
        hi = min(lo + 2048, x.shape[0])
        reconstructed_error[valid_indices[lo:hi]] = np.sqrt(np.mean((original[lo:hi] - w[lo:hi] @ h)**2, axis=1))
    progress(1., "全像素分解完成")
    return {"settings": asdict(settings), "energy_meV": energy,
            "spectra": h, "weights": maps.reshape(*data.shape[:2], settings.components),
            "reconstruction_rmse_map": reconstructed_error.reshape(data.shape[:2]),
            "summary": {"method": settings.method, "valid_pixels": int(valid.sum()),
                        "excluded_pixels": int((~valid).sum()), "negative_values": negative_count,
                        "negative_fraction": negative_count / int(x.size), "selected_start": chosen,
                        "rmse_target": "nonnegative_decomposition_copy" if settings.method == "nmf" else "original_signed_input",
                        "original_input_rmse": float(np.sqrt(np.nanmean(reconstructed_error**2))),
                        "rmse": rmse, "trials": trials, "warnings": warnings,
                        "normalization": "unit_area_spectra; weights carry intensity*meV",
                        "component_order": "spectral_centroid_ascending",
                        "roi_initialization": None if seeds is None else {"rois": seeds["metadata"],
                            "unseeded_components": sorted(set(range(settings.components)) - {r["component"] for r in seeds["metadata"]}),
                            "relative_floor": 1e-8, "used_by_selected_start": chosen == 0, "fixed_spectra": False},
                        "objective_history": history}}
