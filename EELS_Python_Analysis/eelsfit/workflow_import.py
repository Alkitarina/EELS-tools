"""Restore compact workflow exports without optimizing their saved peak models."""
import copy
import json
from pathlib import Path
import re
import uuid

import numpy as np

from .exporting import data_checksum
from .io import load_eels_hdf5
from .multivariate import VERSION, decompose
from .interface_workflow import build_profiles, fit_key, select_profile
from .interface_localization import localize_interface
from .workflow_export import software_identity
from .workflow_peaks import peak_shapes

IMPORT_VERSION = "saved_workflow_json_v1"
RESTORATION = "saved_parameters_v1"
MAX_JSON_BYTES = 8 * 1024 * 1024


def read_document(contents):
    if len(contents) > MAX_JSON_BYTES:
        raise ValueError("流程 JSON 超过 8 MB；请选择默认导出的紧凑流程文件。")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 字段重复：{key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"JSON 不允许非有限数值：{value}")

    try:
        document = json.loads(contents.decode("utf-8-sig"), object_pairs_hook=unique,
                              parse_constant=invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("无法读取流程 JSON，请选择 UTF-8 编码的完整导出文件。") from error
    validate_document(document)
    return document


def saved_fits(document):
    """Collect old single-fit and new region exports, checking repeated aliases."""
    summary = document["results"]
    sources = [summary.get("fit"), summary.get("strip_fit")]
    for name, region in summary.get("regions", {}).items():
        result = region.get("result")
        if result is not None and fit_key(result) != name:
            raise ValueError("JSON 区域名称与拟合目标不匹配。")
        sources.append(result)
    by_id = {}
    for result in sources:
        if result is None:
            continue
        identifier = result["result_id"]
        if identifier in by_id and by_id[identifier] != result:
            raise ValueError("JSON 中相同结果 ID 的峰参数不一致。")
        by_id[identifier] = result
    return by_id


def validate_document(document):
    if not isinstance(document, dict) or document.get("format") != VERSION:
        raise ValueError("请选择 eels_interface_workflow_v1 流程 JSON（如 try 2-7）；不支持旧 PCA 结果或绘图清单。")
    try:
        metadata, summary = document["reproducibility"], document["results"]
        if not isinstance(metadata, dict) or not isinstance(summary, dict):
            raise ValueError("流程参数和结果必须为对象。")
        checksum = metadata["data"]["checksum"]
        if (checksum.get("algorithm") != "sha256" or checksum.get("scope") != "eels_numeric_arrays_v1"
                or not re.fullmatch(r"[0-9a-f]{64}", checksum.get("value", ""))):
            raise ValueError("流程 JSON 缺少有效的数值数据校验信息。")
        if metadata["software"]["algorithm_id"] != VERSION:
            raise ValueError("流程算法标识不匹配。")
        steps = metadata["steps"]
        names = [step["stage"] for step in steps]
        if names != ["decomposition"] and not (names[:2] == ["decomposition", "profiles"]
                and all(name == "fit" for name in names[2:]) and len(names) <= 7):
            raise ValueError("流程顺序必须为分解、体谱、最多四区及一个条带拟合。")
        identifiers = [step["result_id"] for step in steps]
        if any(not isinstance(key, str) or not key for key in identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError("流程结果 ID 为空或重复。")
        fits = saved_fits(document)
        targets = set()
        fit_ids = set()
        for step in steps:
            if not isinstance(step["settings"], dict):
                raise ValueError("流程步骤参数必须为对象。")
            if step["stage"] == "fit":
                fit_ids.add(step["result_id"])
                result = fits[step["result_id"]]
                target = fit_key(result)
                selection = step["settings"].get("selection", {"kind": "region", "region": "Interface"})
                if (target not in {"Si", "Ge", "Interface", "Core", "strip"} or target in targets
                        or step.get("target_key", target) != target
                        or step.get("profiles_id", identifiers[1]) != identifiers[1]
                        or selection.get("kind", "region") != result["selection"]["kind"]
                        or any(selection.get(key) != result["selection"].get(key)
                               for key in (("strip", "x_min", "x_max") if target == "strip" else ("region",)))
                        or step["settings"].get("analysis_kind", "residual") != result.get("analysis_kind", "residual")):
                    raise ValueError("拟合步骤的区域、父结果或分析类型不匹配。")
                targets.add(target)
        if set(fits) != fit_ids:
            raise ValueError("峰结果与流程拟合步骤未一一对应。")
        if bool(summary.get("profiles")) != ("profiles" in names) or not summary.get("decomposition"):
            raise ValueError("JSON 缺少分解或体谱结果。")
        local = metadata.get("localization")
        if local and (local["decomposition_id"] != identifiers[0] or local["result_id"] in identifiers
                      or not isinstance(local["settings"], dict)):
            raise ValueError("局部分解的父结果或 ID 不匹配。")
        # Detect overflow such as 1e999 as well as NaN/Infinity before doing work.
        json.dumps(document, allow_nan=False)
    except (KeyError, TypeError, AttributeError, IndexError, RecursionError) as error:
        raise ValueError("流程 JSON 的结果或参数结构不完整，请重新选择完整导出文件。") from error
    return document


def _vector(value, length=None, *, positive=False):
    vector = np.asarray(value, dtype=float)
    if (vector.ndim != 1 or not np.isfinite(vector).all()
            or (length is not None and len(vector) != length) or (positive and np.any(vector <= 0))):
        raise ValueError("保存的峰参数长度不匹配或含无效数值。")
    return vector


def _restore_model(saved, energy, measured, templates=None):
    if saved is None:
        return None
    model = copy.deepcopy(saved)
    if not isinstance(model.get("success"), bool) or "uncertainty" not in model:
        raise ValueError("保存的拟合状态或不确定度字段缺失。")
    centers = _vector(model["centers_meV"])
    if not 1 <= len(centers) <= 8 or np.any((centers < energy[0]) | (centers > energy[-1])):
        raise ValueError("保存的峰位超出实际能窗或峰数无效。")
    widths = _vector(model["fwhm_meV"], len(centers), positive=True)
    amplitudes = _vector(model["amplitudes"], len(centers))
    _vector(model["areas"], len(centers))
    if len(model["at_constraint_boundary"]) != len(centers):
        raise ValueError("保存的峰边界标记长度不匹配。")
    if "peak_signs" in model and model["peak_signs"] is not None:
        signs = _vector(model["peak_signs"], len(centers))
        if np.any(~np.isin(signs, [-1, 1])) or np.any(signs * amplitudes < 0):
            raise ValueError("保存的峰方向与振幅不匹配。")
    if model.get("amplitude_constraint") is not None:
        _vector(model["amplitude_constraint"]["amplitude_abs_upper"], len(centers))
    weights = _vector(model["bulk_weights"], 0 if templates is None else 2)
    if np.any(weights < 0):
        raise ValueError("保存的体谱系数不能为负。")
    components = (peak_shapes(energy, centers, widths, model["shape"]) * amplitudes).T
    fitted = components.sum(axis=0)
    if templates is not None:
        fitted += weights @ templates
    model.update(peak_components=components, fitted=fitted, fit_residual=measured-fitted)
    return model


def restore_fit(profiles, step, saved):
    """Evaluate the saved Gaussian/Lorentzian model; never call peak optimization."""
    settings = copy.deepcopy(step["settings"])
    kind = saved.get("analysis_kind", "residual")
    if kind not in {"bulk_gaussian", "residual"}:
        raise ValueError("不支持的已保存拟合类型。")
    measured, templates, selection = select_profile(profiles, settings.get("selection", {}),
                                                    require_templates=kind != "bulk_gaussian")
    if kind == "bulk_gaussian" and (selection["kind"] != "region" or selection["region"] not in {"Si", "Ge"}):
        raise ValueError("体区高斯拟合仅支持 Si / Ge。")
    result = copy.deepcopy(saved)
    if not isinstance(result.get("candidates"), list) or not isinstance(result.get("diagnostics"), dict):
        raise ValueError("保存的候选峰或诊断信息缺失。")
    if not isinstance(result["diagnostics"].get("warnings"), list):
        raise ValueError("保存的诊断提示格式不完整。")
    energy = profiles["energy_meV"]
    result.update(analysis_kind=kind, settings=settings, energy_meV=energy, measured=measured,
                  profiles_id=profiles["result_id"])
    if kind == "bulk_gaussian":
        result.update(bulk_fit=_restore_model(saved["bulk_fit"], energy, measured),
                      two_stage=None, joint=None, comparison=None)
    else:
        weights = _vector(saved["weights"], 2)
        if np.any(weights < 0):
            raise ValueError("保存的体谱系数不能为负。")
        bulk_sum = weights @ templates
        residual = measured - bulk_sum
        staged = _restore_model(saved["two_stage"], energy, residual)
        joint = _restore_model(saved["joint"], energy, measured, templates)
        if (staged is None) != (joint is None) or (staged is not None and
                len(staged["centers_meV"]) != len(joint["centers_meV"])):
            raise ValueError("联合与非联合拟合峰数不匹配。")
        result.update(templates=templates, bulk_sum=bulk_sum, residual=residual, two_stage=staged, joint=joint)
        if not isinstance(result["diagnostics"].get("bulk_derivative_correlations"), list):
            raise ValueError("保存的残差诊断信息缺失。")
        if staged is not None:
            comparison = result["comparison"]
            if not isinstance(comparison, dict) or any(len(comparison[key]) != len(staged["centers_meV"])
                    for key in ("center_shift_meV", "amplitude_ratio_joint_to_staged")):
                raise ValueError("保存的联合拟合比较信息缺失或峰数不匹配。")
    return result


def restore_workflow(data, document, data_path=None, allow_version_mismatch=False, filename="", progress=lambda *_: None):
    validate_document(document)
    metadata = document["reproducibility"]
    current_software = software_identity()
    mismatch = metadata["software"] != current_software
    if mismatch and not allow_version_mismatch:
        raise ValueError("程序版本与 JSON 不同。如需继续，请勾选“允许跨版本重建谱图”；保存的峰参数不会重拟合。")
    expected = metadata["data"]["checksum"]
    progress(.01, "检查对应 H5 数据与数值校验信息")
    same_data = data is not None and not data_path and data_checksum(data) == expected
    if not same_data:
        source = Path(data_path or metadata["data"].get("source", ""))
        if not source.is_absolute() or not source.is_file() or source.suffix.lower() not in {".h5", ".hdf5"}:
            raise ValueError("找不到匹配的 H5 数据；请先导入对应 H5，或在流程导入窗口填写移动后的 H5 绝对路径。")
        candidate = load_eels_hdf5(source)
        if data_checksum(candidate) != expected:
            raise ValueError("H5 数值数据校验不匹配，拒绝导入；当前数据和结果保持不变。")
        data = candidate
    if list(data.shape) != metadata["data"]["shape"]:
        raise ValueError("H5 数据形状与流程记录不匹配。")
    results, fits = {}, {}
    summaries = saved_fits(document)
    steps = metadata["steps"]
    for index, step in enumerate(steps):
        stage = step["stage"]
        report = lambda fraction, message: progress(.05 + .8*(index+fraction)/len(steps), message)
        if stage == "decomposition":
            result = decompose(data, step["settings"], progress=report)
        elif stage == "profiles":
            result = build_profiles(data, results["decomposition"], step["settings"], progress=report)
            result["decomposition_id"] = results["decomposition"]["result_id"]
        else:
            result = restore_fit(results["profiles"], step, summaries[step["result_id"]])
            result["restoration"] = RESTORATION
            fits[fit_key(result)] = result
        result["result_id"] = uuid.uuid4().hex
        result["import_origin"] = {"filename": filename, "exported_utc": document.get("exported_utc"),
            "result_id": step["result_id"], "software": metadata["software"],
            "method": "saved_peak_parameters" if stage == "fit" else "rebuilt_from_recipe"}
        results[stage] = result
    local = metadata.get("localization")
    if local:
        rebuilt = localize_interface(data, results["decomposition"], local["settings"],
                                     progress=lambda p, m: progress(.85+.14*p, m))
        rebuilt.update(result_id=uuid.uuid4().hex, decomposition_id=results["decomposition"]["result_id"])
        results["localization"] = rebuilt
    note = "已导入流程：选区及参数已恢复，保存的峰参数未重拟合；谱图已按记录参数重建。"
    if mismatch:
        note += " 跨版本重建，缺失的谱图数组可能与原版本不同。"
    return {"data": data, "same_data": same_data, "results": results, "fits": fits, "message": note}
