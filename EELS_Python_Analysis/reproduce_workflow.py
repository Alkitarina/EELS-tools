"""Replay the separate NMF/MCR-ALS interface workflow JSON with retained HDF5 data."""
import argparse
import json
from pathlib import Path
import warnings

from threadpoolctl import threadpool_limits

from eelsfit.io import load_eels_hdf5
from eelsfit.exporting import data_checksum, json_text
from eelsfit.multivariate import VERSION, decompose
from eelsfit.interface_workflow import build_profiles, fit_profile, fit_key
from eelsfit.interface_localization import localize_interface
from eelsfit.workflow_export import export_document, software_identity
from eelsfit.workflow_import import RESTORATION, restore_fit, saved_fits


def replay_workflow(path, data_path=None, *, allow_version_mismatch=False):
    document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if document.get("format") != VERSION:
        raise ValueError("不支持的界面工作流格式；PCA/高斯旧结果请使用 reproduce.py。")
    metadata = document["reproducibility"]
    software = software_identity()
    if metadata["software"] != software:
        if not allow_version_mismatch:
            raise ValueError("程序或环境版本不匹配；请恢复对应版本或显式 --allow-version-mismatch。")
        warnings.warn("版本不匹配，不能承诺精确复现。", RuntimeWarning)
    data = load_eels_hdf5(data_path or metadata["data"]["source"])
    if data_checksum(data) != metadata["data"]["checksum"]:
        raise ValueError("数值数据校验不匹配，拒绝复现已改变的数据。")
    steps = metadata["steps"]
    names = [step["stage"] for step in steps]
    if names != ["decomposition"] and not (names[:2] == ["decomposition", "profiles"] and all(name == "fit" for name in names[2:]) and len(names) <= 7):
        raise ValueError("工作流须先分解、建立体谱，再按区域拟合；最多保存四区及一个条带结果。")
    if len({step["result_id"] for step in steps}) != len(steps):
        raise ValueError("工作流结果 ID 重复。")
    results = {}
    with threadpool_limits(limits=1):
        for step in steps:
            stage, settings = step["stage"], step["settings"]
            if stage == "decomposition":
                result = decompose(data, settings)
            elif stage == "profiles":
                result = build_profiles(data, results["decomposition"], settings)
                result["decomposition_id"] = results["decomposition"]["result_id"]
            else:
                if step.get("profiles_id", results["profiles"]["result_id"]) != results["profiles"]["result_id"]:
                    raise ValueError("拟合步骤引用了不匹配的体谱。")
                if step.get("restoration") == RESTORATION:
                    result = restore_fit(results["profiles"], step, saved_fits(document)[step["result_id"]])
                    result["restoration"] = RESTORATION
                elif step.get("restoration") is not None:
                    raise ValueError("未知保存峰参数恢复规则。")
                else:
                    result = fit_profile(results["profiles"], settings)
                result["profiles_id"] = results["profiles"]["result_id"]
            if "import_origin" in step:
                result["import_origin"] = step["import_origin"]
            result["result_id"] = step["result_id"]
            if stage == "fit":
                key = fit_key(result)
                if step.get("target_key", key) != key or key in results.setdefault("fits", {}):
                    raise ValueError("区域拟合步骤重复或目标名称不匹配。")
                results["fits"][key] = result
            results[stage] = result
        local_recipe = metadata.get("localization")
        if local_recipe:
            if (local_recipe["decomposition_id"] != results["decomposition"]["result_id"]
                    or local_recipe["result_id"] in {step["result_id"] for step in steps}):
                raise ValueError("局部分解的父结果或结果 ID 不匹配。")
            local = localize_interface(data, results["decomposition"], local_recipe["settings"])
            local.update(result_id=local_recipe["result_id"], decomposition_id=local_recipe["decomposition_id"])
            results["localization"] = local
    return export_document(data, results, software)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-version-mismatch", action="store_true")
    args = parser.parse_args()
    output = args.output or args.result.with_name(args.result.stem + "_reproduced.json")
    if output.exists():
        parser.error("输出已存在，请指定新的 --output。")
    result = replay_workflow(args.result, args.data, allow_version_mismatch=args.allow_version_mismatch)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json_text(result) + "\n")
    print(output)


if __name__ == "__main__":
    main()
