"""Draw a directory of EELS PCA JSON exports using the project's fixed d/e workflow."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode=True
import pca_data as base
from plots import make_figures, sd_of
from threadpoolctl import threadpool_limits


def parse_current(stem):
    room=re.fullmatch(r'RT(?:[-_ ]?(\d+))?',stem,re.I)
    if room:return 0.0,int(room.group(1) or 1)
    current=re.fullmatch(r'(\d+(?:\.\d+)?)(?:\s*mA)?(?:[-_]([1-9]\d*))?',stem,re.I)
    if current:return float(current.group(1)),int(current.group(2) or 1)
    raise ValueError(f'Cannot read current from {stem!r}; use --manifest with explicit file/current_mA entries')


def discover(input_dir,manifest=None):
    if manifest:
        definitions=json.loads(manifest.read_text(encoding='utf-8-sig'))['samples']
    else:
        definitions=[]
        for file in input_dir.glob('*.json'):
            doc=json.loads(file.read_text(encoding='utf-8-sig'))
            if doc.get('format')!='eels_peak_results_v1':continue
            current,order=parse_current(file.stem)
            definitions.append(dict(file=file.name,current_mA=current,export_order=order,label=file.stem))
    sources=[]
    for entry in definitions:
        file=Path(entry['file'])
        path=(input_dir/file).resolve()
        if path.suffix.lower()!='.json' or not path.is_file():raise ValueError(f'Missing JSON: {path}')
        name=entry.get('label',file.stem)
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):raise ValueError('Manifest labels must use letters, digits, dot, dash or underscore')
        current=float(entry['current_mA'])
        if not base.math.isfinite(current) or current<0:raise ValueError('Current must be finite and nonnegative')
        sources.append((name,current,int(entry.get('export_order',1)),str(path)))
    sources.sort(key=lambda row:(row[1],row[2],row[0]))
    if not sources or len({r[0] for r in sources})!=len(sources):raise ValueError('Missing or duplicate sample labels')
    if len({r[3] for r in sources})!=len(sources):raise ValueError('The same JSON is listed more than once')
    return sources


def key(name):
    return name.lower().replace('-','').replace('_','').removesuffix('.json')


def choose_three(records):
    room=next((r for r in records if key(r['sample_id']) in {'rt1','rt'}),None)
    middle=[r for r in records if r['current_mA']==5.5]
    high=[r for r in records if r['current_mA']==7]
    return [room,middle[0],high[0]] if room and len(middle)==len(high)==1 else None


def write_report(output,records,quality,report,groups,args):
    lines=['# EELS PCA 绘图结果','',
           f'输入目录：`{args.input_dir}`。共 {len(records)} 份 JSON、{len(quality)} 条区域谱线、{report["exported_peak_count"]} 个已导出峰。','',
           '## 图件','']
    for name,subset in groups:
        missing=sum(sd_of(d['result'],p) is None for r in subset for d in r['regions'].values() for p in d['result']['peaks'])
        lines.append(f'- [{name} 总图]({name}/fig_de.png)：'+', '.join(r['sample_id'] for r in subset)+f'；缺少标准差的峰数 {missing}。')
    individual_note = ('另按显式要求提供 individual_spectra 逐文件谱图。' if args.individual
                       else '本次仅生成组合图。')
    lines+=['','每组包含 fig_d（堆叠谱线）、fig_e（峰位—电流）、fig_de（合并图）和预览。默认提供 PNG/PDF/SVG。'+individual_note,'',
            '## 数据与口径','',
            '- 峰位采用 JSON 的 center_meV。误差棒为 ±1 SD：d 图横向、e 图纵向；没有放大误差，没有用峰宽代替误差。',
            '- 原始误差缺失时画空心菱形并标为 SD unavailable，CSV 中保持空白；不当作零。',
            '- 仅 --compute-missing-uncertainty 会补算缺失误差；补算结果在 uncertainty_supplements 单独记录，原 JSON 不改动。',
            '- 谱线由匹配 H5、原 ROI 和原 PCA 设置恢复；不执行高斯拟合、不增加平滑。每条谱线除以自身 PCA 最大值，堆叠偏移 1.28。灰线为 ROI 平均谱，浅色填充为整条 PCA 谱下的面积。',
            '- 三种颜色仅代表 <30、30–<48、≥48 meV 的显示区间。所有峰分别显示，不强行匹配或连接物理分支。',
            '- 同一电流有多个文件时分别保留；全量图用少量排版水平偏移避免重叠，数据电流不变。',
            '- 图 e 的能量范围自动包含全部峰位及误差端点，避免遗漏低于旧图范围的峰。',
            '- peak_positions.csv 保存峰位、SD、95% 区间、来源及 uncertainty_origin；analysis_quality.csv 保存 ROI、窗口、阈值和抽样成功数。spectra/ 保存各谱 CSV，spectra_restored.npz 为数组副本。',
            '- source_manifest.json / validation.json / run_config.json 保存来源、校验与命令参数。','',
            '## 校验','',
            f'- {len(records)} 个 H5 数值校验码与其 JSON 一致；{len(quality)} 个区域的像素数、峰数、峰位、半突出度峰宽和 PCA 残差校验通过。',
            f'- 恢复峰位与原 JSON 的最大差值：{report["max_center_difference_meV"]:.3g} meV；容差为 1e-6 meV。',
            '- 每个实际绘制的误差棒端点均校验为 center_meV ± center_std_meV。','',
            '## 不确定度来源','',
            '| 文件 | 区域 | 误差来源 | 成功/请求抽样数 |','|---|---|---|---|']
    for q in quality:
        lines.append(f'| {q["sample_id"]} | {q["region"]} | {q["uncertainty_origin"]} | {q["bootstrap_success"]}/{q["bootstrap_requested"]} |')
    lines+=['','误差仅反映所用自助抽样定义，不包含全部跨数据集的校准或预处理系统差异。检测阈值与 ROI 可能不同，请结合 analysis_quality.csv 阅读；本图不作因果归因。','']
    (output/'README_结果.md').write_text('\n'.join(lines),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--select',nargs='+',help='Exact filename stems or manifest labels; otherwise draw all plus RT1/5.5/7 when available')
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--list',action='store_true',help='Inspect inputs and missing uncertainty without restoring data')
    parser.add_argument('--compute-missing-uncertainty',action='store_true')
    parser.add_argument('--resamples',type=int,default=500)
    parser.add_argument('--block-size',type=int,default=2)
    parser.add_argument('--seed',type=int,default=20260728)
    parser.add_argument('--workers',type=int,default=0,help='0 = project CPU/memory-aware automatic limit')
    parser.add_argument('--formats',nargs='+',choices=['png','pdf','svg'],default=['png','pdf','svg'])
    parser.add_argument('--dpi',type=int,default=350)
    individual_options=parser.add_mutually_exclusive_group()
    individual_options.add_argument('--individual',action='store_true',help='Explicitly add one spectrum figure per input file')
    individual_options.add_argument('--no-individual',dest='individual',action='store_false',help='Compatibility option; combined figures are already the default')
    parser.set_defaults(individual=False)
    parser.add_argument('--resume',action='store_true',help='Resume only an existing output with identical input/configuration')
    args=parser.parse_args()
    args.input_dir=args.input_dir.resolve()
    sources=discover(args.input_dir,args.manifest)
    if args.select:
        wanted={key(s) for s in args.select}
        if not wanted <= {key(s[0]) for s in sources}:raise ValueError('Selected sample not found')
        sources=[s for s in sources if key(s[0]) in wanted]
    inventory=[]
    for sample,current,order,file in sources:
        doc=json.loads(Path(file).read_text(encoding='utf-8-sig'))
        inventory.append(dict(sample=sample,current_mA=current,file=file,
            peaks=sum(len(r['peaks']) for r in doc['results']),
            missing_sd=sum(r['uncertainty']['status']!='computed' or p['center_std_meV'] is None for r in doc['results'] for p in r['peaks'])))
    if args.list:
        print(json.dumps(inventory,ensure_ascii=False,indent=2));return
    if args.resamples<2 or args.block_size<1 or args.dpi<72:raise ValueError('Invalid bootstrap or DPI settings')
    output=(args.output_dir or args.input_dir/f'PCA绘图_{datetime.now():%Y%m%d_%H%M%S}').resolve()
    config=dict(input_dir=str(args.input_dir),sources=sources,
                input_stats=[dict(file=s[3],size=Path(s[3]).stat().st_size,mtime_ns=Path(s[3]).stat().st_mtime_ns) for s in sources],
                selection=args.select,uncertainty=dict(compute=args.compute_missing_uncertainty,resamples=args.resamples,
                block_size=args.block_size,seed=args.seed,workers=args.workers))
    config=json.loads(json.dumps(config))
    if output.exists() and any(output.iterdir()):
        saved=output/'run_config.json'
        if not args.resume or not saved.exists() or json.loads(saved.read_text(encoding='utf-8'))!=config:
            raise ValueError(f'Output exists; use a new directory, or --resume with unchanged inputs: {output}')
    output.mkdir(parents=True,exist_ok=True)
    (output/'run_config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'spectra').mkdir(exist_ok=True)
    with threadpool_limits(limits=1):
        records,peaks,quality,report=base.collect_data(args.input_dir,output,sources=sources,uncertainty=config['uncertainty'])
    groups=[('selected' if args.select else 'all_exports',records)]
    three=None if args.select else choose_three(records)
    if three is not None and len(records)>3:groups.append(('RT1_5p5_7mA',three))
    for index,(name,subset) in enumerate(groups):
        print(f'Plotting {name}: '+', '.join(r['sample_id'] for r in subset),flush=True)
        make_figures(output/name,subset,formats=args.formats,dpi=args.dpi,individual=(index==0 and args.individual))
    report['individual_spectra_enabled']=args.individual
    report['error_bar_endpoints_checked']=True
    report['missing_standard_deviation_peaks']=sum(sd_of(d['result'],p) is None for r in records for d in r['regions'].values() for p in d['result']['peaks'])
    report['output_groups']=[name for name,_ in groups]
    (output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    write_report(output,records,quality,report,groups,args)
    print(json.dumps(dict(output=str(output),files=len(records),spectra=len(quality),peaks=len(peaks),
                         missing_sd=report['missing_standard_deviation_peaks'],groups=report['output_groups']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
