# EELS Python Analysis

This standalone project exports an 'EELS' MATLAB object to HDF5 and provides
pure PCA peak analysis plus bounded multi-Gaussian fitting for Si, interface,
and Ge regions. It does not modify the 3D-EELS TOOLBOX.

## 1. Create the Python environment

From this folder in PowerShell:

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
~~~

## 2. Export the MATLAB EELS object

Open MATLAB, add both the toolbox and this project's 'matlab' directory:

~~~matlab
addpath(genpath('C:\tools\3D-EELS TOOLBOX'))
addpath('C:\projects\EELS-tools\EELS_Python_Analysis\matlab')

export_eels_to_hdf5( ...
    'C:\data\sample.mat', ...
    'C:\data\sample.h5')
~~~

For fitting a deconvolved spectrum, call the exporter on a MAT file that
contains the actual deconvolved 'EELS' object, rather than treating the source
file above as the final pipeline output.

When the toolbox has already created a workspace variable called
'DeconvolutedData', export that object directly:

~~~matlab
export_eels_to_hdf5(DeconvolutedData, ...
    'C:\data\deconvoluted-data.h5')
~~~

## 3. Start the browser interface

The browser interface is the recommended entry point on Windows:

~~~powershell
.\start_web.ps1 -DataFile 'C:\data\sample.h5'
~~~

The launcher checks `/health` before opening the browser. It reuses an existing
server from this project or scans for a free port starting at 8765. The actual
URL, server PID, log paths, and stop command are printed in the terminal.

### 日常启动

网页启动入口为 `start_web.ps1`。在项目目录的 PowerShell 中运行：

~~~powershell
.\start_web.ps1 -DataFile 'C:\data\sample.h5'
~~~

脚本使用项目 `.venv` 在后台运行 Python，健康检查通过后打开默认浏览器，随后返回命令行。
关闭终端或浏览器不会停止后台服务。默认端口为 8765，被其他程序或旧版服务占用时自动顺延；
日志存入 `web_runs/`，不覆盖旧日志。同项目已有服务会复用，保留当前导入数据与分析结果。
首次启动请通过 `-DataFile` 指定自己的 H5；仓库不提供实验数据，也不包含历史默认文件 `2-15-result5.h5`。已有本项目会话时，省略 `-DataFile` 可复用该会话。

常用命令：

~~~powershell
.\start_web.ps1 -DataFile 'C:\data\sample.h5' -NoBrowser
.\start_web.ps1 -DataFile 'C:\data\sample.h5' -Port 8800
.\start_web.ps1 -DataFile "C:\path\to\deconvoluted-data.h5"
.\stop_web.ps1 -Port 8765
~~~

显式指定不同数据文件时不会替换已有会话，而是在下一个可用端口启动新服务。
停止时使用启动输出的实际端口；先导出需要保留的结果。正在分析、导入或计算不确定度时，
停止脚本会拒绝中断；确定要丢弃任务时才使用 `-Force`。它不会停止其他项目或其他应用。

若 PowerShell 阻止脚本执行，可仅为这次启动使用：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_web.ps1 -DataFile 'C:\data\sample.h5'
~~~

仍可直接运行 Python，但这种方式需要保持终端开启：

~~~powershell
.\.venv\Scripts\python.exe eels_web.py 'C:\data\sample.h5' --port 8765 --open
~~~

The page supports rectangular Si, interface, and Ge ROIs, non-blocking
uncertainty calculations, spectrum plotting, MAT/HDF5 import, and single-file JSON result export.

### 网页导入与结果导出

点击右上角 **导入数据**，选择 `.mat`、`.h5` 或 `.hdf5`，也可填写本地绝对路径。
Toolbox 的 MAT 文件需含名为 `data` 的 `EELS` 对象，网页会后台调用本机 MATLAB，
将其转换为 HDF5 后自动加载；不做额外归一化、滤波或解卷积，不修改原文件和 toolbox。
MATLAB 与 toolbox 路径默认自动查找，其他机器可设置 `EELS_MATLAB_PATH` 和
`EELS_TOOLBOX_PATH` 环境变量。HDF5 导入和结果复现都不需要 MATLAB。
只导入可信来源的 MATLAB 对象文件。导入新文件成功后，旧分析结果失效；保留输入参数和仍可用的选区，越界坐标会按新数据检查并提示。

### 参考图和拟合选区

参考底图支持 EELS 总计数、固定窄能窗积分和两侧参考谱贡献，并可显示候选界面中心。切换底图或候选线不移动 Si／界面／Ge 选框。

可选衬度拟合默认关闭。启用后支持 erf／logistic 比较、各区域边界百分比、拟合预览与明确应用；默认 Si／Ge 体区达到 99% 平台、界面采用 10%–90%、纵向取满。应用后仍可手动调整，超时和错误会恢复操作。总计数不等于 HAADF，拟合中心只是参考信号的过渡位置。详见 [参考图与候选界面](docs/参考图与候选界面.md) 和 [衬度拟合选区](docs/衬度拟合选区.md)。

