"""Flask endpoints for the separate multivariate analysis workspace."""
import io

import numpy as np
from scipy.integrate import trapezoid
from flask import Blueprint, current_app, jsonify, request, send_file

from .exporting import json_text, plain
from .interface_workflow import region_masks, mean_spectrum, select_profile, STRIP_DATA_VERSION
from .interface_localization import local_view, core_proposal
from .multivariate import VERSION, select_energy, roi_initial_spectra
from .workflow_export import export_document, software_identity
from .workflow_peaks import two_reference_nnls, residual_diagnostics
from .workflow_import import MAX_JSON_BYTES, read_document


def register_workflow(app, service, manager):
    blueprint = Blueprint("interface_workflow", __name__, url_prefix="/api/workflow")
    software = software_identity()

    @blueprint.before_request
    def validate_dataset():
        manager.synchronize()
        expected = request.headers.get("X-EELS-Dataset")
        if expected and expected != service.dataset_id and not request.path.startswith("/api/workflow/jobs/"):
            return jsonify(error="数据已切换，请刷新当前数据状态。"), 409

    @blueprint.errorhandler(ValueError)
    @blueprint.errorhandler(KeyError)
    @blueprint.errorhandler(TypeError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @blueprint.post("/import")
    def import_workflow():
        upload = request.files.get("file")
        if upload is None or not (upload.filename or "").lower().endswith(".json"):
            raise ValueError("请选择流程 JSON 文件。")
        document = read_document(upload.read(MAX_JSON_BYTES + 1))
        if document["reproducibility"]["software"] != software and request.form.get("allow_version_mismatch") != "true":
            raise ValueError("程序版本与 JSON 不同。如需继续，请勾选“允许跨版本重建谱图”；保存的峰参数不会重拟合。")
        timeout = float(request.form.get("timeout_seconds", 900))
        if not 5 <= timeout <= 900:
            raise ValueError("导入时间限制必须为 5–900 秒。")
        payload = {"stage": "import", "document": document,
                   "filename": (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1],
                   "data_path": request.form.get("data_path", "").strip().strip('"') or None,
                   "allow_version_mismatch": request.form.get("allow_version_mismatch") == "true"}
        return jsonify(manager.start(payload, min(timeout, current_app.config.get("WORKFLOW_MAX_SECONDS", 900)))), 202

    @blueprint.post("/run")
    def start():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ValueError("请输入工作流参数。")
        timeout = float(payload.get("timeout_seconds", 180))
        if not 5 <= timeout <= 900:
            raise ValueError("计算时间限制必须为 5–900 秒。")
        timeout = min(timeout, current_app.config.get("WORKFLOW_MAX_SECONDS", 900))
        if payload.get("stage") == "profiles":
            settings = payload.get("settings", {})
            if not isinstance(settings, dict):
                raise ValueError("步骤参数必须为对象。")
            payload = {**payload, "settings": {"stripe_y_scope": "interface_roi", "strip_data_rule": STRIP_DATA_VERSION,
                                               "bulk_scope": "global", **settings}}
        elif payload.get("stage") == "localization":
            settings = payload.get("settings", {})
            if not isinstance(settings, dict):
                raise ValueError("步骤参数必须为对象。")
            payload = {**payload, "settings": {"x_expansion_percent": 100, **settings}}
        return jsonify(manager.start(payload, timeout)), 202

    @blueprint.get("/state")
    def state():
        active = next((manager.status(key) for key, job in manager.jobs.items()
                       if job["status"] in {"queued", "running"}), None)
        return jsonify(dataset_id=service.dataset_id, result_ids={key: value["result_id"]
                       for key, value in manager.results.items()}, active_job=active,
                       fit_result_ids={key: value["result_id"] for key, value in manager.fits.items()})

    @blueprint.get("/jobs/<job_id>")
    def status(job_id):
        return jsonify(manager.status(job_id))

    @blueprint.post("/jobs/<job_id>/cancel")
    def cancel(job_id):
        return jsonify(manager.cancel(job_id))

    @blueprint.get("/decomposition")
    def decomposition():
        result = manager.require("decomposition", request.args.get("result_id"))
        data = service.data
        yi = np.unique(np.r_[np.arange(0, data.shape[0], max(1, int(np.ceil(data.shape[0]/180)))), data.shape[0]-1])
        xi = np.unique(np.r_[np.arange(0, data.shape[1], max(1, int(np.ceil(data.shape[1]/220)))), data.shape[1]-1])
        cfg = result["settings"]
        energy, mask = select_energy(data, cfg["fit_min"], cfg["fit_max"])
        integral = trapezoid(data.intensity[:, :, mask], energy, axis=2)
        views = {"integral": integral[np.ix_(yi, xi)],
                 "reconstruction_error": result["reconstruction_rmse_map"][np.ix_(yi, xi)]}
        if data.total_counts is not None:
            views["total_counts"] = data.total_counts[np.ix_(yi, xi)]
        for index in range(result["spectra"].shape[0]):
            views[f"component_{index}"] = result["weights"][np.ix_(yi, xi, [index])][:, :, 0]
        return jsonify(plain({"result_id": result["result_id"], "settings": cfg, "summary": result["summary"],
                              "energy_meV": energy, "spectra": result["spectra"], "x_nm": data.x_nm[xi],
                              "pixel_x_nm": data.x_nm, "pixel_y_nm": data.y_nm,
                              "y_nm": data.y_nm[yi], "maps": views, "display_downsampled": len(xi)*len(yi) < data.shape[0]*data.shape[1]}))

    @blueprint.get("/initialization-map")
    def initialization_map():
        data = service.data
        energy, mask = select_energy(data, float(request.args["fit_min"]), float(request.args["fit_max"]))
        yi = np.unique(np.r_[np.arange(0, data.shape[0], max(1, int(np.ceil(data.shape[0]/180)))), data.shape[0]-1])
        xi = np.unique(np.r_[np.arange(0, data.shape[1], max(1, int(np.ceil(data.shape[1]/220)))), data.shape[1]-1])
        integral = trapezoid(data.intensity[np.ix_(yi, xi, np.flatnonzero(mask))], energy, axis=2)
        maps = {"integral": integral}
        if data.total_counts is not None:
            maps["total_counts"] = data.total_counts[np.ix_(yi, xi)]
        return jsonify(plain({"x_nm": data.x_nm[xi], "y_nm": data.y_nm[yi], "pixel_x_nm": data.x_nm,
                              "pixel_y_nm": data.y_nm, "maps": maps, "energy_bounds": [energy[0], energy[-1]]}))

    @blueprint.get("/localization")
    def localization():
        return jsonify(plain(local_view(manager.require("localization", request.args.get("result_id")))))

    @blueprint.post("/core-proposal")
    def propose_core():
        payload = request.get_json()
        result = manager.require("localization", payload.get("localization_id"))
        return jsonify(plain(core_proposal(service.data, result, payload.get("settings", {}))))

    @blueprint.post("/initialization-preview")
    def initialization_preview():
        # Inspection before running does not assert that preprocessing has been confirmed.
        # The actual job still requires confirmation and recomputes H0 from source data.
        return jsonify(plain(roi_initial_spectra(service.data, request.get_json(), require_confirmation=False)))

    @blueprint.post("/roi-preview")
    def roi_preview():
        payload = request.get_json()
        result = manager.require("decomposition", payload.get("decomposition_id"))
        cfg = result["settings"]
        energy, mask = select_energy(service.data, cfg["fit_min"], cfg["fit_max"])
        masks = region_masks(service.data, payload["rois"], payload.get("core_enabled", False))
        output = {}
        for name, spatial in masks.items():
            spectrum, count = mean_spectrum(service.data.intensity[:, :, mask], spatial)
            output[name] = {"spectrum": spectrum, "pixel_count": count}
        return jsonify(plain({"energy_meV": energy, "regions": output}))

    @blueprint.get("/profiles")
    def profiles():
        result = manager.require("profiles", request.args.get("result_id"))
        output = {key: result[key] for key in ("result_id", "decomposition_id", "settings", "energy_meV", "x_axis_nm",
                                             "alignment", "regions", "raw_bulk", "templates", "template_metadata", "summary")}
        output["strips"] = [{key: value for key, value in strip.items() if key in {
            "index", "status", "row_start", "row_stop", "y_min", "y_max", "reason", "residual_rmse",
            "weights", "basis_condition", "template_metadata", "core_overlap_rows", "measured_status"}} for strip in result["strips"]]
        return jsonify(plain(output))

    @blueprint.get("/strip")
    def strip():
        result = manager.require("profiles", request.args.get("result_id"))
        index = int(request.args.get("index", 0))
        if not 0 <= index < len(result["strips"]):
            raise ValueError("条带索引超出范围。")
        source = result["strips"][index]
        if source.get("measured_status", source["status"]) != "available":
            return jsonify(plain(source))
        xs = np.unique(np.r_[np.arange(0, len(result["x_axis_nm"]), max(1, int(np.ceil(len(result["x_axis_nm"])/220)))), len(result["x_axis_nm"])-1])
        es = np.unique(np.r_[np.arange(0, len(result["energy_meV"]), max(1, int(np.ceil(len(result["energy_meV"])/300)))), len(result["energy_meV"])-1])
        return jsonify(plain({"index": index, "status": source["status"], "measured_status": "available",
                              "reason": source.get("reason"), "x_axis_nm": result["x_axis_nm"][xs],
                              "energy_meV": result["energy_meV"][es],
                              **{key: source[key][np.ix_(xs, es)] for key in ("measured", "bulk_sum", "residual") if key in source},
                              "display_downsampled": len(xs)*len(es) < source["measured"].size}))

    @blueprint.post("/preview")
    def preview():
        payload = request.get_json()
        result = manager.require("profiles", payload.get("profiles_id"))
        measured, templates, selection = select_profile(result, payload.get("selection", {}),
                                                       require_templates=payload.get("analysis_kind") != "bulk_gaussian")
        if payload.get("analysis_kind") == "bulk_gaussian":
            if selection["kind"] != "region" or selection["region"] not in ("Si", "Ge"):
                raise ValueError("体谱预览仅支持 Si / Ge 体区。")
            return jsonify(plain({"analysis_kind": "bulk_gaussian", "energy_meV": result["energy_meV"],
                                  "measured": measured, "selection": selection}))
        weights, summed, residual, condition = two_reference_nnls(measured, templates)
        return jsonify(plain({"analysis_kind": "residual", "energy_meV": result["energy_meV"], "measured": measured, "templates": templates,
            "weights": weights[0], "bulk_sum": summed[0], "residual": residual[0], "selection": selection,
            "diagnostics": residual_diagnostics(result["energy_meV"], measured, residual[0], templates, weights[0]),
            "basis_condition": condition}))

    @blueprint.get("/fit")
    def fit():
        return jsonify(plain(manager.require("fit", request.args.get("result_id"))))

    @blueprint.post("/export")
    def export():
        payload = request.get_json()
        requested = payload.get("result_ids", {})
        if not isinstance(requested, dict) or "decomposition" not in requested:
            raise ValueError("请指定已完成的工作流结果。")
        results = {stage: manager.require(stage, expected) for stage, expected in requested.items()}
        # Collect all retained fits for these profiles, regardless of which region is on screen.
        if "profiles" in results:
            for key, expected in payload.get("fit_result_ids", {}).items():
                if key not in manager.fits or manager.fits[key]["result_id"] != expected:
                    raise ValueError("某区域拟合已被更新，请重新读取结果后导出。")
            results["fits"] = {key: fit for key, fit in manager.fits.items()
                               if fit["profiles_id"] == results["profiles"]["result_id"]}
            if results["fits"] and "fit" not in results:
                results["fit"] = manager.results["fit"]
        document = export_document(service.data, results, software)
        return send_file(io.BytesIO((json_text(document)+"\n").encode("utf-8")), mimetype="application/json",
                         as_attachment=True, download_name=service.data.source.with_suffix(".json").name)

    @blueprint.get("/arrays")
    def export_arrays():
        decomp = manager.require("decomposition", request.args.get("decomposition_id"))
        arrays = {"energy_meV": decomp["energy_meV"], "x_nm": service.data.x_nm, "y_nm": service.data.y_nm,
                  "component_spectra_ke": decomp["spectra"], "component_weights_yxk": decomp["weights"],
                  "reconstruction_rmse_yx": decomp["reconstruction_rmse_map"]}
        if request.args.get("localization_id"):
            local = manager.require("localization", request.args["localization_id"])
            if local["decomposition_id"] != decomp["result_id"]:
                raise ValueError("局部分解结果已失效。")
            arrays.update(interface_component_spectra_ke=local["spectra"], interface_component_weights_yxk=local["weights"],
                          interface_x_nm=local["x_nm"], interface_y_nm=local["y_nm"],
                          interface_reconstruction_rmse_yx=local["reconstruction_rmse_map"])
        if request.args.get("profiles_id"):
            profiles = manager.require("profiles", request.args["profiles_id"])
            if profiles["decomposition_id"] != decomp["result_id"]:
                raise ValueError("条带结果已失效。")
            arrays.update(profile_axis_nm=profiles["x_axis_nm"], global_bulk_templates_ke=profiles["templates"])
            arrays.update(strip_row_bounds=np.asarray([[strip["row_start"], strip["row_stop"]] for strip in profiles["strips"]], dtype=int),
                          strip_y_bounds_nm=np.asarray([[strip["y_min"], strip["y_max"]] for strip in profiles["strips"]]),
                          strip_core_overlap_rows=np.asarray([strip["core_overlap_rows"] for strip in profiles["strips"]], dtype=int))
            for strip in profiles["strips"]:
                if strip.get("measured_status", strip["status"]) == "available":
                    for key in ("measured", "weights", "bulk_sum", "residual", "templates", "pixel_counts"):
                        if key in strip: arrays[f"strip_{strip['index']:03d}_{key}"] = strip[key]
        stream = io.BytesIO()
        np.savez_compressed(stream, **arrays)
        stream.seek(0)
        return send_file(stream, mimetype="application/octet-stream", as_attachment=True,
                         download_name=service.data.source.with_suffix(".npz").name)

    app.register_blueprint(blueprint)
