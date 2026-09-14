"""Reference-style PCA spectra and current plots, with honest uncertainty handling."""
from collections import Counter
import math

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MultipleLocator
from matplotlib.transforms import ScaledTranslation

import pca_data as base


ERROR_COLOR = '#46505A'


def sd_of(result, peak):
    return peak['center_std_meV'] if result['uncertainty']['status']=='computed' else None


def marker(record, sd):
    return 'D' if sd is None else ('o','s','^','v','p')[(record['export_order']-1)%5]


def label(record):
    return f"0 mA ({record['sample_id']})" if record['current_mA']==0 else (
        f"{record['current_mA']:g} mA" if record['export_order']==1 else
        f"{record['current_mA']:g} mA / {record['export_order']}")


def mark(ax, center, other, sd, *, horizontal, symbol='o', transform=None):
    color=base.PEAK_COLORS[base.peak_band(center)]
    xy=(center,other) if horizontal else (other,center)
    transform=ax.transData if transform is None else transform
    if sd is None:
        ax.plot(*xy,marker='D',ms=4.1,mfc='white',mec=color,mew=1.1,ls='none',transform=transform,zorder=6)
        return
    if not math.isfinite(sd) or sd<0:
        raise ValueError('Invalid standard deviation')
    kwargs={'xerr':sd} if horizontal else {'yerr':sd}
    artist=ax.errorbar(*xy,**kwargs,fmt=symbol,ms=4.1,mfc='white',mec=color,mew=1.1,
                      ecolor=ERROR_COLOR,elinewidth=1.05,capsize=3.5,capthick=1.05,
                      transform=transform,zorder=6)
    segment=artist.lines[2][0].get_segments()[0]
    if not np.allclose(segment[:,0 if horizontal else 1],[center-sd,center+sd],rtol=0,atol=1e-12):
        raise ValueError('Error bar endpoints differ from peak center ± SD')


def ranges(records):
    energies=[d['energy'] for r in records for d in r['regions'].values()]
    lo=min(float(e[0]) for e in energies)
    hi=max(float(e[-1]) for e in energies)
    limits=[]
    for record in records:
        for data in record['regions'].values():
            for p in data['result']['peaks']:
                s=sd_of(data['result'],p)
                limits.extend([p['center_meV']-(s or 0),p['center_meV']+(s or 0)])
    if not limits:
        limits=[lo,hi]
    return (math.floor(lo),math.ceil(hi)),(5*math.floor((min(limits)-0.5)/5),5*math.ceil((max(limits)+0.5)/5))


def arrange_annotations(ax, annotations):
    """Fit actual rendered labels, including crowded extra peaks, inside each panel."""
    for _ in range(35):
        ax.figure.canvas.draw()
        renderer=ax.figure.canvas.get_renderer()
        bounds=ax.get_window_extent(renderer)
        placed=[]
        changed=False
        for annotation in annotations:
            box=annotation.get_window_extent(renderer)
            dx,dy=annotation.get_position()
            pixel_to_pt=72/ax.figure.dpi
            if box.x0<bounds.x0+3:
                dx+=(bounds.x0+3-box.x0)*pixel_to_pt
            if box.x1>bounds.x1-3:
                dx-=(box.x1-bounds.x1+3)*pixel_to_pt
            if dx!=annotation.get_position()[0]:
                annotation.set_position((dx,dy));changed=True
                box=annotation.get_window_extent(renderer)
            if any(box.expanded(1.10,1.08).overlaps(previous) for previous in placed):
                annotation.set_position((dx,dy+14));changed=True
                box=annotation.get_window_extent(renderer)
            if box.y1>bounds.y1-5:
                bottom,top=ax.get_ylim()
                ax.set_ylim(bottom,top+0.15);changed=True
            placed.append(box)
        if not changed:
            return
    raise ValueError('Peak labels need manual layout review')


