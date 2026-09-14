"""Local MATLAB EELS object conversion; original data and toolbox remain read-only."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .io import EELSData, load_eels_hdf5


APP_ROOT = Path(__file__).resolve().parents[1]


def matlab_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def prepare_import(source: Path, destination: Path) -> EELSData:
    """Read HDF5 directly or convert a trusted MAT object using installed MATLAB."""
    source = source.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"找不到数据文件：{source}")
    suffix = source.suffix.lower()
    if suffix in {".h5", ".hdf5"}:
        return load_eels_hdf5(source)
    if suffix != ".mat":
        raise ValueError("只支持 .mat、.h5 和 .hdf5 文件。")
    executable = os.environ.get("EELS_MATLAB_PATH") or shutil.which("matlab")
    if not executable:
        candidates = sorted(Path("C:/Program Files/MATLAB").glob("R*/bin/matlab.exe"))
        executable = str(candidates[-1]) if candidates else None
    if not executable:
        raise ValueError("MAT 数据含 EELS 对象，需要本机 MATLAB。请设置 EELS_MATLAB_PATH，或先在 MATLAB 中导出 HDF5。")
    toolbox = Path(os.environ.get("EELS_TOOLBOX_PATH", str(
        APP_ROOT.parent.parent / "calculation_program" / "3D-EELS TOOLBOX"
    )))
    if not (toolbox / "Import" / "EELS.m").is_file():
        raise ValueError("找不到 3D-EELS TOOLBOX 的 EELS 类，请设置 EELS_TOOLBOX_PATH。")
    if destination.exists():
        raise ValueError(f"转换目标已存在，拒绝覆盖：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = (
        f"addpath(genpath({matlab_literal(toolbox)}));"
        f"addpath({matlab_literal(APP_ROOT / 'matlab')});"
        f"export_eels_to_hdf5({matlab_literal(source)},{matlab_literal(destination)});"
    )
    try:
        result = subprocess.run(
            [executable, "-batch", command], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("MATLAB 转换超过 15 分钟，已终止；当前数据未改变。") from error
    if result.returncode != 0:
        details = (result.stderr or result.stdout)[-3500:]
        raise ValueError(f"MATLAB 转换失败（请检查许可和 EELS 类路径）：\n{details}")
    data = load_eels_hdf5(destination)
    data.attributes["original_source"] = str(source)
    data.attributes["import_operation"] = "MATLAB EELS to HDF5; no signal processing"
    return data
