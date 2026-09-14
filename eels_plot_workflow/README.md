# EELS PCA 绘图固定流程

用于本项目的 `eels_peak_results_v1` PCA JSON。统一绘制参考图 d/e：Si、Ge、Interface 三列；d 为归一化堆叠谱与峰位横向误差棒，e 为峰位—电流与纵向误差棒。误差均为 ±1 标准差。

## 一条命令

在 `EELS-tools` 仓库根目录运行；先按根 README 创建 `EELS_Python_Analysis/.venv`：

```powershell
.\eels_plot_workflow\plot_eels.ps1 -DataDir '.\data\batch'
```

自动识别 `RT.json`、`RT1.json`、`RT2.json`、`RT-1.json`、`5.5.json`、`5.5mA.json` 等名称，RT 为 0 mA；非标准文件名使用下面的显式清单。读取当前目录的 JSON，不递归读取旧输出。

默认生成全量图；当存在 RT1（或 RT）、5.5、7 mA 时，同时生成这三组的对比图。输出位于输入目录下新的 `PCA绘图_年月日_时分秒` 文件夹，程序结束时打印完整路径。无需修改代码中的路径或峰数。

默认只生成组合图，不生成逐文件谱图或逐文件联系表。示例中的数据目录需自行提供，不随仓库分发。

## 常用选项

```powershell
# 先快速检查文件、电流、峰数和缺失误差；不恢复 H5、不写输出
.\eels_plot_workflow\plot_eels.ps1 -DataDir '.\data\batch' -List

# 仅绘制选定文件，不取 RT2/RT3
.\eels_plot_workflow\plot_eels.ps1 -DataDir '.\data\batch' -Select 'RT1','5.5','7'

# 补算缺失标准差：500 次空间分块 PCA bootstrap，结果另存
.\eels_plot_workflow\plot_eels.ps1 -DataDir '.\data\batch' -ComputeMissingUncertainty

# 自定义输出位置；存在产物时默认拒绝覆盖
.\eels_plot_workflow\plot_eels.ps1 -DataDir '.\data\batch' -OutputDir '.\data\batch\PCA新图'

# 中断后继续：必须使用完全相同的输入、选择和误差设置
.\eels_plot_workflow\plot_eels.ps1 -DataDir '.\data\batch' -OutputDir '.\data\batch\PCA新图' -Resume
```

只有用户之后明确要求逐文件谱图时，才加 `-Individual`（Python 参数为 `--individual`）；`-NoIndividual` 保留兼容，默认已关闭逐文件图。`-Workers 0` 使用现有项目的 CPU/内存约束自动选取线程数；`-Workers 8` 明确指定 8。补算参数可通过 `-Resamples`、`-BlockSize`、`-Seed` 设置，默认 500、2 像素、20260728。逐区域种子为 Si +0、Interface +1、Ge +2，与当前数据集已有 bootstrap 的规则一致。

首次绘图通常直接使用默认参数；缺失误差默认明确标注，不自动开展额外统计计算。需要补算时启用 `-ComputeMissingUncertainty`。已存在的误差直接采用，不重复计算。

## 非标准文件名

将清单保存为输入目录以外的 JSON（或使用不属于 eels_peak_results_v1 的配置文件）：

```json
{"samples": [
  {"file": "eels_results_abc.json", "current_mA": 0, "label": "RT1", "export_order": 1},
  {"file": "eels_results_def.json", "current_mA": 5.5, "label": "5.5", "export_order": 1}
]}
```

`file` 相对 `--input-dir` 解析，也可以是绝对路径。`label` 使用英文字母、数字、点、短横线或下划线；`export_order` 标识同一电流内不同文件的显示序号。

```powershell
& '.\EELS_Python_Analysis\.venv\Scripts\python.exe' -B -X utf8 '.\eels_plot_workflow\plot_eels.py' --input-dir '.\数据目录' --manifest '.\清单.json'
```

Python 入口另支持 `--formats png pdf svg`、`--dpi 350`。详细参数用 `--help` 查看。

## 输出与核验

- `all_exports/`：全量 d/e 组合图。指定选择范围时目录名为 `selected/`。
- `RT1_5p5_7mA/`：三组对比 d/e 图（如果输入齐全）。
- 各组的 `fig_de_preview.png` 用于快速目视检查；`fig_de.png/.pdf/.svg` 为交付图。
- `peak_positions.csv`、`analysis_quality.csv`、`spectra/`、`spectra_restored.npz`：峰位、误差、ROI/阈值/抽样质量以及谱线。
- `README_结果.md`、`source_manifest.json`、`validation.json`、`run_config.json`：来源、绘图口径、核验和运行配置。
- `uncertainty_supplements/`：仅补算时存在。每区域一个 JSON，记录输入校验码、原结果/配方、实际软件指纹、种子、线程数、成功/失败计数及补算结果。不是覆盖后的原导出 JSON。

