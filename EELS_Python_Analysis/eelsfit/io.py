"""Read the deliberately simple HDF5 interchange format used by this project."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import h5py
import numpy as np


REFERENCE_MAPS_SCHEMA = "eels_reference_maps_v1"


@dataclass
class EELSData:
    intensity: np.ndarray
    x_nm: np.ndarray
    y_nm: np.ndarray
    energy_meV: np.ndarray
    source: Path
    attributes: dict[str, Any]
    total_counts: np.ndarray | None = None

    @property
    def shape(self) -> tuple[int, int, int]:
        return tuple(int(value) for value in self.intensity.shape)


def _decode_attribute(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.ndarray) and value.dtype.kind == "S":
        return [item.decode("utf-8") for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _orient_intensity(
    intensity: np.ndarray, y_count: int, x_count: int, energy_count: int
) -> np.ndarray:
    expected = (y_count, x_count, energy_count)
    if intensity.shape == expected:
        return intensity

    # MATLAB HDF5 readers can expose column-major dimensions in reverse order.
    if intensity.shape == expected[::-1]:
        return np.transpose(intensity, (2, 1, 0))

    raise ValueError(
        "Intensity shape does not match coordinate axes. "
        f"Expected {expected} (or reversed storage order), got {intensity.shape}."
    )


def load_eels_hdf5(path: str | Path) -> EELSData:
    """Load intensity[y, x, energy] and coordinate vectors from an HDF5 file."""

    source = Path(path).expanduser().resolve()
    with h5py.File(source, "r") as handle:
        required = ("/intensity", "/x_nm", "/y_nm", "/energy_meV")
        missing = [name for name in required if name not in handle]
        if missing:
            raise KeyError(f"Missing HDF5 datasets: {', '.join(missing)}")

        intensity = np.asarray(handle["/intensity"], dtype=float)
        x_nm = np.asarray(handle["/x_nm"], dtype=float).reshape(-1)
        y_nm = np.asarray(handle["/y_nm"], dtype=float).reshape(-1)
        energy_meV = np.asarray(handle["/energy_meV"], dtype=float).reshape(-1)
        attributes = {
            str(key): _decode_attribute(value) for key, value in handle.attrs.items()
        }
        attributes.update(
            {
                f"intensity.{key}": _decode_attribute(value)
                for key, value in handle["/intensity"].attrs.items()
            }
        )
        if "zest_import_json" in attributes:
            provenance = json.loads(attributes.pop("zest_import_json"))
            if not isinstance(provenance, dict) or provenance.get("version") != "eels_zest_import_v1":
                raise ValueError("Unsupported EELS-ZEST import provenance.")
            attributes["zest_import"] = provenance
        total_counts = None
        if "/reference_maps/total_counts" in handle:
            try:
                counts_dataset = handle["/reference_maps/total_counts"]
                schema = attributes.get("reference_maps_schema", "")
                if schema not in ("", REFERENCE_MAPS_SCHEMA):
                    raise ValueError("不支持此 HDF5 的总计数格式版本。")
                if not isinstance(counts_dataset, h5py.Dataset) or counts_dataset.dtype.kind not in "fiu":
                    raise ValueError("总计数必须是实数二维数组。")
                order = _decode_attribute(counts_dataset.attrs.get("axis_order", ""))
                if order not in ("y_nm,x_nm", "x_nm,y_nm") and (schema or order):
                    raise ValueError("总计数图缺少有效的 axis_order，无法确定空间方向。")
                if schema and (
                    attributes.get("total_counts_status") != "available"
                    or _decode_attribute(counts_dataset.attrs.get("source_field", "")) != "EELS.info.totint"
                ):
                    raise ValueError("总计数图的来源或状态声明与 HDF5 格式规则不一致。")
                candidate = np.asarray(counts_dataset, dtype=float)
                reversed_cube = intensity.shape != (y_nm.size, x_nm.size, energy_meV.size)
                if order == "x_nm,y_nm" or (not order and reversed_cube):
                    candidate = candidate.T
                if candidate.shape != (y_nm.size, x_nm.size) or not np.any(np.isfinite(candidate)):
                    raise ValueError("总计数图尺寸或有效数值与当前谱像不匹配，未使用。")
                total_counts = candidate
            except (ValueError, TypeError, OSError) as error:
                attributes["total_counts_error"] = str(error)
        elif attributes.get("total_counts_status") == "available":
            attributes["total_counts_error"] = "HDF5 声明有总计数，但缺少 reference_maps/total_counts；请重新转换源 MAT。"
        elif attributes.get("total_counts_status") == "missing_in_source":
            attributes["total_counts_error"] = "源 MAT 未保存 info.totint，无法提供保留的总计数图。"
        elif attributes.get("total_counts_status") == "missing_in_input":
            attributes["total_counts_error"] = "当前输入没有总计数图；可检查源 MAT 的 info.totint 后重新转换。"

    intensity = _orient_intensity(
        intensity, y_nm.size, x_nm.size, energy_meV.size
    )
    if x_nm.size < 1 or y_nm.size < 1 or energy_meV.size < 8:
        raise ValueError("Data needs spatial coordinates and at least eight energy channels.")
    if not all(np.all(np.isfinite(axis)) for axis in (x_nm, y_nm, energy_meV)):
        raise ValueError("Coordinate vectors must contain finite values.")
    if not np.any(np.isfinite(intensity)):
        raise ValueError("Intensity has no finite samples.")
    if np.any(np.diff(x_nm) <= 0) or np.any(np.diff(y_nm) <= 0):
        raise ValueError("Spatial coordinate vectors must be strictly increasing.")
    if np.any(np.diff(energy_meV) <= 0):
        raise ValueError("Energy coordinate vector must be strictly increasing.")

    return EELSData(
        intensity=intensity,
        x_nm=x_nm,
        y_nm=y_nm,
        energy_meV=energy_meV,
        source=source,
        attributes=attributes,
        total_counts=total_counts,
    )


def write_eels_hdf5(data: EELSData, path: str | Path) -> None:
    """Write canonical Python axis order, without changing any sample values."""
    if data.total_counts is not None:
        counts_array = np.asarray(data.total_counts)
        if (counts_array.dtype.kind not in "fiu" or counts_array.shape != data.shape[:2]
                or not np.any(np.isfinite(counts_array))):
            raise ValueError("Total-count map must be real, contain finite values and match the spectrum-image geometry.")
    with h5py.File(path, "x") as handle:
        handle.create_dataset("intensity", data=data.intensity, compression="gzip")
        for name in ("x_nm", "y_nm", "energy_meV"):
            handle.create_dataset(name, data=getattr(data, name))
        handle.attrs["format"] = "eels_python_hdf5_v1"
        handle.attrs["source"] = str(data.source)
        if "zest_import" in data.attributes:
            handle.attrs["zest_import_json"] = json.dumps(data.attributes["zest_import"], ensure_ascii=False, allow_nan=False)
            handle.attrs["original_source"] = data.attributes.get("original_source", str(data.source))
        handle.attrs["reference_maps_schema"] = REFERENCE_MAPS_SCHEMA
        handle.attrs["total_counts_status"] = (
            "available" if data.total_counts is not None
            else "missing_in_source" if data.attributes.get("total_counts_status") == "missing_in_source"
            else "missing_in_input"
        )
        handle["intensity"].attrs["axis_order"] = "y_nm,x_nm,energy_meV"
        if data.total_counts is not None:
            counts = handle.create_dataset("reference_maps/total_counts", data=data.total_counts)
            counts.attrs["axis_order"] = "y_nm,x_nm"
            counts.attrs["source_field"] = "EELS.info.totint"
