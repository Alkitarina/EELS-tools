// Optional partition previews; only the two explicit Apply actions change analysis ROIs.
const EelsWorkflowRegions=(()=>{
  const s={contrast:null,pending:null,proposal:null,coreToken:0,corePending:false,applied:{}};
  const el=id=>document.getElementById(id),value=id=>el(id).value;
  const w=()=>EelsWorkflow.state;
  const supported=()=>["interface_localization_v1","interface_y_stripes_v1"].includes(state.info?.workflow_revision);
  const expansionSupported=()=>state.info?.localization_range_version==="interface_x_expansion_v1";
  const number=id=>{if(!value(id).trim()||!Number.isFinite(Number(value(id))))throw Error("请输入有效数值。");return Number(value(id));};
  const message=(id,text,error=false)=>{el(id).textContent=text;el(id).classList.toggle("error",error);};
  const roiKey=roi=>JSON.stringify(["x_min","x_max","y_min","y_max"].map(key=>roi?.[key]));
  const sameRoi=(a,b)=>roiKey(a)===roiKey(b);
  function expansionPercent(){
    if(!expansionSupported())return 0;
    const percent=number("wf-local-expand");
    if(percent<0||percent>1000)throw Error("两侧扩展比例须为 0–1000%。");
    return percent;
  }
  function analysisDomain(){
    const roi=w().rois.Interface;if(!roi)return null;
    const percent=expansionPercent(),width=roi.x_max-roi.x_min;
    if(percent>0&&width<=0)throw Error("总界面横向宽度须大于 0，才能按比例扩展。");
    const padding=width*percent/100,requested={...roi,x_min:roi.x_min-padding,x_max:roi.x_max+padding};
    const actual={...requested,x_min:Math.max(state.info.x_nm[0],requested.x_min),x_max:Math.min(state.info.x_nm[1],requested.x_max)};
    return {percent,requested,actual,clipped:actual.x_min!==requested.x_min||actual.x_max!==requested.x_max};
  }
  function localCurrent(){
    try{return !!w().localization&&w().localization.decomposition_id===w().decomposition?.result_id&&
      sameRoi(w().localization.settings.roi,w().rois.Interface)&&
      (w().localization.settings.x_expansion_percent??0)===expansionPercent();}catch{return false;}
  }
  function contrastSettings(){return {source:"total_counts",thresholds:{si_bulk_percent:number("wf-contrast-si"),
    interface_low_percent:number("wf-contrast-low"),interface_high_percent:number("wf-contrast-high"),ge_bulk_percent:number("wf-contrast-ge")}};}
  function contrastModel(){
    try{return s.contrast?.dataset_id===state.info?.dataset_id&&s.contrast.signature===JSON.stringify(contrastSettings())?s.contrast.models[value("wf-contrast-model")]:null;}catch{return null;}
  }
  function contrastRois(fit){
    if(fit?.status!=="available")return null;
    if(value("wf-contrast-scope")==="all")return fit.rois;
    const b=fit.boundaries,x=w().decomposition?.pixel_x_nm||[],y=state.info.y_nm;
    if(b.interface_start_nm<x[0]||b.interface_end_nm>x.at(-1)||!x.some(v=>v>=b.interface_start_nm&&v<=b.interface_end_nm))return null;
    return {Interface:{x_min:b.interface_start_nm,x_max:b.interface_end_nm,y_min:y[0],y_max:y[1]}};
  }
  function cancelContrast(text="拟合预览已取消，原选框保留。"){
    if(s.pending){const pending=s.pending;s.pending=null;clearTimeout(pending.timer);pending.controller.abort();message("wf-contrast-status",text);}
    sync();
  }
  function clearProposal(){s.coreToken++;s.corePending=false;s.proposal=null;render();message("wf-core-status","候选条件已变化，请重新预览；当前选框保持不变。");}
  function reset(){cancelContrast();s.contrast=null;s.proposal=null;s.applied={};s.coreToken++;s.corePending=false;sync();
    message("wf-contrast-status","尚未拟合。总计数不等同于 HAADF，过渡比例不等同于成分比例。");
    message("wf-local-status","请先确定总界面范围，再做局部分解与候选核预览。");}
  function sync(){
    if(!el("wf-local-run"))return;
    const busy=w().busy,fit=contrastModel();
    el("wf-contrast-run").disabled=busy||!!s.pending||!w().decomposition||!w().decomposition.maps.total_counts;
    el("wf-contrast-cancel").disabled=!s.pending;
    el("wf-contrast-apply").disabled=busy||!!s.pending||!contrastRois(fit);
    let validRange=true;try{analysisDomain();}catch{validRange=false;}
    el("wf-local-expand-field").hidden=!expansionSupported();
    el("wf-local-run").disabled=busy||!supported()||!w().decomposition||!validRange;
    el("wf-local-negative-field").hidden=value("wf-local-method")!=="nmf";
    el("wf-core-preview").disabled=busy||s.corePending||!localCurrent();
    el("wf-core-apply").disabled=busy||s.corePending||!currentProposal()?.selected||!!currentProposal()?.apply_error;
  }
  async function runContrast(){
    if(w().busy||s.pending)return;
    const inputs=contrastSettings(),task={controller:new AbortController(),dataset:state.info.dataset_id,signature:JSON.stringify(inputs)};
    s.pending=task;s.contrast=null;sync();render();message("wf-contrast-status","正在拟合 erf / logistic…");
    task.timer=setTimeout(()=>{if(s.pending===task)cancelContrast("拟合响应超时，原选框保留；可重试。");},12000);
    try{
      const result=await requestApi("/api/region-fit",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(inputs),signal:task.controller.signal});
      if(s.pending!==task||task.dataset!==state.info.dataset_id||task.signature!==JSON.stringify(contrastSettings()))return;
      if(result.dataset_id!==task.dataset)throw Error("拟合响应属于其他数据，本次拟合未采用；原选框保留。");
      if(!result.models?.erf||!result.models?.logistic)throw Error("拟合响应不完整，请重试。");
      s.contrast={...result,signature:task.signature};render();EelsWorkflow.render();
    }catch(error){if(s.pending===task)message("wf-contrast-status",error.name==="AbortError"?"拟合已取消。":error.message,true);}
    finally{clearTimeout(task.timer);if(s.pending===task)s.pending=null;sync();}
  }
  async function applyContrast(){
    const fit=contrastModel();if(!contrastRois(fit)||s.pending||w().busy)return;
    const next=structuredClone(contrastRois(fit));
    if(!el("wf-contrast-full-y").checked)for(const [key,roi]of Object.entries(next)){roi.y_min=w().rois[key].y_min;roi.y_max=w().rois[key].y_max;}
    const evidence={source:"total_counts",model:fit.model,thresholds:structuredClone(s.contrast.thresholds),
      center_nm:fit.center_nm,width_10_90_nm:fit.width_10_90_nm,rmse:fit.rmse,r_squared:fit.r_squared,
      applied_rois:structuredClone(next)};
    await EelsWorkflow.applyRegionProposal(next);
    s.applied.contrast=evidence;clearProposal();
    message("wf-contrast-status","已应用拟合选框；可以继续手动调整。局部分解以该界面宽度为基准扩展。");
  }
  function settings(){return {roi:structuredClone(w().rois.Interface),
    ...(expansionSupported()?{x_expansion_percent:expansionPercent()}:{}),
    decomposition:{method:value("wf-local-method"),components:number("wf-local-rank"),
    init:value("wf-local-init"),starts:number("wf-local-starts"),seed:number("wf-local-seed"),max_iter:number("wf-local-iterations"),
    tolerance:number("wf-local-tolerance"),negative_policy:value("wf-local-negative")}};}
  function load(result){
    w().localization=result;s.proposal=null;s.coreToken++;s.corePending=false;
    const cfg=result.settings.decomposition,fields={method:"method",components:"rank",init:"init",starts:"starts",seed:"seed",max_iter:"iterations",tolerance:"tolerance",negative_policy:"negative"};
    for(const [key,suffix]of Object.entries(fields))el("wf-local-"+suffix).value=cfg[key];
    if(Object.hasOwn(result.settings,"x_expansion_percent"))el("wf-local-expand").value=result.settings.x_expansion_percent;
    el("wf-local-component").replaceChildren(...result.spectra.map((_,i)=>new Option("局部组分 "+(i+1),String(i))));
    render();message("wf-core-status","局部分解已更新，请选择组分并预览候选核。");
  }
  function proposalSettings(candidate=number("wf-core-candidate")){
    return {component:number("wf-local-component"),threshold_percent:number("wf-core-threshold"),direction:value("wf-core-direction"),
      min_pixels:number("wf-core-min-pixels"),candidate,rois:structuredClone(w().rois)};
  }
  function proposalSignature(inputs){return JSON.stringify([inputs.component,inputs.threshold_percent,inputs.direction,inputs.min_pixels,inputs.candidate,
    ...["Si","Ge","Interface"].map(name=>roiKey(inputs.rois[name]))]);}
  function currentProposal(){
    try{return localCurrent()&&s.proposal?.localization_id===w().localization.result_id&&s.proposal.signature===proposalSignature(proposalSettings())?s.proposal:null;}catch{return null;}
  }
  async function previewCore(resetChoice=true){
    if(w().busy||!localCurrent())throw Error("请先对当前界面及扩展范围完成局部分解。");
    const cfg=proposalSettings(resetChoice?0:number("wf-core-candidate")),signature=proposalSignature(cfg);
    const token=++s.coreToken,localId=w().localization.result_id;s.corePending=true;s.proposal=null;render();EelsWorkflow.render();
    message("wf-core-status","正在按完整像素权重生成候选区域…");
    try{
      const result=await EelsWorkflow.api("core-proposal",{localization_id:localId,settings:cfg});
      if(token!==s.coreToken||localId!==w().localization?.result_id||!localCurrent())return;
      if(result.localization_id!==localId)throw Error("候选响应与当前局部分解不匹配，请重新预览。");
      s.proposal={...result,signature};
      el("wf-core-candidate").replaceChildren(...(result.candidates.length?result.candidates.map((c,i)=>new Option("候选 "+(i+1)+" · "+c.pixel_count+" 像素",String(i))):[new Option("没有候选区","0")]));
      el("wf-core-candidate").value=String(cfg.candidate);render();EelsWorkflow.render();
    }catch(error){if(token===s.coreToken)message("wf-core-status",error.message,true);}
    finally{if(token===s.coreToken)s.corePending=false;sync();}
  }
  async function applyCore(){
    const proposal=currentProposal();if(!proposal?.selected||proposal.apply_error||w().busy)return;
    const local=w().localization,evidence={localization_id:local.result_id,localization_settings:structuredClone(local.settings),
      localization_summary:structuredClone(local.summary),proposal_settings:structuredClone(proposal.settings),
      selected:structuredClone(proposal.selected)};
    await EelsWorkflow.applyRegionProposal({Core:proposal.selected.roi},{coreEnabled:true,activeRegion:"Core"});
    s.applied.local=evidence;message("wf-core-status","已应用候选核矩形，普通界面将排除该框内像素；仍可在主图手动调整。");
  }
  function evidence(){
    const output=structuredClone(s.applied);
    if(output.contrast)output.contrast.manually_adjusted=Object.entries(output.contrast.applied_rois).some(([name,roi])=>!sameRoi(roi,w().rois[name]));
    if(output.local)output.local.manually_adjusted=!sameRoi(output.local.selected.roi,w().rois.Core);
    return output;
  }
  function restoreEvidence(value){s.applied=structuredClone(value||{});}
  function rect(g,roi,color,dash=[5,3]){
    if(!g||!roi)return;const a=g.p(roi.x_min,roi.y_max),b=g.p(roi.x_max,roi.y_min);
    g.ctx.save();g.ctx.strokeStyle=color;g.ctx.lineWidth=2;g.ctx.setLineDash(dash);g.ctx.strokeRect(a.x,a.y,Math.max(2,b.x-a.x),Math.max(2,b.y-a.y));g.ctx.restore();
  }
  function drawOverlay(g){
    const fit=contrastModel();if(fit?.status==="available"&&el("wf-contrast-panel").open){
      for(const key of ["interface_start_nm","interface_end_nm"]){const x=fit.boundaries[key];if(x<state.info.x_nm[0]||x>state.info.x_nm[1])continue;const a=g.p(x,state.info.y_nm[0]),b=g.p(x,state.info.y_nm[1]);
        g.ctx.save();g.ctx.strokeStyle="#dc7a2d";g.ctx.lineWidth=2;g.ctx.setLineDash([3,5]);g.ctx.beginPath();g.ctx.moveTo(a.x,a.y);g.ctx.lineTo(b.x,b.y);g.ctx.stroke();g.ctx.restore();}
    }
    if(el("wf-local-panel").open){
      if(expansionSupported()){try{rect(g,analysisDomain()?.actual,"#2563eb",[8,4]);}catch{}}
      rect(g,currentProposal()?.selected?.roi,"#a9468a");
    }
  }
  function render(){
    if(!el("wf-local-run"))return;sync();
    const fit=contrastModel(),local=w().localization;
    el("wf-contrast-preview").hidden=fit?.status!=="available";
    if(fit?.status==="available"){
      const usable=!!contrastRois(fit),detail=usable?(fit.apply_error?"总界面可应用；体区边界超出视场，当前保留原体区。":"预览完成，应用后仍可调整选框。"):(fit.apply_error||"当前边界不在图内或没有像素，请调整比例。");
      message("wf-contrast-status",fit.model+"：中心 "+fit.center_nm.toFixed(3)+" nm；10–90% 宽度 "+fit.width_10_90_nm.toFixed(3)+" nm；"+detail,!usable);
      EelsWorkflow.plot("wf-contrast-curve",fit.curve_x_nm,[{x:s.contrast.profile_x_nm,y:fit.profile_contrast,color:"#465a61",label:"总计数归一化衬度"},{y:fit.curve_contrast,color:"#2563eb",label:fit.model+" 拟合"}],"wf-contrast-legend","归一化衬度","x (nm)");
    }else if(fit)message("wf-contrast-status",fit.reason||"拟合不可用。",true);
    else if(w().decomposition&&!w().decomposition.maps.total_counts)message("wf-contrast-status","此数据没有可用的保留总计数图，请手动确定总界面区。",true);
    const roi=w().rois.Interface,cfg=w().decomposition?.settings;
    let rangeError="";
    if(roi&&cfg){
      try{
        const d=analysisDomain(),base="原总界面：x "+roi.x_min.toFixed(3)+"–"+roi.x_max.toFixed(3)+" nm；";
        el("wf-local-scope").textContent=base+(expansionSupported()?
          "左右各扩展 "+d.percent+"%，目标总宽度 "+(100+2*d.percent)+"%；局部分析 x "+d.actual.x_min.toFixed(3)+"–"+d.actual.x_max.toFixed(3)+" nm，":
          "当前旧服务按原选框局部分解，不扩展；")+
          "y "+roi.y_min.toFixed(3)+"–"+roi.y_max.toFixed(3)+" nm；沿用 "+cfg.fit_min+"–"+cfg.fit_max+" meV。"+
          (d.clipped?"扩展范围超出图像，已截到图像边缘。":"")+"分析范围包括已有位错核，原选框保持不变。";
      }catch(error){rangeError=error.message;el("wf-local-scope").textContent=rangeError;}
    }
    el("wf-local-results").hidden=!local;
    if(!supported()){message("wf-local-status","当前端口尚不支持局部分解。请运行 start_web.ps1 打开新版服务，旧端口结果继续保留。",true);return;}
    if(!local){if(rangeError)message("wf-local-status",rangeError,true);return;}
    const summary=local.summary,actual=local.settings.decomposition;
    const used=local.settings.analysis_roi||local.settings.roi;
    message("wf-local-status","已完成 "+(actual.method==="nmf"?"NMF":"MCR-ALS")+"，"+summary.valid_pixels+" 个有限像素，"+actual.components+" 个局部组分；实际分析 x "+used.x_min.toFixed(3)+"–"+used.x_max.toFixed(3)+" nm；RMSE "+summary.rmse.toPrecision(3)+"。"+(localCurrent()?"":"界面或扩展比例已变，此处仍为上次范围的预览；请重新局部分解。")+summary.warnings.join(" "),!localCurrent());
    const index=number("wf-local-component"),map=local.weights.map(row=>row.map(weights=>weights[index]));
    const g=EelsWorkflow.heatmap("wf-local-map",map,local.x_nm,local.y_nm,{aspect:true,compactTicks:true,scaleId:"wf-local-scale"});
    rect(g,local.settings.roi,"#dc7a2d");
    EelsWorkflow.plot("wf-local-spectra",local.energy_meV,local.spectra.map((y,i)=>({y,color:componentColors[i%componentColors.length],label:"局部组分 "+(i+1),width:i===index?3:1})),"wf-local-legend","单位面积谱");
    const proposal=currentProposal();
    el("wf-core-spectra-preview").hidden=!proposal?.selected;
    if(proposal){
      const selected=proposal.selected;
      message("wf-core-status",selected?"候选 "+(proposal.settings.candidate+1)+"：连通区域 "+selected.pixel_count+" 像素；外接矩形 "+selected.rectangle_pixels+" 像素，额外包含 "+selected.extra_rectangle_pixels+" 像素。"+(proposal.apply_error||"核对预览后应用，矩形仍可手动调整。")+(proposal.candidate_count>50?" 仅列出最大的 50 个候选区。":""):proposal.apply_error,!!proposal.apply_error);
      if(g&&selected){
        g.ctx.save();g.ctx.fillStyle="rgba(169,70,138,.7)";
        proposal.preview_mask.forEach((row,iy)=>row.forEach((yes,ix)=>{if(yes){const p=g.p(local.x_nm[ix],local.y_nm[iy]);g.ctx.fillRect(p.x-2,p.y-2,4,4);}}));g.ctx.restore();rect(g,selected.roi,"#a9468a");
        EelsWorkflow.plot("wf-core-spectra",proposal.energy_meV,Object.entries(proposal.region_spectra).filter(([,r])=>r).map(([name,r])=>({y:r.spectrum,color:name==="Core"?"#a9468a":"#dc7a2d",label:(name==="Core"?"候选核矩形":"其余总界面")+" · "+r.pixel_count+" 像素"})),"wf-core-legend");
      }
    }
  }
  function bind(){
    const on=(id,event,fn)=>el(id).addEventListener(event,()=>Promise.resolve().then(fn).catch(error=>message(id.startsWith("wf-contrast")?"wf-contrast-status":"wf-core-status",error.message,true)));
    on("wf-contrast-run","click",runContrast);on("wf-contrast-cancel","click",()=>cancelContrast());on("wf-contrast-apply","click",applyContrast);
    for(const id of ["wf-contrast-si","wf-contrast-low","wf-contrast-high","wf-contrast-ge"])on(id,"input",()=>{cancelContrast();s.contrast=null;render();EelsWorkflow.render();message("wf-contrast-status","边界设置已修改，请重新拟合预览；选框保持不变。");});
    on("wf-contrast-model","change",()=>{render();EelsWorkflow.render();});
    on("wf-contrast-scope","change",render);
    on("wf-local-method","change",sync);on("wf-local-run","click",()=>EelsWorkflow.run("localization"));
    on("wf-local-expand","input",()=>{clearProposal();EelsWorkflow.render();});
    for(const id of ["wf-local-component","wf-core-direction","wf-core-threshold","wf-core-min-pixels"])on(id,id.includes("threshold")||id.includes("min-pixels")?"input":"change",()=>{clearProposal();EelsWorkflow.render();});
    on("wf-core-preview","click",()=>previewCore());on("wf-core-candidate","change",()=>previewCore(false));on("wf-core-apply","click",applyCore);
    for(const id of ["wf-contrast-panel","wf-local-panel"])on(id,"toggle",()=>{render();EelsWorkflow.render();});sync();
  }
  return {bind,reset,sync,render,load,settings,evidence,restoreEvidence,drawOverlay,state:s};
})();
