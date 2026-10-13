"""Read saved EELS-ZEST spectra, without running ZEST or extracting its ZIP."""
from __future__ import annotations

from dataclasses import dataclass
import gzip
import io
import json
from pathlib import Path
import re
import zipfile

import numpy as np

from .io import EELSData, load_eels_hdf5, write_eels_hdf5

VERSION = "eels_zest_import_v1"
SIGNALS = {
    "raw_minus_zlp": "实测谱减去保存的零损失峰",
    "vib_only": "保存的拟合振动谱（模型）",
    "raw_data": "ZEST 输入实测谱（含零损失峰）",
}
MAX_JSON_BYTES = 32 * 1024**2
MAX_MAP_BYTES = 128 * 1024**2
MAX_CUBE_BYTES = 1024**3


def normalize_options(options=None):
    if options is None:
        options = {}
    if not isinstance(options, dict) or set(options) - {"signal", "energy_axis"}:
        raise ValueError("无效的 ZEST 导入选项。")
    signal = options.get("signal", "raw_minus_zlp")
    energy_axis = options.get("energy_axis", "raw")
    if not isinstance(signal, str) or signal not in SIGNALS:
        raise ValueError("不支持的 ZEST 谱线类型。")
    if not isinstance(energy_axis, str) or energy_axis not in {"raw", "corrected"}:
        raise ValueError("ZEST 能量轴须为 raw 或 corrected。")
    return {"signal": signal, "energy_axis": energy_axis}


def _read(archive, name, limit):
    if archive.getinfo(name).file_size > limit:
        raise ValueError(f"ZEST 条目过大：{name}")
    with archive.open(name) as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"ZEST 条目过大：{name}")
    return content


def _float_marker(value):
    if set(value) == {"__zest_float__"}:
        marker = value["__zest_float__"]
        if marker not in {"nan", "inf", "-inf"}:
            raise ValueError("无效的 ZEST 非有限值标记。")
        return float(marker)
    return value


def _json(content):
    value = json.loads(content.decode("utf-8-sig"), object_hook=_float_marker)
    if not isinstance(value, dict):
        raise ValueError("ZEST JSON 须为对象。")
    return value


def _vector(value, name, *, axis=False):
    result = np.asarray(value, dtype=float)
    if result.ndim != 1 or result.size < (8 if axis else 1):
        raise ValueError(f"ZEST {name} 须为一维数组" + ("且至少有 8 个通道。" if axis else "。"))
    if not np.all(np.isfinite(result)) or (axis and np.any(np.diff(result) <= 0)):
        raise ValueError(f"ZEST {name} 包含非有限值或无效的能量轴。")
    return result


def _indices(value, name, columns=None):
    array = np.asarray(value)
    expected_dim = 2 if columns else 1
    if (array.dtype.kind not in "iu" or array.ndim != expected_dim
            or (columns and array.shape[1] != columns) or np.any(array < 0)):
        raise ValueError(f"ZEST {name} 须为非负整数索引。")
    return array


def _spatial_axes(values, shape):
    """Optional explicit nm axes; never infer nm from pixel numbers."""
    present = [name in values for name in ("x_nm", "y_nm")]
    if not any(present):
        return None, None
    if not all(present):
        raise ValueError("ZEST 空间标定必须同时提供 x_nm 和 y_nm。")
    x, y = (_vector(values[name], name) for name in ("x_nm", "y_nm"))
    if (x.size != shape[1] or y.size != shape[0] or not all(
            np.all(np.diff(a) > 0) or np.all(np.diff(a) < 0) for a in (x, y))):
        raise ValueError("ZEST 空间轴尺寸或顺序与 [y,x] 图不匹配。")
    return x, y


def _scalar(value):
    a = np.asarray(value)
    if a.size != 1:
        raise ValueError("ZEST 空间标定标识必须是标量。")
    return a.item()


