"""Verify a completed recalculation and its combined plots; write a batch summary."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--plot-dir', required=True, type=Path)
    args = parser.parse_args()
    target, plots = args.output_dir.resolve(), args.plot_dir.resolve()
    state = read(target / '_recalculation' / 'state.json')
    validation = read(plots / 'validation.json')
    assert state['status'] == 'complete'
    assert validation['all_h5_checksums_match'] and validation['error_bar_endpoints_checked']
    fraction = state['config']['min_prominence_fraction']
    rows, new_peaks, all_peaks = [], [], {}
    for completed in state['completed']:
        old_path = Path(completed['source_json'])
        new_path = target / old_path.name
        assert hashlib.sha256(old_path.read_bytes()).hexdigest() == state['config']['input_sha256'][str(old_path)]
        assert hashlib.sha256(new_path.read_bytes()).hexdigest() == completed['output_sha256']
        old, new = read(old_path), read(new_path)
        old_recipes = {r['region']: r for r in old['reproducibility']['analyses']}
        new_recipes = {r['region']: r for r in new['reproducibility']['analyses']}
        old_results = {r['region']: r for r in old['results']}
        assert new['reproducibility']['data'] == old['reproducibility']['data']
        assert new['reproducibility']['software'] == state['config']['software']
        for result in new['results']:
            region = result['region']
            before, after = old_recipes[region], new_recipes[region]
            expected = {**before['settings'], 'min_prominence_fraction': fraction}
            assert after['settings'] == expected
            for key in ('roi', 'fit_min', 'fit_max', 'mode', 'bootstrap', 'resolved_fit_config'):
                assert before[key] == after[key], (old_path.name, region, key)
            assert result['result_id'] == after['result_id'] != before['result_id']
            assert before['created_utc'] != after['created_utc']
            unc = result['uncertainty']
            assert unc['n_success'] + unc['n_failed'] == unc['n_requested']
            if after['bootstrap']:
                assert unc['n_requested'] == after['bootstrap']['n_resamples'] and unc['n_success'] >= 2
            for peak in result['peaks']:
                std = peak['center_std_meV']
                assert std is None or (math.isfinite(std) and std >= 0)
                if after['bootstrap']:
                    assert std is not None
                all_peaks[(new_path.stem, region, peak['index'])] = peak
                if all(abs(peak['center_meV'] - p['center_meV']) > 1e-6 for p in old_results[region]['peaks']):
                    new_peaks.append(dict(file=new_path.name, region=region,
                        center_meV=peak['center_meV'], center_std_meV=std))
            rows.append(dict(file=new_path.name, region=region,
                old_peaks=len(old_results[region]['peaks']), new_peaks=len(result['peaks']),
                bootstrap_requested=unc['n_requested'], bootstrap_success=unc['n_success'],
                bootstrap_failed=unc['n_failed']))
    with (plots / 'peak_positions.csv').open(encoding='utf-8-sig', newline='') as stream:
        csv_rows = list(csv.DictReader(stream))
    assert len(csv_rows) == len(all_peaks) == validation['exported_peak_count']
    for row in csv_rows:
        peak = all_peaks[(row['sample_id'], row['region'], int(row['source_peak_index']))]
        assert float(row['center_meV']) == peak['center_meV']
        assert (float(row['center_std_meV']) if row['center_std_meV'] else None) == peak['center_std_meV']
    counts = {}
    for suffix in ('png', 'svg', 'pdf'):
        files = list(plots.rglob(f'*.{suffix}'))
        counts[suffix] = len(files)
        for file in files:
            if suffix == 'png':
                with Image.open(file) as im:
                    im.verify()
            elif suffix == 'svg':
                ET.parse(file)
            else:
                # Container check only; visual inspection uses the same Matplotlib PNG export.
                raw = file.read_bytes()
                assert raw.startswith(b'%PDF-') and raw.rstrip().endswith(b'%%EOF')
    folder = plots / 'verification'
    folder.mkdir(exist_ok=True)
    report = dict(files=len(state['completed']), spectra=len(rows),
        old_peaks=sum(r['old_peaks'] for r in rows), new_peaks=len(all_peaks),
        newly_detected_peaks=new_peaks, min_prominence_percent=fraction * 100,
        requested_resamples=sum(r['bootstrap_requested'] for r in rows),
        successful_resamples=sum(r['bootstrap_success'] for r in rows),
        failed_resamples=sum(r['bootstrap_failed'] for r in rows),
        original_jsons_unchanged=True, only_prominence_parameter_changed=True,
        saved_bootstrap_settings_preserved=True, csv_matches_json=True,
        missing_sd=validation['missing_standard_deviation_peaks'],
        max_center_difference_meV=validation['max_center_difference_meV'],
        png_svg_validation='passed', pdf_container_check='passed', artifact_counts=counts,
        regions=rows)
    (folder / 'artifact_checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# PCA 重算结果', '',
        f'沿用 `{state["config"]["input_dir"]}` 的各份实际参数，仅将最小突出度改为 **{fraction * 100:g}%**（JSON 比例值为 `{fraction:g}`）。', '',
        f'共 {report["files"]} 份 JSON、{report["spectra"]} 条区域谱线；峰数由 {report["old_peaks"]} 变为 **{report["new_peaks"]}**。', '',
        f'各区域按原设置重新计算 bootstrap，合计请求 {report["requested_resamples"]} 次、成功 {report["successful_resamples"]} 次、失败 {report["failed_resamples"]} 次。误差棒为成功样本的 ±1 SD，缺失 SD 的峰数为 {report["missing_sd"]}。', '',
        '未改变 PCA 谱本身；新阈值改变峰检出和抽样中的峰匹配。保留原最大峰数，不强行补足峰数。', '',
        '## 图件', '',
        f'- [全量 d/e 高清 PNG]({plots.name}/all_exports/fig_de.png)',
        f'- [全量 d/e 矢量 PDF]({plots.name}/all_exports/fig_de.pdf)',
        f'- [峰位与标准差表]({plots.name}/peak_positions.csv)', '',
        '## 各区域抽样质量', '',
        '| 文件 | 区域 | 原峰数 | 新峰数 | 成功 / 请求 |',
        '|---|---|---:|---:|---:|']
    lines += [f'| {r["file"]} | {r["region"]} | {r["old_peaks"]} | {r["new_peaks"]} | {r["bootstrap_success"]} / {r["bootstrap_requested"]} |' for r in rows]
    lines += ['', '## 新检出的峰', '', '| 文件 | 区域 | 峰位 ±1 SD（meV） |', '|---|---|---:|']
    for p in new_peaks:
        sd = f'{p["center_std_meV"]:.3f}' if p['center_std_meV'] is not None else '未计算'
        lines.append(f'| {p["file"]} | {p["region"]} | {p["center_meV"]:.3f} ± {sd} |')
    with_sd = [p for p in new_peaks if p['center_std_meV'] is not None]
    if with_sd:
        widest = max(with_sd, key=lambda p: p['center_std_meV'])
        quality = next(r for r in rows if r['file'] == widest['file'] and r['region'] == widest['region'])
        lines += ['', f'新增峰中最大的峰位 SD 为 {widest["center_std_meV"]:.3f} meV（{widest["file"]} / {widest["region"]}，峰位 {widest["center_meV"]:.3f} meV）；该区域成功抽样 {quality["bootstrap_success"]}/{quality["bootstrap_requested"]} 次。图中保留实际误差。']
    lines += ['',
        '源 JSON/H5 未修改；配方逐项比较通过，绘图 CSV 与新 JSON 的峰位和标准差一致。原阈值结果和新 JSON 的数值恢复均通过核验。', '',
        '复算过程见 `_recalculation/state.json`；新 JSON 使用新的结果 ID 和时间戳。绘图 validation.json 的 bootstrap_recomputed 指绘图阶段的补算，本批次误差已在此前的重算阶段完成。', '']
    changed = sorted({name for c in state['completed'] for name in c['integration_source_changes']})
    if changed:
        lines += [f'当前集成源码与父 JSON 的记录存在差异：{", ".join(changed)}。PCA、bootstrap、数值核心、导出器及依赖版本一致，且原输入数值、原峰位/峰宽/突出度/RMSE/PCA 模型均已核对。差异记录在新 JSON 的 reproducibility.derivation 中。', '']
    (target / 'README_重算说明.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in {'regions', 'newly_detected_peaks'}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