def draw_d(axes,records,*,annotate=False):
    energy_range,_=ranges(records)
    xlo,xhi=energy_range
    annotations=[]
    for ax,region in zip(axes,base.REGIONS,strict=True):
        labels=[]
        for index,record in enumerate(records):
            d=record['regions'][region]
            x,scale=d['energy'],d['normalization']
            raw,pca=d['raw']/scale,d['reconstructed']/scale
            offset=(len(records)-index-1)*1.28
            color=base.CURRENT_COLORS.get(record['current_mA'],'#568FA0')
            ax.hlines(offset,xlo,xhi,color='#DDE1E3',lw=0.6)
            ax.fill_between(x,offset,offset+pca,color=color,alpha=0.075,lw=0)
            ax.plot(x,offset+raw,color='#ADB4B8',lw=0.7)
            ax.plot(x,offset+pca,color=color,lw=1.45)
            for p in d['result']['peaks']:
                center=p['center_meV'];sd=sd_of(d['result'],p)
                y=offset+float(np.interp(center,x,pca))
                ax.vlines(center,offset+0.02,y,color=base.PEAK_COLORS[base.peak_band(center)],
                          lw=0.6,linestyles=(0,(2,2)),alpha=0.7)
                mark(ax,center,y,sd,horizontal=True)
                if annotate:
                    text=f'{center:.2f}\n'+(f'±{sd:.3f}' if sd is not None else 'SD unavailable')
                    labels.append(ax.annotate(text,(center,y),xytext=(0,10),textcoords='offset points',
                                              ha='center',va='bottom',fontsize=8,color=ERROR_COLOR))
            if not annotate:
                ax.text(xhi-0.012*(xhi-xlo),offset+0.10,label(record),ha='right',va='bottom',
                        fontsize=9.5,bbox=dict(facecolor='white',alpha=0.88,edgecolor='none',pad=0.4))
        ax.set_xlim(*energy_range)
        ax.set_ylim(-0.055,1.28*(len(records)-1)+(1.65 if annotate else 1.12))
        ax.xaxis.set_major_locator(MultipleLocator(20));ax.xaxis.set_minor_locator(MultipleLocator(5))
        ax.set_yticks([0,0.5,1] if annotate else [])
        ax.set_xlabel('Energy loss (meV)')
        ax.set_title(region,loc='right',fontweight='bold',pad=9)
        ax.tick_params(top=True,right=True)
        annotations.append((ax,labels))
    axes[0].set_ylabel('Normalized intensity (a.u.)' if annotate else 'Normalized intensity + offset (a.u.)',labelpad=12)
    if annotate:
        for ax,labels in annotations:
            arrange_annotations(ax,labels)


def draw_e(axes,records):
    _,energy_range=ranges(records)
    currents=sorted({r['current_mA'] for r in records})
    span=max(currents[-1]-currents[0],1)
    counts=Counter(r['current_mA'] for r in records)
    groups={c:[r for r in records if r['current_mA']==c] for c in currents}
    for ax,region in zip(axes,base.REGIONS,strict=True):
        for record in records:
            c=record['current_mA'];siblings=groups[c]
            shift=(siblings.index(record)-(len(siblings)-1)/2)*7 if counts[c]>1 else 0
            transform=ax.transData+ScaledTranslation(shift/72,0,ax.figure.dpi_scale_trans)
            for p in record['regions'][region]['result']['peaks']:
                sd=sd_of(record['regions'][region]['result'],p)
                mark(ax,p['center_meV'],c,sd,horizontal=False,symbol=marker(record,sd),transform=transform)
        ax.set_xlim(currents[0]-0.07*span,currents[-1]+0.07*span)
        ax.set_ylim(*energy_range)
        ax.set_xticks(currents)
        ax.set_xticklabels(['0\n(RT)' if c==0 else f'{c:g}' for c in currents])
        ax.yaxis.set_major_locator(MultipleLocator(10));ax.yaxis.set_minor_locator(MultipleLocator(5))
        ax.grid(axis='y',color='#E6E9EB',lw=0.6)
        ax.tick_params(top=True,right=True,labelsize=9.5)
        ax.set_xlabel('Applied current (mA)')
        ax.set_title(region,loc='right',fontweight='bold',pad=9)
    axes[0].set_ylabel('Peak energy (meV)',labelpad=10)
    for ax in axes[1:]:ax.tick_params(labelleft=False)


def legend(fig,records,y,kind):
    if kind=='d':
        handles=[Line2D([0],[0],color='#ADB4B8',lw=1,label='ROI mean'),
                 Line2D([0],[0],color='#327C83',lw=1.6,label='PCA reconstruction'),
                 Line2D([0],[0],color=ERROR_COLOR,marker='o',ms=4,mfc='white',lw=1,label='Peak position ±1 SD')]
    else:
        handles=[Line2D([0],[0],color=c,marker='o',mfc='white',lw=0,label=l)
                 for c,l in zip(base.PEAK_COLORS,base.PEAK_LABELS,strict=True)]
        handles.append(Line2D([0],[0],color=ERROR_COLOR,marker='_',lw=1,label='Error bars: ±1 SD'))
    if any(sd_of(d['result'],p) is None for r in records for d in r['regions'].values() for p in d['result']['peaks']):
        handles.append(Line2D([0],[0],color=ERROR_COLOR,marker='D',mfc='white',lw=0,label='SD unavailable'))
    fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(0.54,y),frameon=False,
               ncol=len(handles),fontsize=9.2,columnspacing=1.7)


