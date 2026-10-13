"""Separate compact, replayable contract for the multivariate interface workflow."""
import hashlib
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import platform

from .exporting import data_checksum, plain
from .multivariate import VERSION
from .interface_workflow import fit_key

SOURCES = ("eelsfit/multivariate.py", "eelsfit/interface_workflow.py", "eelsfit/interface_localization.py", "eelsfit/workflow_peaks.py",
           "eelsfit/workflow_jobs.py", "eelsfit/workflow_export.py", "eelsfit/workflow_import.py", "reproduce_workflow.py")


def software_identity():
    root = Path(__file__).resolve().parents[1]
    return {"algorithm_id": VERSION, "source_hash_scope": "sha256_utf8_lf_v1",
            "source_sha256": {name: hashlib.sha256((root/name).read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest()
                              for name in SOURCES}, "python": platform.python_version(),
            "packages": {name: version(name) for name in ("numpy", "scipy", "h5py", "threadpoolctl")}}


def fit_summary(fit):
    if fit is None:
        return None
    return {key: fit.get(key) for key in ("shape", "success", "message", "centers_meV", "fwhm_meV", "peak_signs",
            "amplitudes", "areas", "bulk_weights", "rmse", "sse", "basis_condition", "nfev",
            "center_bounds", "fwhm_bounds", "at_constraint_boundary", "uncertainty")} | {
            key: fit[key] for key in ("amplitude_constraint", "at_amplitude_limit") if key in fit}


def analysis_summary(fitted):
    common = {"result_id": fitted["result_id"], "analysis_kind": fitted.get("analysis_kind", "residual"),
              "selection": fitted["selection"], "candidates": fitted["candidates"], "diagnostics": fitted["diagnostics"]}
    if common["analysis_kind"] == "bulk_gaussian":
        return {**common, "bulk_fit": fit_summary(fitted["bulk_fit"])}
    return {**common, "weights": fitted["weights"], "comparison": fitted["comparison"],
            "two_stage": fit_summary(fitted["two_stage"]), "joint": fit_summary(fitted["joint"])}


def export_document(data, results, software):
    if "decomposition" not in results:
        raise ValueError("请先完成谱图分解。")
    decomp = results["decomposition"]
    profiles, fitted = results.get("profiles"), results.get("fit")
    if profiles and profiles["decomposition_id"] != decomp["result_id"]:
        raise ValueError("体谱结果与当前分解不匹配。")
    if fitted and (not profiles or fitted["profiles_id"] != profiles["result_id"]):
        raise ValueError("残差拟合与当前体谱不匹配。")
    fits = dict(results.get("fits", {}))
    if fitted:
        existing = fits.get(fit_key(fitted))
        if existing and existing["result_id"] != fitted["result_id"]:
            raise ValueError("同一区域存在不一致的当前拟合结果。")
        fits[fit_key(fitted)] = fitted
    for key, value in fits.items():
        if not profiles or value["profiles_id"] != profiles["result_id"] or key != fit_key(value):
            raise ValueError("区域拟合与所选体谱或区域名称不匹配。")
    if len({value["result_id"] for value in fits.values()}) != len(fits):
        raise ValueError("不同区域不能共用同一个拟合结果 ID。")
    summary = {"decomposition": decomp["summary"], "profiles": None, "fit": None, "regions": {}}
    localization = results.get("localization")
    if localization:
        if localization["decomposition_id"] != decomp["result_id"]:
            raise ValueError("局部分解与当前全图分解不匹配。")
        if localization["result_id"] in {value["result_id"] for value in (decomp, profiles, *fits.values()) if value}:
            raise ValueError("局部分解结果 ID 与主流程重复。")
        summary["localization"] = {"result_id": localization["result_id"], "roi": localization["settings"]["roi"],
                                   "summary": localization["summary"]}
        if "analysis_roi" in localization["settings"]:
            summary["localization"].update({key: localization["settings"][key] for key in
                ("analysis_roi", "requested_analysis_roi", "x_expansion_percent", "range_rule")})
    if profiles:
        summary["profiles"] = {**profiles["summary"], "template_metadata": profiles["template_metadata"],
            "strips": [{key: strip[key] for key in ("index", "row_start", "row_stop", "y_min", "y_max", "status", "reason", "core_overlap_rows", "measured_status") if key in strip}
                       for strip in profiles["strips"]],
            "alignment": {key: value for key, value in profiles["alignment"].items() if key != "centers_nm"},
            "regions": {name: {key: value for key, value in region.items()
                                if key not in {"measured", "bulk_sum", "residual"}} for name, region in profiles["regions"].items()}}
    if fitted:
        summary["fit"] = analysis_summary(fitted)
    for name in ("Si", "Ge", "Interface", "Core"):
        enabled = name != "Core" or bool(profiles and profiles["settings"].get("core_enabled"))
        result = fits.get(name)
        kind = "bulk_gaussian" if name in ("Si", "Ge") else "residual"
        model = None if result is None else result.get("bulk_fit") if result.get("analysis_kind") == "bulk_gaussian" else result.get("two_stage")
        status = ("disabled" if not enabled else "not_analyzed") if result is None else "no_peaks" if model is None else "completed" if model["success"] and (result.get("joint") is None or result["joint"]["success"]) else "not_converged"
        summary["regions"][name] = {"expected_analysis_kind": kind, "status": status,
            "roi": profiles["settings"]["rois"].get(name) if profiles else None,
            "result": analysis_summary(result) if result else None}
    if "strip" in fits:
        summary["strip_fit"] = analysis_summary(fits["strip"])
    ordered_fits = [value for value in fits.values() if fitted is None or value["result_id"] != fitted["result_id"]]
    if fitted:
        ordered_fits.append(fitted)
    steps = [{"stage": stage, "result_id": value["result_id"], "settings": value["settings"]}
             for stage, value in ((stage, results[stage]) for stage in ("decomposition", "profiles") if stage in results)]
    steps.extend({"stage": "fit", "result_id": value["result_id"], "profiles_id": value["profiles_id"],
                  "target_key": fit_key(value), "settings": value["settings"]} for value in ordered_fits)
    retained = {value["result_id"]: value for value in (decomp, profiles, *ordered_fits) if value}
    for step in steps:
        value = retained[step["result_id"]]
        for key in ("restoration", "import_origin"):
            if key in value:
                step[key] = value[key]
    return plain({"format": VERSION, "exported_utc": datetime.now(timezone.utc).isoformat(),
        "units": {"position": "nm", "energy": "meV", "intensity": "unchanged input units",
                  "decomposition_spectra": "unit area", "decomposition_weights": "input intensity * meV"},
        "results": summary, "reproducibility": {
            "data": {"source": str(data.source), "shape": list(data.shape), "axis_order": "y,x,energy",
                     "checksum": data_checksum(data),
                     **({"input_preparation": data.attributes["zest_import"]} if "zest_import" in data.attributes else {})}, "software": software,
            "notes": ["输入已降噪、扣背景；全部有限像素在统一能量窗内分解，不自动指定材料或位错。",
                      "NMF 的截断（如选用）仅作用于分解副本；体谱、条带与残差均来自原始已处理输入。",
                      "谱组分单位面积归一化，空间权重不代表元素原子分数；组分数、初始化及约束可能影响分解。",
                      "残差保留正负值；联合拟合是同一数据的模型敏感性检查，非独立物理验证。",
                      "ROI 初值仅初始化首个起点，谱形继续优化；复现从记录的 ROI 和输入重新提取均谱。",
                      "候选峰方向由实际 polarity 参数指定；负峰具有负振幅和负面积，体谱贡献系数仍非负。",
                      "残差候选 candidate_score=zero_baseline_prominence_v1 时，以 min(原突出度, 相对零线的绝对峰高) 同时筛选和排序；阈值仍为残差峰峰值乘 prominence。selection_score 保存实际评分，prominence 保留原突出度。缺失/null 或 prominence_v1 保留历史突出度规则。手动中心不参与自动候选筛选。",
                      "Si/Ge 体区直接对原始已处理均谱做高斯分峰；普通界面/位错核分析扣除体谱后的有符号残差。",
                      "results.regions 独立记录四区状态与结果；not_analyzed/disabled/no_peaks 不表示零峰参数。",
                      "局部分解按实际 analysis_roi 独立探索，缺少该字段时使用原总界面 roi；x_expansion_percent 表示左右各按原界面宽度扩展的百分比，100 表示总宽度 300%，越界时截到图像边缘。分析选框保持独立。阈值候选采用连通区域外接矩形，不自动证明位错；实际应用依据在 profiles.settings.evidence.partition_assistance。",
                      "条带按实际 stripe_y_scope 分段：interface_roi 仅总界面的 y 像素范围，full_y 为历史全图范围。每条覆盖全 x，配准时仅保留选中行的共有距离范围。位错核标记只看 y 像素行重叠。",
                      "strip_data_rule=independent_full_x_v1 时，measured_status 单独记录实测条带可用性，status 记录扣谱可用性；缺少体谱不会丢弃实测数据。缺少该规则字段的历史配方沿用旧耦合行为。",
                      "仅在已完成能窗内计算残差与 SSE/RMSE；高斯中心在窗内，尾部可跨边界，窗外不参与目标函数。areas 沿用完整线型解析面积。",
                      "可选 amplitude_limit 使用初始候选峰邻域的同号实测极值设置绝对峰高上限；Si/Ge 参考原均谱，Interface/Core 的两版共用固定 NNLS 残差上限。缺失/null 沿用历史不设上限行为。",
                      "此流程尚未计算峰参数不确定度；null 不是零误差。本 JSON 不包含全谱或空间数组。"],
            "steps": steps, **({"localization": {"result_id": localization["result_id"],
                "decomposition_id": localization["decomposition_id"], "settings": localization["settings"]}} if localization else {})}})
