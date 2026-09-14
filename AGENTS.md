# EELS 数据绘图入口

PCA JSON 绘图使用 `eels_plot_workflow/README.md` 和 `eels_plot_workflow/plot_eels.ps1`。

快速读取新批次清单：
```powershell
.\eels_plot_workflow\plot_eels.ps1 -DataDir '.\data\批次目录' -List
```
绘图使用同一命令去掉 `-List`。默认绘制全量，并在有 RT1、5.5、7 mA 时生成三组对比；`-Select` 可指定用户选择的文件。

默认仅生成组合图，不生成逐文件谱图或逐文件联系表。只有用户明确要求时才使用 `-Individual`。

需要沿用某批次参数、仅修改最小突出度并重新计算时，使用 `eels_plot_workflow/recalculate_pca.ps1`。`-MinProminencePercent 0.1` 表示 0.1%，JSON 中为 0.001；保留逐区域抽样种子，重新计算已有 bootstrap，不沿用旧误差。完整命令见流程 README。

- 保持已确认的口径：电流 mA、PCA 谱线、原有峰位、±1 SD 误差棒；d 横向，e 纵向。
- 缺失误差不得借用其他批次或当成零。需要补算时使用显式 `-ComputeMissingUncertainty`，补算结果另存；是否启用依当前用户请求决定。
- 输出结束后检查 `validation.json` 和各组主图预览；向用户报告结果路径和实质缺失项。
- 原 JSON/H5、MAT 数据及分析服务保持不变。脚本使用项目已有 Python 环境，不写全局配置或记忆。
- 这些要求限于数据绘图；修改 `EELS_Python_Analysis` 时继续遵循其内部 `AGENTS.md`。

- 仓库仅保存非敏感代码、测试与文档。不要提交实验数据、分析结果、图件、导入副本、运行日志、凭据、个人绝对路径或外部 MATLAB toolbox 源码。