def _calibrated_axes(metadata, shape):
    """Producer spatial schema v1: physical pixel-center axes with declared units."""
    if (metadata.get("schema_version") != 1 or metadata.get("axis_order") != "YX"
            or metadata.get("pixel_index_base") != 0
            or metadata.get("coordinate_reference") != "pixel_center"
            or metadata.get("shape") != list(shape)):
        raise ValueError("不支持或不一致的 ZEST 空间标定格式。")
    factors = {"nm": 1., "pm": .001, "um": 1000., "µm": 1000., "μm": 1000.,
               "Å": .1, "angstrom": .1, "mm": 1e6, "m": 1e9}
    axes, description, missing = {}, {}, False
    for name in ("x", "y"):
        axis = metadata.get(name)
        if not isinstance(axis, dict):
            raise ValueError("ZEST 空间标定缺少 x / y 轴。")
        unit = axis.get("unit")
        if unit in {"pixel", "px", "pixels", "unknown", ""}:
            missing = True
            continue
        if unit not in factors:
            raise ValueError(f"尚不支持空间单位 {unit!r}，请在 ZEST 中导出 nm 坐标。")
        if axis.get("calibrated") is False:
            raise ValueError("ZEST 空间轴的物理单位与未标定声明冲突。")
        axes[name+"_nm"] = np.asarray(axis.get("values"), dtype=float)*factors[unit]
        description[name] = {k: axis[k] for k in ("unit", "source", "unit_source", "origin_source") if k in axis}
        description[name]["nm_per_unit"] = factors[unit]
    if missing:
        return None, None, {"status": metadata.get("status", "unavailable")}
    x, y = _spatial_axes(axes, shape)
    return x, y, description


def _merge_axes(x, y, candidate_x, candidate_y):
    if candidate_x is None:
        if x is not None:
            raise ValueError("ZEST 的空间标定与未标定声明互相矛盾。")
        return x, y
    if x is not None and (not np.array_equal(x, candidate_x) or not np.array_equal(y, candidate_y)):
        raise ValueError("ZEST 的两份空间标定互相矛盾。")
    return candidate_x, candidate_y


@dataclass
class ZestCube:
    intensity: np.ndarray
    energy_meV: np.ndarray
    x_nm: np.ndarray | None
    y_nm: np.ndarray | None
    provenance: dict