def save(fig,folder,stem,formats,dpi,preview=False):
    for ext in formats:
        fig.savefig(folder/f'{stem}.{ext}',dpi=dpi,bbox_inches='tight',pad_inches=0.14)
    if preview:fig.savefig(folder/f'{stem}_preview.png',dpi=140,bbox_inches='tight',pad_inches=0.14)
    plt.close(fig)


def make_figures(folder,records,*,formats=('png','pdf','svg'),dpi=350,individual=False):
    folder.mkdir(parents=True,exist_ok=True)
    base.configure_style()
    d_height=max(5.4,1.0*len(records)+1.6)
    duplicate=len({r['current_mA'] for r in records})!=len(records)
    caption='Peak-energy uncertainty: horizontal in d, vertical in e (±1 SD). Per-spectrum maximum normalization; energy-range colors only.'
    if duplicate:
        second='Same-current exports: 1 = circles, 2 = squares, 3 = triangles. Small horizontal offsets separate points.'
        if any(sd_of(d['result'],p) is None for r in records for d in r['regions'].values() for p in d['result']['peaks']):
            second+=' Diamonds: SD unavailable.'
    else:
        second='Peak positions are taken from the selected JSON files. Small error bars may lie within the point symbols.'
    fig,axes=plt.subplots(1,3,figsize=(12.5,d_height))
    fig.subplots_adjust(left=0.075,right=0.985,top=0.85,bottom=0.14,wspace=0.17)
    draw_d(axes,records);fig.text(0.025,0.86,'d',fontsize=25,fontweight='bold');legend(fig,records,0.987,'d')
    fig.text(0.54,0.035,'Horizontal error bars: peak energy ±1 SD. Each spectrum is normalized by its PCA maximum.',ha='center',fontsize=9,color='#59636B')
    save(fig,folder,'fig_d',formats,dpi)
    fig,axes=plt.subplots(1,3,figsize=(12.5,4.9))
    fig.subplots_adjust(left=0.075,right=0.985,top=0.80,bottom=0.25,wspace=0.17)
    draw_e(axes,records);fig.text(0.025,0.81,'e',fontsize=25,fontweight='bold');legend(fig,records,0.986,'e')
    fig.text(0.54,0.074,'Vertical error bars = ±1 SD. Colors denote energy ranges; physical branch identities are not assigned.',ha='center',fontsize=9,color='#59636B')
    fig.text(0.54,0.029,second,ha='center',fontsize=8.4,color='#59636B')
    save(fig,folder,'fig_e',formats,dpi)
    height=d_height+3.7
    fig=plt.figure(figsize=(12.5,height))
    grid=fig.add_gridspec(2,3,left=0.075,right=0.985,top=0.91,bottom=1.52/height,
                         height_ratios=[max(1.35,len(records)*0.41),1],wspace=0.17,hspace=0.43)
    top=[fig.add_subplot(grid[0,i]) for i in range(3)]
    bottom=[fig.add_subplot(grid[1,i]) for i in range(3)]
    draw_d(top,records);draw_e(bottom,records)
    for panel,letter in [(top[0],'d'),(bottom[0],'e')]:
        fig.text(0.025,panel.get_position().y1+0.004,letter,fontsize=25,fontweight='bold')
    legend(fig,records,0.988,'d');legend(fig,records,0.77/height,'e')
    fig.text(0.54,0.32/height,caption,ha='center',fontsize=8.5,color='#59636B')
    fig.text(0.54,0.10/height,second,ha='center',fontsize=8.2,color='#59636B')
    save(fig,folder,'fig_de',formats,dpi,preview=True)
    if individual:
        target=folder/'individual_spectra';target.mkdir(exist_ok=True)
        for record in records:
            fig,axes=plt.subplots(1,3,figsize=(12.5,4.6))
            fig.subplots_adjust(left=0.075,right=0.985,top=0.75,bottom=0.17,wspace=0.20)
            draw_d(axes,[record],annotate=True)
            fig.suptitle(label(record)+'  |  '+record['source_h5_name'],fontsize=14,y=0.995)
            legend(fig,[record],0.917,'d')
            fig.text(0.54,0.025,'Labels and horizontal error bars: peak energy ±1 SD (meV).',ha='center',fontsize=9,color='#59636B')
            save(fig,target,f"{record['sample_id']}_pca_errorbars",formats,dpi)