### JSON 导出与复现

计算后点击 **导出结果（JSON）**，下载单个可读 JSON 文件，默认与导入文件同名，仅替换扩展名。前部保存各区域峰位、宽度、
标准差、95% 区间和运行状态，末尾 `reproducibility` 保存各区域的 **实际运行参数**、seed、
数据路径及数值校验值、算法与依赖版本。不再默认导出数据副本、源码、谱线或逐次抽样明细。
未计算的不确定度记为 `null`，不是零。每个模式每个区域保存最近一次结果。

独立转换命令（不覆盖已有输出文件）：

~~~powershell
.\.venv\Scripts\python.exe import_eels_data.py "C:\data\result.mat" "C:\data\result.h5"
~~~

保留对应的输入数据和程序版本后，可以重跑分析与抽样，不需要原来的逐次抽样结果：

~~~powershell
.\.venv\Scripts\python.exe reproduce.py "C:\results\eels_results.json"
.\.venv\Scripts\python.exe reproduce.py "C:\results\eels_results.json" --data "D:\moved\data.h5"
~~~

输出为 `<原文件名>_reproduced.json`，不会覆盖已有文件。`--data` 可指定移动后的 HDF5 或可信
toolbox MAT 文件（MAT 需本机 MATLAB）。输入数值不一致会拒绝复现；程序或依赖版本不一致时
默认拒绝，明确要做跨版本比较才使用 `--allow-version-mismatch`，此时不承诺结果完全一致。
详细说明见 [数据导入与结果导出](docs/数据导入与结果导出.md)，开发者 JSON 结构约定见 [AGENTS.md](AGENTS.md)。

### 纯 PCA 峰分析

网页默认使用 **纯 PCA 峰分析**。选择 ROI 和分析能量范围，设置主成分数、目标峰数、
最小峰距和最小突出度后，点击 **分析当前区域** 或 **分析全部区域**。计算过程是：

1. 对分析范围内的完整谱像做均值中心化 PCA；
2. 用前 K 个主成分重建所选 ROI 的平均谱；
3. 在重建谱中寻找满足峰距和突出度条件的局部极大值；
4. 用三点抛物线插值细化峰位，并报告半突出度宽度。

该模式不会调用高斯模型。表中的宽度是 **半突出度宽度**，不是高斯 FWHM；强烈重叠、
背景倾斜或能量窗口截断时，宽度可能明显偏大。若实际只找到少于目标峰数的可分辨峰，
程序会报告“未分辨”，不会用噪声补足峰数。

**评估不确定度** 使用空间块自助法：按空间块重采样完整谱像和 ROI，每次重新计算 PCA、
重建谱并寻找峰，最后给出峰位及半突出度宽度的 95% 百分位区间。该区间反映空间采样和
PCA 重算带来的经验不确定度，不包含能量标定、背景扣除、去卷积模型和 ROI 选择等系统误差。

### 高斯拟合

需要参数化峰面积或高斯 FWHM 时，可将分析方式切换为 **高斯自动寻优**。选择 ROI 和
拟合能量范围，输入已知峰数后点击 **分析当前区域**；程序默认尝试 48 组受边界限制的
初始峰位，并保留残差 RMSE 最小的已尝试结果。FWHM 的上下限仍需明确保留，因为完全
无约束地追求最小残差会将噪声拟合成不具物理意义的窄峰。已有可靠峰位或峰位窗口先验时，
可切换到 **高斯手动约束**。自动寻优得到的最佳模型会固定后再做残差块自助法，因此
报告的区间描述的是该已选模型下的参数不确定度。

## 4. Start the desktop fitting interface

~~~powershell
.\.venv\Scripts\python.exe eels_peakfit.py 'C:\data\sample.h5'
~~~

The map uses an integrated energy window. Select 'Si', 'Interface', or 'Ge'
with the radio buttons, then drag a rectangle on the map. Set the fit window,
initial peak centers, center bounds, and FWHM bounds. Fit either the active
region or all three regions.

'Bootstrap active' and 'Bootstrap all' perform residual block bootstrap.
The block size can be an integer number of energy channels or 'auto'.
The reported intervals are conditional on the ROI, preprocessing, peak model,
and bounds. Repeat the analysis with changed ROI boundaries, background
settings, and deconvolution settings to assess systematic uncertainty.

## HDF5 convention

The output contains:

- '/intensity': float64, logically ordered as [y, x, energy]
- '/x_nm', '/y_nm': pixel-center coordinates, starting at zero
- '/energy_meV': the original energy calibration
- '/reference_maps/total_counts': retained EELS.info.totint when available

The loader handles the dimension reversal some MATLAB HDF5 readers expose to
Python. The GUI always works internally with [y, x, energy].

New exports also declare total-count availability, source and stored axis order. See [H5 数据格式与总计数](docs/H5数据格式与总计数.md); missing total counts must not be replaced with a sum of the processed spectrum.

## Verify the numerical core

~~~powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
~~~

浏览器测试、绘图工作流自检和完整目录说明见 [仓库 README](../README.md)。
