# EELS Python Analysis

当前版本：**1.0.0**。界面右上角显示版本号，`/health` 和 `/api/info` 的
`app_version` 来自 `eelsfit/version.py`。更新记录见 [CHANGELOG.md](CHANGELOG.md)。

This standalone project exports an 'EELS' MATLAB object to HDF5 and provides
pure PCA peak analysis plus bounded multi-Gaussian fitting for Si, interface,
and Ge regions. It does not modify the 3D-EELS TOOLBOX.

网页采用 EELS-ZEST Parallel 的浅色渐变与半透明卡片样式，统一蓝色操作按钮、
编号步骤和导入窗口。顶部概览显示当前数据的像素数、能量通道、能量范围及空间
像素中心跨度；常规分析在桌面端左侧显示图表、右侧调整参数，窄屏自动纵向排列。
样式文件为 `web/static/workbench.css`，不改变计算方法、热图色标或结果格式。

网页顶端现在按分析方法进入独立工作区：PCA、高斯自动、高斯手动，以及新的
**NMF / MCR-ALS 界面残差工作流**。新流程依次提供全像素分解、组分与空间图、
Si / Ge / 普通界面 / 位错核分区、可选界面配准和 y 条带、非负体谱扣除，
以及残差峰和联合模型交叉拟合。输入需已降噪、扣背景。
详见 [多变量界面残差工作流](docs/多变量界面残差工作流.md)。

新流程导出 `eels_interface_workflow_v1`，由 `reproduce_workflow.py` 复现；
原 PCA / 高斯的 `eels_peak_results_v1` 和 `reproduce.py` 保持独立。
新方法目前不计算峰参数误差条；未计算值不会当作零误差。

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
.\start_web.ps1
~~~

The latest UI uses **http://127.0.0.1:8768**. The launcher checks `/health` and
the running source version before opening the browser. It never silently
changes ports. The URL, server PID, logs and stop command are printed.

### 日常启动

启动入口改为 `start_web.ps1`，方式与 grading web service 一致，不再使用 BAT。在项目目录的 PowerShell 中运行：

~~~powershell
.\start_web.ps1
~~~

脚本使用项目 `.venv` 在后台运行 Python，健康检查通过后打开默认浏览器，随后返回命令行。
关闭终端或浏览器不会停止后台服务。**最新版日常入口固定为 8768，不再自动顺延端口。**
日志存入 `web_runs/`，不覆盖旧日志。同端口、同项目且代码版本一致的服务会复用，保留当前导入数据与分析结果。
8768 被其他应用或旧版服务占用时明确报错，保留现有进程；其他端口的旧会话不会改变日常入口。
代码更新后，先导出需保留的旧结果，再停止旧服务并在 8768 重启。正在计算时等待完成；升级时不要直接丢弃会话。
新建服务默认先打开空白 UI，不读取上次文件、旧默认 H5，也不弹出文件选择窗口。
进入页面后点击“导入数据”选择 MAT / H5 / EELS-ZEST ZIP，或点击“导入流程 JSON”继续调整已有结果。
`-NoBrowser` / 非交互启动同样不需要文件；只有显式传入 `-DataFile` 才在启动时加载该 H5。
`web_runs/last_launch.json` 仅保留可选的启动历史，不再用于自动选取输入数据。
空白页面不会覆盖已有参数记忆；导入数据后恢复适用的参数。重复启动会复用同版本服务，保留当前会话。

常用命令：

~~~powershell
.\start_web.ps1 -NoBrowser
.\start_web.ps1 -DataFile "C:\path\to\deconvoluted-data.h5"
.\stop_web.ps1 -Port 8768
~~~

显式指定不同数据文件时不会替换已有会话，也不会换端口；请在网页中导入，或先导出并停止旧会话再重新启动。
`-Port` 仅供明确指定的测试/旧服务使用；`-MaxPortScan` 为旧快捷方式保留，但已不再扫描。
停止时先导出需要保留的结果。正在分析、导入或计算不确定度时，
停止脚本会拒绝中断；确定要丢弃任务时才使用 `-Force`。它不会停止其他项目或其他应用。

测试源码放在 `tests/`，运行产物、任务备份和临时脚本统一放在
`test_runs/.tmp_<任务名>_<唯一后缀>/`，不再堆在项目根目录。可删除的临时目录使用 `.tmp_` 前缀；
每次任务结束清理无用产物，需保留的验证截图/记录也留在该目录内。
正式服务的参数记忆和结果归档不属于临时文件。详见 [测试目录说明](test_runs/README.md)。

若 PowerShell 阻止脚本执行，可仅为这次启动使用：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_web.ps1
~~~

仍可直接运行 Python，但这种方式需要保持终端开启：

~~~powershell
.\.venv\Scripts\python.exe eels_web.py --port 8768 --open
~~~

The page supports rectangular Si, interface, and Ge ROIs, non-blocking
uncertainty calculations, spectrum plotting, MAT/HDF5 import, and single-file JSON result export.

### 网页导入与结果导出

#### EELS-ZEST ZIP

在“导入数据”中直接选择 EELS-ZEST 导出的 ZIP，或粘贴其本地绝对路径，无需解压、MATLAB 或重新运行 ZEST。上传上限仍为 1 GB，较大文件使用本地路径。原 ZIP 保持不变，成功导入时在 `imports/<唯一目录>/<输入名>.h5` 保存后续分析和复现所需的数据。