恢复采用 `EELS_Python_Analysis/eelsfit` 现有函数。只对实际关联 H5 校验其已记录的数值校验码；检查 ROI 像素数、峰数、峰位、PCA 残差与半突出度峰宽。峰位容差为 1e-6 meV；失败时停止，不能套用旧数据或重拟合来掩盖差异。每条误差棒的数值端点也直接核对。

所有已有峰位不变。缺失误差保留为空，使用空心菱形和 `SD unavailable`；不得当成零、峰宽或上一轮误差。补算仅执行空间分块 PCA bootstrap，使用现有种子分流规则，不拟合高斯、不重跑已有 bootstrap。补算缓存仅在数据、原配方、设置及实际软件身份完全一致时复用。

归一化使用各自 PCA 谱的最大值，对应平均谱用同一个除数。颜色只代表能量范围，不认定跨电流物理分支；不补峰、不平均不同来源。图 e 自动包含全部峰位及误差范围。多个同电流文件在图上作少量水平排版偏移，数据中的电流保持不变。

运行后只需打开各组主图预览查看布局，不生成逐文件检查图；不必重新逐行阅读历史绘图脚本。标签有自动防重叠处理。若数字校验失败、源 H5 丢失或未知文件名，则按明确错误处理后再运行。

流程代码变更后，可用 `EELS_Python_Analysis\.venv\Scripts\python.exe -B -X utf8 eels_plot_workflow\self_check.py` 跑小型合成数据检查：文件名解析、缺失 SD、误差棒端点、补算不修改来源、缓存复用与不匹配拒绝。该检查不对用户的测量数据开展 bootstrap；测试目录限定在本目录内的 `.tmp_workflow_self_check`，结束后清理。

## 修改突出度、重算新批次

已有 JSON 里的阈值必须通过重新计算修改，不能只改 JSON 参数或复用旧峰位和误差：

```powershell
.\eels_plot_workflow\recalculate_pca.ps1 -DataDir '.\data\batch-original' -OutputDir '.\data\batch-recalculated' -MinProminencePercent 0.1 -Plot
```

`0.1` 的单位为百分数，转换后 `min_prominence_fraction = 0.001`；算法的绝对门槛为该比例乘以分析窗内 PCA 谱的强度极差（最大值减最小值）。保留每份文件各区域的 ROI、能窗、主成分数、最大峰数、最小峰间距、随机种子和已保存的抽样参数；原来有 bootstrap 的区域全部重新抽样。原来未计算误差的区域仍保留为空。

默认要求新的输出文件夹，源 JSON 和 H5 保持只读。新 JSON 使用新的结果 ID、时间戳及实际软件信息，并记录父 JSON 和参数覆盖关系。先核对原 H5 数值、原阈值峰位/峰宽/突出度/RMSE/PCA 模型，再更改阈值；更改后还检查 PCA 谱本身未变、只有允许的参数发生变化。PCA/抽样/数值核心或环境不匹配会停止；仅 IO/应用集成文件的版本差异在上述数值核验通过后记录于来源信息中。

逐文件进度与完整性记录在新目录的 `_recalculation/state.json`。中断后可用相同命令加 `-Resume`，只复用输入、软件、脚本、参数及输出摘要完全匹配的已完成文件。`-Plot` 调用上面的固定绘图流程并新建绘图输出目录。

修改重算脚本后可执行 `EELS_Python_Analysis\.venv\Scripts\python.exe -B -X utf8 eels_plot_workflow\self_check_recalculate.py`，通过小型合成弱峰数据核对阈值降低、新误差、配方保留和源文件不变。

绘图后可执行 `verify_recalculation.py --output-dir '新批次目录' --plot-dir '新批次内的绘图目录'`（使用同一 Python 环境），逐项核对父/子 JSON、抽样参数、CSV 和图件，生成 `README_重算说明.md` 和新增峰清单；最后查看各组主图的布局。

## 项目依赖与文件边界

PowerShell 入口固定使用项目现有 `.venv`，不安装全局依赖、不调用网络、不操作分析 Web 会话。使用 `-B -X utf8`，避免写入 Python 字节码；CSV 为 UTF-8 BOM，说明为 UTF-8。

原始 JSON/H5 只读。本流程代码集中在本目录；依赖同仓库的 `EELS_Python_Analysis/eelsfit`，不依赖历史绘图脚本或图件。数据或参数变化时默认创建新输出目录；不要把 `--resume` 当作跨版本覆盖选项。
