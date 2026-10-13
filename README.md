# EELS tools

当前分析网页版本：**1.1.0**（[更新记录](EELS_Python_Analysis/CHANGELOG.md)）。

用于 Si／界面／Ge／位错核区域的本地 EELS 分析与 PCA 结果绘图。包含中文网页 UI、MAT→HDF5 转换、EELS-ZEST ZIP 导入、峰分析、不确定度计算、多变量界面残差分析，以及复现、绘图和参数重算工作流。

仓库仅保存代码、测试和文档，不附带实验 MAT/H5、分析 JSON、图件、MATLAB toolbox、虚拟环境或本机配置。安装后需提供自己的数据。

## 目录

| 目录 | 用途 |
| --- | --- |
| [EELS_Python_Analysis](EELS_Python_Analysis/README.md) | Flask 网页、数值分析、MAT/H5/ZEST ZIP 导入、流程 JSON 恢复、结果导出与复现 |
| [eels_plot_workflow](eels_plot_workflow/README.md) | 从 PCA JSON 恢复谱线、绘制组合图、补算缺失误差和重算突出度阈值 |
| [MATLAB 转换脚本](EELS_Python_Analysis/matlab) | 单对象与批量 MAT→HDF5，保留可用的 EELS 总计数 |

## Windows 快速开始

需要 Python 3.12。以下命令在仓库根目录的 PowerShell 中运行：

```powershell
py -3.12 -m venv .\EELS_Python_Analysis\.venv
.\EELS_Python_Analysis\.venv\Scripts\python.exe -m pip install -r .\EELS_Python_Analysis\requirements.txt
.\EELS_Python_Analysis\start_web.ps1
```

打开 [http://127.0.0.1:8768](http://127.0.0.1:8768)。首次启动为空白界面，在网页中导入 H5、可信的 Toolbox MAT、已保存空间标定的 EELS-ZEST ZIP，或导入流程 JSON 继续分析。服务只监听本机；端口被其他服务占用时会明确提示，不自动改用其他端口。同项目、同版本的已有服务会复用，保留当前数据与结果。

完成环境安装后，日常可直接双击 `EELS_Python_Analysis/打开分析界面.vbs`。启动前不需要选择或准备数据文件；打开网页后再导入。

停止前先导出结果，再按实际端口运行：

```powershell
.\EELS_Python_Analysis\stop_web.ps1 -Port 8768
```

如需转换 Toolbox MAT，先安装 MATLAB 和另行获取的 3D-EELS TOOLBOX，然后在当前 PowerShell 设置路径。下面也演示先在命令行转换、再显式载入 H5 的方式：

```powershell
$env:EELS_MATLAB_PATH = 'C:\Program Files\MATLAB\R2025a\bin\matlab.exe'
$env:EELS_TOOLBOX_PATH = 'C:\tools\3D-EELS TOOLBOX'
.\EELS_Python_Analysis\.venv\Scripts\python.exe .\EELS_Python_Analysis\import_eels_data.py 'C:\data\sample.mat' 'C:\data\sample.h5'
.\EELS_Python_Analysis\start_web.ps1 -DataFile 'C:\data\sample.h5'
```

MATLAB 版本路径按本机安装修改。H5 分析、JSON 复现和绘图不需要 MATLAB。网页与 Python 转换入口适配变量 `data` 为 `EELS` 对象的 MAT；其他对象和多变量文件见 [导入说明](EELS_Python_Analysis/docs/数据导入与结果导出.md) 和 [H5 格式](EELS_Python_Analysis/docs/H5数据格式与总计数.md)。

## 网页功能

- EELS-ZEST Parallel 风格的半透明卡片界面、数据概览、参数侧栏及窄屏布局，右上角显示应用版本。
- 从导入窗口直接读取 EELS-ZEST ZIP，自动验证空间标定、像素坐标和能量轴，保留缺失像素及有符号信号。
- Si／界面／Ge 选框支持移动、八方向缩放和重画。
- PCA 峰识别、高斯拟合与 CPU bootstrap 不确定度计算。
- 能窗积分、EELS 总计数、窄能窗和参考谱贡献底图，以及候选界面中心；切换底图保留选框。
- 可选 erf／logistic 衬度拟合，支持自定义区域边界百分比、预览后应用、继续手动调整，以及取消和超时恢复。
- 导出单个 `eels_peak_results_v1` JSON，默认沿用导入文件名，保存实际参数、峰结果及复现信息。
- NMF / MCR-ALS 工作流支持四区选区、条带体谱、有符号残差和联合分峰；独立导出 `eels_interface_workflow_v1` JSON，并支持导入后继续调整。
- 参数草稿保存在本机，重启后恢复适用的选项和选区；恢复参数不自动启动分析。

EELS 总计数不等同于 HAADF；候选中心、衬度过渡宽度和参考谱贡献不能直接解释为结构界面、互扩散厚度或元素含量。详见 [参考图](EELS_Python_Analysis/docs/参考图与候选界面.md) 与 [拟合选区](EELS_Python_Analysis/docs/衬度拟合选区.md)。

## PCA 绘图和重算

工作流共用上述 Python 环境。输入为网页导出的 PCA JSON，且 JSON 中关联的原 H5 路径必须可访问；JSON 不内嵌完整谱像。

```powershell
# 查看批次清单
.\eels_plot_workflow\plot_eels.ps1 -DataDir 'C:\data\pca_results' -List

# 绘图：默认组合图，峰位误差为 ±1 SD
.\eels_plot_workflow\plot_eels.ps1 -DataDir 'C:\data\pca_results'

# 保留区域、种子等配方，将最小突出度改为 0.1%，另存并重新绘图
.\eels_plot_workflow\recalculate_pca.ps1 -DataDir 'C:\data\pca_results' -OutputDir 'C:\data\pca_recalculated' -MinProminencePercent 0.1 -Plot
```

缺失误差默认留空，仅在显式使用 `-ComputeMissingUncertainty` 时补算。重算阈值会重新计算原有 bootstrap。文件命名、电流清单、选择范围、逐文件图和验证步骤见 [工作流说明](eels_plot_workflow/README.md)。

## 验证

Python 测试和工作流自检使用合成数据，不需要实验文件。PowerShell 启停测试会启动自己的临时服务：

```powershell
Push-Location .\EELS_Python_Analysis
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s tests -v
Pop-Location
.\EELS_Python_Analysis\.venv\Scripts\python.exe -B -X utf8 .\eels_plot_workflow\self_check.py
.\EELS_Python_Analysis\.venv\Scripts\python.exe -B -X utf8 .\eels_plot_workflow\self_check_recalculate.py
```

可选浏览器测试需要 Node.js、Playwright 和本机 Google Chrome。在 `EELS_Python_Analysis` 中运行：

```powershell
npm install --no-save --package-lock=false playwright
node tests/test_roi_geometry.cjs
node tests/browser_reference_maps.cjs
node tests/browser_import_settings.cjs
node tests/browser_region_fit.cjs
```

后三个浏览器测试自行创建隔离的合成数据服务。其他手动浏览器集成或性能脚本需要另设测试数据与 `EELS_TEST_URL`；不要指向正在处理实验的会话。MATLAB 转换另需在已安装 Toolbox 的环境中用自己的可信 MAT 验证。

## 数据与版本

`.gitignore` 排除实验数据、分析 JSON/CSV、导出图件、导入副本、运行日志、缓存和凭据。需要提交新的代码配置 JSON 或静态图像时，应先明确其来源并调整忽略规则。保存分析 JSON 时还需自行保存对应输入数据及程序版本；旧结果的复现可能受源码指纹、Python 和依赖版本约束。