- **谱线**：默认 `raw_data - zlp_only`，即实测谱扣除保存的零损失峰，保留负值与原强度。可显式选择 `vib_only`（拟合振动模型）或 `raw_data`（ZEST 输入谱，仍含零损失峰）。这些字段已经是原强度尺度，不再乘除 `norm_val`。不把拟合振动谱、拟合残差、温度和温度不确定度混为实测谱或峰位误差。
- **能量轴**：默认保存的 `ene_raw`，要求各有效像素通道完全一致，不应用逐像素能量修正，也不插值。选择 `ene_corrected` 时，将每条谱线线性插值到第一条有效谱线在全部有效像素公共范围内的通道，不外推，实际通道与操作写入来源记录。
- **空间方向**：以 `summary.json` 的 `job.coords[index] = [y,x]` 和 `pixels/<index>.json.gz` 对应，不能按文件排序直接 reshape。兼容旧版完整包及并行版的 `pixel_indices`；未计算和显式失败像素留为 NaN，报告数量，不补零。
- **空间尺寸自动读取，不手填**：已接入处理端新增的 `spatial_calibration.json`（schema_version 1）及 `maps.npz` 内的 `spatial_x` / `spatial_y`、单位和逐像素坐标。要求坐标与 `[y,x]` 排列一致；显式物理单位换算为 nm，降序空间轴会连同数据一起反向排列并记录，不插值。另兼容 `maps.npz` 或 `dataset.json` 内直接保存的一维 `x_nm`、`y_nm`。未标定的 pixel / unknown 轴不能当作 nm：旧包缺尺寸时会报告已读取像素/通道数量，保留原会话，需处理端重新导出。
- **缺失参考图**：ZIP 的温度、可靠性、R² 等图不替代 `EELS.info.totint`，总计数图保持不可用。
- 导入窗口显示读取进度；完成后页面顶部显示实际谱线和能量轴口径。选区、PCA、高斯和多变量工作流沿用既有入口。失败时保留当前数据、参数及分析结果。
- 新 H5 保存 `zest_import_json` 来源记录。两类结果 JSON 的 `reproducibility.data.input_preparation` 记录 ZIP 来源、谱线选择、能量处理、像素数量及空间标定状态，不包含全谱。复现和流程 JSON 导入继续使用生成的 H5；请保留该文件。

回归检查：`python -m unittest tests.test_zest_import -v`；浏览器检查：`node tests/browser_zest_import.cjs`（隔离的合成 ZIP/服务）。参考旧包 `EELS-ZEST-b4d6b27e24e1.zip` 已验证谱线排布为 `[48,36,683]`；其 nm 标定缺失，等待处理端补全后使用新版 ZIP。

已对照处理端实际 `spatial.py` 的导出结构，并读取新样例 `EELS-ZEST_4pixel_spatial_calibrated.zip`：`[2,2,683]`，X/Y 像素间距均为 `0.4166666567325592 nm`。新包可直接导入。浏览器测试可通过进程环境变量 `EELS_ZEST_EXAMPLE` 指定该样例进行真实上传和选区 PCA 验证，不写入日常服务。


页面顶部 **导入流程 JSON** 可载入 `eels_interface_workflow_v1` 文件（例如 try 2-7），
恢复分解参数、四区选框、条带和配准设置、局部分解，以及各区已保存的峰参数。
JSON 不含完整谱图：导入会先匹配当前 H5 或 JSON 记录的原 H5 路径，校验数值后重建
分解、体谱及显示曲线；已保存的峰位、宽度、振幅和未计算误差的 null 保持原值，不自动重新寻峰。
H5 已移动时可填写新绝对路径，或先通过“导入数据”载入。程序版本不同时需勾选
“允许跨版本重建谱图”；重建的谱图不保证与原版本完全相同。全部成功后才替换当前工作流，
失败、超时或取消保留原状态。导入后可继续调整参数并运行当前区或四区拟合，再导出流程 JSON。
旧 PCA 结果和绘图清单不适用于此入口。

点击右上角 **导入数据**，选择 `.mat`、`.h5` 或 `.hdf5`，也可填写本地绝对路径。
Toolbox 的 MAT 文件需含名为 `data` 的 `EELS` 对象，网页会后台调用本机 MATLAB，
将其转换为 HDF5 后自动加载；不做额外归一化、滤波或解卷积，不修改原文件和 toolbox。
MATLAB 与 toolbox 路径默认自动查找，其他机器可设置 `EELS_MATLAB_PATH` 和
`EELS_TOOLBOX_PATH` 环境变量。HDF5 导入和结果复现都不需要 MATLAB。
只导入可信来源的 MATLAB 对象文件。导入新文件成功后，选区和旧结果会清空。

计算后点击 **导出结果（JSON）**，下载单个可读 JSON 文件。前部保存各区域峰位、宽度、
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
.\.venv\Scripts\python.exe eels_peakfit.py .\sample.h5
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

The loader handles the dimension reversal some MATLAB HDF5 readers expose to
Python. The GUI always works internally with [y, x, energy].

## Verify the numerical core

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
~~~