def read_zest_archive(source, options=None, progress=None):
    """Restore old and parallel exports; filename indices refer to job.coords."""
    options = normalize_options(options)
    source = Path(source).resolve()
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("ZIP 含重复条目，无法可靠确定像素来源。")
        roots = [n[:-len("summary.json")] for n in names
                 if n == "summary.json" or n.endswith("/summary.json")]
        roots = [p for p in roots if p + "maps.npz" in names]
        if len(roots) != 1:
            raise ValueError("这不是单份 EELS-ZEST 结果 ZIP：需要 summary.json、maps.npz 和 pixels/*.json.gz。")
        prefix = roots[0]
        summary = _json(_read(archive, prefix + "summary.json", MAX_JSON_BYTES))
        job = summary.get("job", {})
        if not isinstance(job, dict):
            raise ValueError("ZEST summary.json 缺少有效的 job。")
        coords = _indices(job.get("coords"), "job.coords", 2)
        if not len(coords) or len(set(map(tuple, coords))) != len(coords):
            raise ValueError("ZEST 像素坐标为空或重复。")
        map_bytes = _read(archive, prefix + "maps.npz", MAX_MAP_BYTES)
        with zipfile.ZipFile(io.BytesIO(map_bytes)) as map_archive:
            if sum(e.file_size for e in map_archive.infolist()) > MAX_MAP_BYTES:
                raise ValueError("ZEST maps.npz 解压后过大。")
        with np.load(io.BytesIO(map_bytes), allow_pickle=False) as maps:
            map_name = next((n for n in ("R2", "T_raw", "fit_status") if n in maps), None)
            if map_name is None or maps[map_name].ndim != 2:
                raise ValueError("ZEST maps.npz 缺少二维 R2 / T_raw / fit_status 图，无法确定空间尺寸。")
            shape = maps[map_name].shape
            if not all(shape) or np.any(coords >= np.asarray(shape)):
                raise ValueError("ZEST 像素坐标超出 [y,x] 图的范围。")
            x_nm, y_nm = _spatial_axes(maps, shape)
            spatial_origin = {}
            spatial_positions = None
            if "spatial_schema_version" in maps:
                metadata = {key: _scalar(maps["spatial_"+key]) for key in
                            ("schema_version", "axis_order", "pixel_index_base", "coordinate_reference", "status")}
                metadata["shape"] = list(shape)
                for name in ("x", "y"):
                    metadata[name] = {"values": maps["spatial_"+name],
                                      "unit": _scalar(maps["spatial_"+name+"_unit"])}
                dx, dy, spatial_origin = _calibrated_axes(metadata, shape)
                x_nm, y_nm = _merge_axes(x_nm, y_nm, dx, dy)
                if "spatial_positions_yx" in maps and dx is not None:
                    spatial_positions = np.asarray(maps["spatial_positions_yx"], dtype=float).copy()
                    spatial_positions = spatial_positions * np.array([
                        spatial_origin["y"]["nm_per_unit"], spatial_origin["x"]["nm_per_unit"]])
            saved_coords = _indices(maps["coords"], "maps.coords", 2) if "coords" in maps else None
            indices = _indices(maps["pixel_indices"], "pixel_indices") if "pixel_indices" in maps else None
            if indices is not None:
                if (saved_coords is None or len(indices) != len(saved_coords)
                        or len(set(indices.tolist())) != len(indices) or np.any(indices >= len(coords))
                        or not np.array_equal(coords[indices], saved_coords)):
                    raise ValueError("ZEST pixel_indices 与保存的像素坐标不一致。")
            elif saved_coords is not None and not set(map(tuple, saved_coords)) <= set(map(tuple, coords)):
                raise ValueError("ZEST maps.coords 与 job.coords 不一致。")
            if spatial_positions is not None and (saved_coords is None or spatial_positions.shape != saved_coords.shape
                    or not np.array_equal(spatial_positions, np.column_stack((y_nm[saved_coords[:,0]], x_nm[saved_coords[:,1]])))):
                raise ValueError("ZEST 逐像素物理坐标与空间轴不一致。")
        if prefix + "spatial_calibration.json" in names:
            metadata = _json(_read(archive, prefix+"spatial_calibration.json", MAX_JSON_BYTES))
            dx, dy, spatial_origin = _calibrated_axes(metadata, shape)
            x_nm, y_nm = _merge_axes(x_nm, y_nm, dx, dy)
        # New producers may retain geometry in dataset.json instead of NPZ.
        if prefix + "dataset.json" in names:
            metadata = _json(_read(archive, prefix + "dataset.json", MAX_JSON_BYTES))
            dx, dy = _spatial_axes(metadata, shape)
            if dx is not None:
                x_nm, y_nm = _merge_axes(x_nm, y_nm, dx, dy)

        entries = {}
        for name in names:
            match = re.fullmatch(re.escape(prefix) + r"pixels/(\d+)\.json\.gz", name)
            if match:
                index = int(match.group(1))
                if index >= len(coords) or index in entries:
                    raise ValueError("ZEST 像素文件索引越界或重复。")
                entries[index] = name
        if not entries:
            raise ValueError("ZIP 未保存可导入的逐像素谱线。")
        if indices is not None and set(indices.tolist()) != set(entries):
            raise ValueError("ZEST 像素文件与 pixel_indices 清单不一致。")
        if saved_coords is not None and (len(saved_coords) != len(entries)
                or set(map(tuple, coords[list(entries)])) != set(map(tuple, saved_coords))):
            raise ValueError("ZEST 像素文件与 maps.coords 清单不一致。")
        spectra, axes, used, failures, software_versions = [], [], [], [], set()
        for done, (index, name) in enumerate(sorted(entries.items()), 1):
            compressed = _read(archive, name, MAX_JSON_BYTES)
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                content = stream.read(MAX_JSON_BYTES + 1)
            if len(content) > MAX_JSON_BYTES:
                raise ValueError(f"ZEST 像素 JSON 解压后过大：{name}")
            pixel = _json(content)
            y, x = coords[index]
            for key, expected in (("pixel_y", int(y) + 1), ("pixel_x", int(x) + 1)):
                if key in pixel and pixel[key] != expected:
                    raise ValueError(f"{name} 的 {key} 与坐标清单不一致。")
            if pixel.get("error"):
                failures.append({"index": index, "reason": str(pixel["error"])[:300]})
                continue
            try:
                axis = _vector(pixel.get("ene_raw" if options["energy_axis"] == "raw" else "ene_corrected"),
                               "能量轴", axis=True)
                signal = options["signal"]
                if signal == "raw_minus_zlp":
                    values = _vector(pixel.get("raw_data"), "raw_data")
                    zlp = _vector(pixel.get("zlp_only"), "zlp_only")
                    if values.shape != zlp.shape:
                        raise ValueError("raw_data 与 zlp_only 通道数不一致。")
                    values = values - zlp
                else:
                    values = _vector(pixel.get(signal), signal)
                if values.shape != axis.shape:
                    raise ValueError("谱线与能量轴通道数不一致。")
            except (TypeError, ValueError) as error:
                raise ValueError(f"{name}：{error}") from error
            if spectra and (axis.shape != axes[0].shape or (
                    options["energy_axis"] == "raw" and not np.array_equal(axis, axes[0]))):
                raise ValueError("逐像素能量轴不一致；请选择校正能量轴，或重新导出统一通道的结果。")
            if np.prod(shape, dtype=np.int64) * axis.size * 8 > MAX_CUBE_BYTES:
                raise ValueError("ZEST 数据立方体超过当前导入的 1 GiB 上限。")
            spectra.append(values)
            axes.append(axis)
            used.append(index)
            version = pixel.get("settings", {}).get("software_version")
            if isinstance(version, str):
                software_versions.add(version)
            if progress and (done % 25 == 0 or done == len(entries)):
                progress(done, len(entries))
        if not used:
            raise ValueError("ZIP 中没有成功保存的有限谱线。")
        energy = axes[0]
        if options["energy_axis"] == "corrected":
            lower, upper = max(a[0] for a in axes), min(a[-1] for a in axes)
            energy = energy[(energy >= lower) & (energy <= upper)]
            if energy.size < 8:
                raise ValueError("逐像素校正能量轴的公共范围不足 8 个通道。")
        cube = np.full((*shape, energy.size), np.nan)
        for index, values, axis in zip(used, spectra, axes):
            cube[tuple(coords[index])] = (values if options["energy_axis"] == "raw"
                                         else np.interp(energy, axis, values))
        reversals = []
        if x_nm is not None:
            if x_nm.size > 1 and x_nm[1] < x_nm[0]:
                x_nm, cube = x_nm[::-1].copy(), cube[:,::-1,:].copy()
                reversals.append("x")
            if y_nm.size > 1 and y_nm[1] < y_nm[0]:
                y_nm, cube = y_nm[::-1].copy(), cube[::-1,:,:].copy()
                reversals.append("y")
        provenance = dict(version=VERSION, source_zip=str(source), job_id=job.get("id"),
            dataset_id=job.get("dataset_id"), job_status=job.get("status"), options=options,
            signal_label=SIGNALS[options["signal"]], intensity_scale="saved original intensity units",
            axis_order="y,x,energy", coordinate_rule="job.coords[index] = [y,x], zero-based",
            spatial_calibration="explicit_nm_axes" if x_nm is not None else "missing",
            spatial_metadata=spatial_origin, spatial_axes_reversed=reversals,
            energy_rule=("saved ene_raw, no resampling; per-pixel energy correction not applied"
                         if options["energy_axis"] == "raw" else
                         "ene_corrected, linear interpolation to first valid pixel grid within common support; no extrapolation"),
            input_shape=[*shape, axes[0].size], output_shape=list(cube.shape),
            valid_pixels=len(used), missing_pixels=int(np.prod(shape)) - len(used),
            requested_pixels=len(coords), saved_pixel_files=len(entries), failed_pixels=failures,
            zest_software_versions=sorted(software_versions),
            total_counts_status="missing_in_input", refit=False)
        return ZestCube(cube, energy, x_nm, y_nm, provenance)


def prepare_zest_import(source, destination, options=None, progress=None):
    """Publish a validated, durable H5 for existing analysis/replay consumers."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError(f"转换目标已存在，拒绝覆盖：{destination}")
    result = read_zest_archive(source, options, progress)
    if result.x_nm is None:
        raise ValueError(
            f"已读取 EELS-ZEST 的 {result.provenance['valid_pixels']} 个像素、"
            f"{result.energy_meV.size} 个能量通道，但 ZIP 未包含物理空间标定（X/Y 坐标及单位）。"
            "请用补全尺寸信息的新版处理程序重新导出后再导入；不会将像素索引当作 nm，当前数据保持不变。"
        )
    data = EELSData(result.intensity, result.x_nm, result.y_nm, result.energy_meV,
                    Path(source).resolve(), {"original_source": str(Path(source).resolve()),
                    "zest_import": result.provenance})
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".writing")
    if temporary.exists():
        raise ValueError(f"临时转换文件已存在：{temporary}")
    try:
        write_eels_hdf5(data, temporary)
        load_eels_hdf5(temporary)
        temporary.rename(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return load_eels_hdf5(destination)
