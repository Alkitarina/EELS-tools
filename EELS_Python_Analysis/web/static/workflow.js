const EelsWorkflow = (() => {
  const w = {dataset: null, decomposition: null, profiles: null, fit: null, preview: null,
    fits: {}, fitInputs: {}, activeTarget: "Interface", defaultFit: null, localization:null,
    roiPreview: null, strip: null, rois: {}, step: 1, region: "Si", busy: false, job: null,
    request: 0, drag: null, geometry: null, structure: null, reference: null, restored: false, restoring: false,
    seeds: [], seedIndex: 0, seedMap: null, seedPreview: null, seedGeometry: null, seedDrag: null, seedRequest: 0, seedMapRequest: 0};
  const colors = {Si: "#3976b5", Ge: "#438b58", Interface: "#dc7a2d", Core: "#a9468a"};
  const labels = {Si: "Si bulk", Ge: "Ge bulk", Interface: "普通界面", Core: "位错核附近"};
  const el = id => document.getElementById(id);
  const val = id => el(id).value;
  const num = id => {const n = Number(val(id)); if (!Number.isFinite(n)) throw Error("请输入有效数值。"); return n;};
  const checked = id => el(id).checked;
  const interfaceStripesSupported=()=>state.info?.workflow_revision==="interface_y_stripes_v1";
  const independentStripsSupported=()=>state.info?.strip_data_version==="independent_full_x_v1";
  const amplitudeLimitSupported=()=>state.info?.amplitude_limit_version==="fixed_initial_local_extremum_v1";
  const candidateScoreSupported=()=>state.info?.candidate_score_version==="zero_baseline_prominence_v1";
  const regionFitsSupported=()=>["bulk_residual_regions_v1","interface_localization_v1","interface_y_stripes_v1"].includes(state.info?.workflow_revision);
  const roiSupported=()=>["roi_init_signed_peaks_v1","bulk_residual_regions_v1","interface_localization_v1","interface_y_stripes_v1"].includes(state.info?.workflow_revision);
  const isBulkTarget=(target=val("wf-target"))=>regionFitsSupported()&&["Si","Ge"].includes(target);
  const resultKey=result=>result.selection.kind==="region"?result.selection.region:"strip";
  const visible = () => !el("workflow-workspace").hidden;
  const fmt = (n, digits=3) => n == null || !Number.isFinite(n) ? "—" : Number(n).toPrecision(digits);
  const extent = values => {let min=Infinity,max=-Infinity;for(const value of values){if(value!=null&&Number.isFinite(value)){min=Math.min(min,value);max=Math.max(max,value);}}return [min,max];};

  function status(text, error=false) {
    el("wf-status").textContent = text; el("wf-status").classList.toggle("error", error);
    if(!hasDataset()){el("empty-message").textContent=text;el("empty-message").classList.toggle("error",error);}
  }
  async function api(path, payload, timeout=20000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch("/api/workflow/" + path, {
        method: payload === undefined ? "GET" : "POST", signal: controller.signal,
        headers: {"Content-Type": "application/json", "X-EELS-Dataset": state.info.dataset_id},
        ...(payload === undefined ? {} : {body: JSON.stringify(payload)})});
      const body = await response.json();
      if (!response.ok) throw Error(body.error || "工作流请求失败。");
      return body;
    } catch (error) {
      throw Error(error.name === "AbortError" ? "请求超时；计算任务如已启动仍可取消或重新连接查看。" : error.message);
    } finally {clearTimeout(timer);}
  }
  function setRunning(busy) {
    w.busy = busy; setBusy(busy);
    el("workflow-workspace").querySelectorAll("input, select, textarea, .wf-step button, .wf-export button").forEach(node => {node.disabled = busy;});
    el("wf-cancel").disabled = !busy || !w.job;
    el("empty-cancel").hidden = el("empty-progress").hidden = !busy || !w.job;
    if (!busy) syncOptions();
  }
  function defaults(info) {
    const [a,b] = info.x_nm, [c,d] = info.y_nm, dx=b-a, dy=d-c;
    return {Si: {x_min:a,x_max:a+.25*dx,y_min:c,y_max:d},
      Ge: {x_min:a+.75*dx,x_max:b,y_min:c,y_max:d},
      Interface: {x_min:a+.38*dx,x_max:a+.62*dx,y_min:c,y_max:d},
      Core: {x_min:a+.47*dx,x_max:a+.53*dx,y_min:c+.35*dy,y_max:c+.65*dy}};
  }
  function resetDataset(info, preserve) {
    if (w.dataset === info.dataset_id) return;
    w.dataset = info.dataset_id; w.request++; w.job = null; w.restored = false;
    el("import-workflow").disabled=info.workflow_import_version!=="saved_workflow_json_v1";
    el("import-workflow").title=info.workflow_import_version?"导入保存的界面工作流并继续调整":"请通过 start_web.ps1 更新服务后使用流程导入";
    w.decomposition = w.profiles = w.fit = w.preview = w.roiPreview = w.strip = null;
    w.localization=null;EelsWorkflowRegions.reset();
    w.fits={};
    w.structure = null; w.reference = null;
    w.seedMap=w.seedPreview=w.seedGeometry=null;w.seedRequest++;w.seedMapRequest++;
    el("wf-structure-confirmed").checked = false;
    el("wf-structure-file").value = ""; el("wf-reference-file").value = "";
    el("wf-preprocessed").checked = false;
    if(info.dataset_loaded===false){w.rois={};w.seeds=[];w.fitInputs={};return;}
    const initial = defaults(info);
    if (!preserve || !Object.keys(w.rois).length) w.rois = initial;
    else for (const name of Object.keys(initial)) {
      const r = w.rois[name];
      if (r.x_min < info.x_nm[0] || r.x_max > info.x_nm[1] || r.y_min < info.y_nm[0] || r.y_max > info.y_nm[1]) w.rois[name] = initial[name];
    }
    for(let i=0;i<8;i++) {
      const prior=w.seeds[i],r=prior?.roi;
      if(!preserve||!r||r.x_min<info.x_nm[0]||r.x_max>info.x_nm[1]||r.y_min<info.y_nm[0]||r.y_max>info.y_nm[1])
        w.seeds[i]={enabled:i<2,roi:structuredClone(initial[i===0?"Si":i===1?"Ge":"Interface"])};
    }
    syncSeedChoices();syncSeedInputs();
    const enhanced=roiSupported();
    el("wf-init").querySelector('[value="roi"]').disabled=!enhanced;
    el("wf-polarity").disabled=!enhanced;
    el("wf-feature-note").hidden=regionFitsSupported();
    el("wf-feature-note").textContent="当前端口 "+location.port+" 尚未加载四区体谱 / 残差分峰与汇总导出。请运行 start_web.ps1 打开新版端口；旧服务的已有结果继续保留。";
    for(const name of ["Si","Ge"])el("wf-target").querySelector('[value="'+name+'"]').textContent=name+(regionFitsSupported()?" 体区 · 高斯分峰":" 体区（旧版自检）");
    if(!enhanced){if(val("wf-init")==="roi")el("wf-init").value="nndsvda";el("wf-polarity").value="positive";}
    if (!preserve) {
      el("wf-min").value = Math.max(info.energy_meV[0], 10);
      el("wf-max").value = Math.min(info.energy_meV[1], 80);
      if (num("wf-min") >= num("wf-max")) {el("wf-min").value=info.energy_meV[0]; el("wf-max").value=info.energy_meV[1];}
      el("wf-stripe-rows").value = Math.min(4, info.shape.y);
    }
    el("wf-stripe-rows").max = info.shape.y;
    syncRoiInputs(); syncOptions();
    for (const id of ["wf-decomp-summary","wf-profiles-summary","wf-diagnostics","wf-comparison","wf-fit-table","wf-trials"]) el(id).textContent = "";
    status("新数据已载入；请重新确认预处理状态。已有参数保留，结果需重新计算。");
    if (visible()) showStep(1);
  }
  async function open() {
    showStep(w.step);
    if (w.restored || w.restoring || !state.info?.workflow_version) return;
    w.restoring = true;setRunning(true);status("正在恢复此服务中的工作流结果与参数…");
    try {
      const saved = await api("state");
      if (saved.result_ids.decomposition) {
        for (const stage of ["decomposition","profiles"]) if (saved.result_ids[stage]) await loadResult(stage, saved.result_ids[stage]);
        if(saved.result_ids.localization)await loadResult("localization",saved.result_ids.localization);
        const fitIds=new Set([...Object.values(saved.fit_result_ids||{}),...(saved.result_ids.fit?[saved.result_ids.fit]:[])]);
        for(const id of fitIds)await loadResult("fit",id,false);
        const cfg = w.decomposition.settings;
        const fields = {method:"wf-method",components:"wf-rank",fit_min:"wf-min",fit_max:"wf-max",init:"wf-init",starts:"wf-starts",seed:"wf-seed",max_iter:"wf-iterations",tolerance:"wf-tolerance",negative_policy:"wf-negative"};
        Object.entries(fields).forEach(([key,id]) => {el(id).value = cfg[key];});
        if(cfg.init==="roi") {
          w.seeds.forEach(seed=>{seed.enabled=false;});
          for(const seed of cfg.seed_rois||[])w.seeds[seed.component]={enabled:true,roi:structuredClone(seed.roi)};
          el("wf-seed-negative").value=cfg.seed_negative_policy||"reject";syncSeedChoices();syncSeedInputs();
        }
        el("wf-preprocessed").checked = cfg.preprocessing_confirmed;
        if (w.profiles) {w.rois=structuredClone(w.profiles.settings.rois); restoreProfileInputs(w.profiles.settings); syncRoiInputs();}
        else if(w.localization){w.rois.Interface=structuredClone(w.localization.settings.roi);syncRoiInputs();}
        w.fit=Object.values(w.fits).find(fit=>fit.result_id===saved.result_ids.fit)||null;w.preview=w.fit;
        if (w.fit) restoreFitInputs(w.fit.settings);
        status("已恢复此服务中完成的工作流结果；切换方法保留结果与参数。");
        render();
      }
      w.restored=true;
      if (saved.active_job) {w.job=saved.active_job.job_id;poll();}
      else if(!saved.result_ids.decomposition)status(hasDataset()?"请确认预处理状态与可靠能量窗，再运行分解。":"当前未加载文件。启动时不会自动读取上次的数据。");
    } catch (error) {w.restored=false; status(error.message,true);}
    finally {w.restoring=false;setRunning(Boolean(w.job));}
  }
  function showStep(step) {
    finishSeedDrag(true);
    w.step=Number(step);
    el("workflow-workspace").querySelectorAll(".wf-step").forEach(node=>{node.hidden=Number(node.dataset.step)!==w.step;});
    el("workflow-workspace").querySelectorAll("[data-wf-step]").forEach(node=>{
      if (Number(node.dataset.wfStep)===w.step) node.setAttribute("aria-current","step"); else node.removeAttribute("aria-current");});
    if (w.step > 1 && !w.decomposition) status("请先在第 1 步完成全像素分解。");
    else if (w.step===5 && !w.profiles) status("请先在第 4 步确认分区并建立体谱。");
    requestAnimationFrame(render);
  }
  function syncOptions() {
    const oldScope=el("wf-bulk-scope").querySelector('[value="per_strip"]');
    oldScope.disabled=oldScope.hidden=independentStripsSupported();
    if(independentStripsSupported())el("wf-bulk-scope").value="global";
    el("wf-negative-field").hidden = val("wf-method")!=="nmf";
    el("wf-seed-panel").hidden=val("wf-init")!=="roi";
    el("wf-polarity").disabled=w.busy||!roiSupported();
    el("wf-amplitude-enabled").disabled=w.busy||!amplitudeLimitSupported();
    el("wf-amplitude-percent").disabled=w.busy||!amplitudeLimitSupported();
    el("wf-amplitude-unsupported").hidden=amplitudeLimitSupported();
    if(state.info&&!amplitudeLimitSupported())el("wf-amplitude-enabled").checked=false;
    el("wf-template-fields").hidden = val("wf-template-kind")!=="parametric";
    document.querySelectorAll(".wf-strip-selection").forEach(node=>{node.hidden=val("wf-target")!=="strip";});
    for (const id of ["wf-region","wf-target"]) el(id).querySelector('[value="Core"]').disabled = !checked("wf-core-enabled");
    if (!checked("wf-core-enabled") && val("wf-region")==="Core") {el("wf-region").value="Interface"; w.region="Interface"; syncRoiInputs();}
    if (!checked("wf-core-enabled") && val("wf-target")==="Core") el("wf-target").value="Interface";
    const bulk=isBulkTarget();
    el("wf-candidate-policy").hidden=bulk;
    el("wf-candidate-policy").textContent=candidateScoreSupported()?"新残差拟合按 min(突出度, 相对零线的绝对峰高) 筛选并排序；最小突出度百分比乘残差的峰峰值，作为评分下限。两侧异号峰不能抬高浅谷评分。手动峰位不参与自动筛选。":"当前服务使用历史突出度筛选规则；请通过 start_web.ps1 更新后端后使用零线约束评分。";
    el("wf-polarity").closest("label").hidden=bulk;
    el("wf-peak-shape").disabled=w.busy||bulk;
    if(bulk){el("wf-peak-shape").value="gaussian";el("wf-polarity").value="positive";}
    el("wf-fit-regions").disabled=w.busy||!regionFitsSupported();
    el("wf-preview").textContent=bulk?"预览体区平均谱":"预览体谱贡献与残差";
    el("wf-fit").textContent=bulk?"对当前体区做高斯分峰":"提取候选峰并交叉拟合";
    el("wf-analysis-title").textContent=bulk?"对 "+val("wf-target")+" 体区平均谱做高斯分峰":"检查残差来源，再做候选峰和联合拟合";
    el("wf-spectrum-title").textContent=bulk?"体区平均谱与高斯拟合":"实测谱与 Si + Ge 贡献";
    el("wf-components-title").textContent=bulk?"高斯子峰":"有符号残差与候选线型";
    el("wf-analysis-help").textContent=bulk?"直接拟合所选体区的原始已处理平均谱。预览总拟合与各高斯子峰；结果独立保存，不自动替换第 4 步的扣谱模板。":"普通界面和位错核使用全 y 体区模板；条带目标使用第 4 步选定的体谱范围。";
    el("wf-residual-help").hidden=bulk;
    syncStripColors();
    EelsWorkflowRegions.sync();
  }
  function coreStripOverlapRows(profiles) {
    const roi=profiles.settings.core_enabled?profiles.settings.rois.Core:null;
    const y=w.decomposition?.pixel_y_nm||state.signalMap?.y_nm||[];
    if(!roi)return profiles.strips.map(()=>0);
    // X displacement is irrelevant; use original y pixel centers, never display downsampling.
    return profiles.strips.map(strip=>strip.core_overlap_rows??y.slice(strip.row_start,strip.row_stop).filter(value=>value>=roi.y_min&&value<=roi.y_max).length);
  }
  function syncStripColors() {
    for(const id of ["wf-strip-view","wf-target-strip"]){
      const select=el(id);
      for(const option of select.options){
        const count=w.profiles?Number(option.dataset.coreRows||0):0;
        option.classList.toggle("wf-core-strip",count>0);
        option.textContent=option.dataset.stripLabel+(count>0?" · 涉及位错核":"");
        option.title=count>0?"与位错核 y 范围重叠 "+count+" 行像素；不检查 x 位置":"";
      }
      const selected=select.selectedOptions[0];
      select.classList.toggle("wf-core-strip",!!selected?.classList.contains("wf-core-strip"));
      select.title=selected?.title||"";
    }
  }
  function decompositionSettings() {
    return {method:val("wf-method"),components:num("wf-rank"),fit_min:num("wf-min"),fit_max:num("wf-max"),
      init:val("wf-init"),starts:num("wf-starts"),seed:num("wf-seed"),max_iter:num("wf-iterations"),tolerance:num("wf-tolerance"),
      negative_policy:val("wf-negative"),preprocessing_confirmed:checked("wf-preprocessed"),
      ...(val("wf-init")==="roi"?{seed_negative_policy:val("wf-seed-negative"),seed_rois:w.seeds.slice(0,num("wf-rank")).flatMap((seed,i)=>seed.enabled?[{component:i,roi:structuredClone(seed.roi)}]:[])}:{})};
  }
  function profileSettings() {
    let anchors=[];
    if (checked("wf-align")) anchors=val("wf-anchors").trim().split(/\n/).map(line=>{
      const values=line.trim().split(/[\s,，;]+/).map(Number);
      if(values.length!==2||!values.every(Number.isFinite))throw Error("界面位置每行须为两个数值：y_nm, x0_nm。");return values;});
    return {rois:structuredClone(w.rois),regions_confirmed:true,core_enabled:checked("wf-core-enabled"),
      stripe_rows:num("wf-stripe-rows"),bulk_scope:val("wf-bulk-scope"),
      ...(interfaceStripesSupported()?{stripe_y_scope:"interface_roi"}:{}),
      ...(independentStripsSupported()?{strip_data_rule:"independent_full_x_v1"}:{}),
      template:{kind:val("wf-template-kind"),shape:val("wf-template-shape"),peak_count:num("wf-template-peaks"),
        fwhm_min:num("wf-template-low"),fwhm_max:num("wf-template-high")},alignment:{enabled:checked("wf-align"),anchors},
      evidence:{note:val("wf-region-evidence"),structure_name:w.structure?.name||null,
        structure_extent_confirmed:checked("wf-structure-confirmed"),flip_x:checked("wf-image-flipx"),flip_y:checked("wf-image-flipy"),
        reference_name:w.reference?.name||null,background:val("wf-background"),partition_assistance:EelsWorkflowRegions.evidence()}};
  }
  function restoreProfileInputs(s) {
    el("wf-core-enabled").checked=s.core_enabled; el("wf-stripe-rows").value=s.stripe_rows;
    el("wf-bulk-scope").value=s.bulk_scope; el("wf-template-kind").value=s.template.kind;
    el("wf-template-shape").value=s.template.shape||"gaussian";el("wf-template-peaks").value=s.template.peak_count||2;
    el("wf-template-low").value=s.template.fwhm_min||1;el("wf-template-high").value=s.template.fwhm_max||20;
    el("wf-align").checked=s.alignment.enabled;
    el("wf-anchors").value=(s.alignment.anchors||[]).map(pair=>pair.join(", ")).join("\n");
    el("wf-region-evidence").value=s.evidence?.note||"";EelsWorkflowRegions.restoreEvidence(s.evidence?.partition_assistance);syncOptions();
  }
  function selection(target=val("wf-target")) {
    return target==="strip" ? {kind:"strip",strip:num("wf-target-strip"),x_min:num("wf-target-min"),x_max:num("wf-target-max")} : {kind:"region",region:target};
  }
  function restoreFitInputs(settings) {
    const target=settings.selection||{};el("wf-target").value=target.kind==="strip"?"strip":target.region||"Interface";
    w.activeTarget=val("wf-target");
    if(target.kind==="strip"){el("wf-target-strip").value=target.strip;el("wf-target-min").value=target.x_min;el("wf-target-max").value=target.x_max;}
    const fields={shape:"wf-peak-shape",peak_count:"wf-peak-count",distance:"wf-distance",center_radius:"wf-center-radius",fwhm_min:"wf-peak-low",fwhm_max:"wf-peak-high"};
    Object.entries(fields).forEach(([key,id])=>{if(settings[key]!=null)el(id).value=settings[key];});
    el("wf-prominence").value=(settings.prominence??.03)*100;el("wf-centers").value=(settings.centers||[]).join(", ");syncOptions();
    el("wf-polarity").value=settings.polarity||"positive";
    el("wf-amplitude-enabled").checked=!!settings.amplitude_limit;
    el("wf-amplitude-percent").value=(settings.amplitude_limit?.factor??1.5)*100;
  }
  function fitSettings(target=val("wf-target")) {
    const centers=val("wf-centers").trim();
    const bulk=isBulkTarget(target);
    return {selection:selection(target),analysis_kind:bulk?"bulk_gaussian":"residual",shape:bulk?"gaussian":val("wf-peak-shape"),polarity:bulk?"positive":val("wf-polarity"),peak_count:num("wf-peak-count"),prominence:num("wf-prominence")/100,
      distance:num("wf-distance"),center_radius:num("wf-center-radius"),fwhm_min:num("wf-peak-low"),fwhm_max:num("wf-peak-high"),
      centers:centers?centers.split(/[\s,，;]+/).map(Number):null,
      ...(!bulk&&candidateScoreSupported()?{candidate_score:"zero_baseline_prominence_v1"}:{}),
      amplitude_limit:checked("wf-amplitude-enabled")?{method:"fixed_initial_local_extremum_v1",factor:num("wf-amplitude-percent")/100}:null};
  }
  function settingsForTarget(target) {
    const cfg=structuredClone(w.fitInputs[target]||w.fits[target]?.settings||w.defaultFit);
    cfg.selection=target==="strip"?(cfg.selection?.kind==="strip"?cfg.selection:selection(target)):{kind:"region",region:target};
    cfg.analysis_kind=isBulkTarget(target)?"bulk_gaussian":"residual";
    if(isBulkTarget(target)){cfg.shape="gaussian";cfg.polarity="positive";}
    if(!isBulkTarget(target)&&candidateScoreSupported())cfg.candidate_score="zero_baseline_prominence_v1";
    else delete cfg.candidate_score;
    if(!amplitudeLimitSupported())delete cfg.amplitude_limit;
    return cfg;
  }
  function selectFitTarget() {
    const next=val("wf-target");
    w.fitInputs[w.activeTarget]=fitSettings(w.activeTarget);
    restoreFitInputs(settingsForTarget(next));
    w.fit=currentTargetFit(next);w.preview=w.fit;w.request++;renderResidual();
  }
  function currentTargetFit(target=val("wf-target")) {
    const fit=w.fits[target];if(!fit)return null;
    if(target==="strip") {const a=fit.selection,b=selection(target);if(["strip","x_min","x_max"].some(key=>a[key]!==b[key]))return null;}
    return fit;
  }
  function batchFitSettings() {
    w.fitInputs[w.activeTarget]=fitSettings();
    return {regions:Object.fromEntries(["Si","Ge","Interface",...(checked("wf-core-enabled")?["Core"]:[])].map(name=>[name,settingsForTarget(name)]))};
  }
  async function run(stage) {
    if (w.busy || state.busy) return;
    try {
      if (stage!=="decomposition" && !w.decomposition) throw Error("请先完成全像素分解。");
      if (["fit","fit_regions"].includes(stage) && !w.profiles) throw Error("分区或条带设置已变化，请先重新建立体谱。");
      const settings=stage==="decomposition"?decompositionSettings():stage==="profiles"?profileSettings():stage==="localization"?EelsWorkflowRegions.settings():stage==="fit_regions"?batchFitSettings():fitSettings();
      if(stage==="fit")w.fitInputs[w.activeTarget]=structuredClone(settings);
      setRunning(true);status("正在启动计算…");
      const job=await api("run",{stage,settings,decomposition_id:w.decomposition?.result_id,
        profiles_id:w.profiles?.result_id,timeout_seconds:num("wf-timeout")});
      w.job=job.job_id;el("wf-cancel").disabled=false;poll();
    } catch(error){setRunning(false);status(error.message,true);}
  }
  async function poll() {
    if (!w.job) return;
    const token=w.dataset, jobId=w.job;
    try {
      const job=await api("jobs/"+encodeURIComponent(jobId));
      if(token!==w.dataset||jobId!==w.job)return;
      el("wf-progress").value=el("empty-progress").value=job.progress;status(job.message,job.status==="error");
      if(["queued","running"].includes(job.status)){setTimeout(poll,500);return;}
      if(job.status==="done"){
        if(job.stage==="import"){
          const firstImport=!hasDataset();
          const info=await requestApi("/api/info");
          if(info.dataset_id!==state.info.dataset_id)await loadDatasetInfo({preserveSettings:!firstImport});
          else state.info=info;
          w.dataset=null;w.fitInputs={};resetDataset(state.info,false);
          await open();
          if(!w.restored)throw Error("流程已导入服务，页面恢复未完成；请刷新页面读取结果。");
          el("fit-mode").value="multivariate";syncFitMode();
          if(firstImport){await EelsPreferences.restore({applySaved:false});EelsPreferences.bind();await EelsPreferences.save(true);}
          showStep(w.fit?5:w.profiles?4:2);status(job.message);EelsPreferences.schedule();
          return;
        }else if(job.stage==="fit_regions"){
          for(const id of Object.values(job.fit_result_ids))await loadResult("fit",id,false);
          w.fit=currentTargetFit();w.preview=w.fit;
        }else await loadResult(job.stage,job.result_id);
        w.job=null;setRunning(false);showStep(job.stage==="decomposition"?2:job.stage==="localization"?3:job.stage==="profiles"?4:5);
        status("本步骤完成。结果和导出参数对应这次实际运行。");
      }else{w.job=null;setRunning(false);status(job.error||job.message,true);}
    }catch(error){status(error.message+" 正在重新连接任务状态…",true);setTimeout(poll,2000);}
  }
  async function importWorkflow(event) {
    event.preventDefault();
    const file=el("workflow-import-file").files[0];
    if(!file||state.busy)return;
    const form=new FormData();form.append("file",file);
    form.append("data_path",val("workflow-import-data"));
    form.append("allow_version_mismatch",String(checked("workflow-import-version")));
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
    el("workflow-import-submit").disabled=true;setRunning(true);
    try {
      const response=await fetch("/api/workflow/import",{method:"POST",body:form,signal:controller.signal,
        headers:{"X-EELS-Dataset":state.info.dataset_id}});
      const job=await response.json();if(!response.ok)throw Error(job.error||"流程导入失败。");
      w.job=job.job_id;w.restored=true;
      el("fit-mode").value="multivariate";syncFitMode();
      el("workflow-import-dialog").close();setRunning(true);
      status("正在导入流程并重建谱图；保存的峰参数保持不变…");poll();
    } catch(error) {
      el("workflow-import-message").textContent=error.name==="AbortError"?"请求超时；请刷新查看任务状态，避免重复导入。":error.message;
      setRunning(false);
    } finally {clearTimeout(timer);el("workflow-import-submit").disabled=false;}
  }
  async function loadResult(stage,id,activate=true) {
    const result=await api(stage+"?result_id="+encodeURIComponent(id));
    if(stage==="decomposition"){
      w.decomposition=result;w.profiles=w.fit=w.preview=w.strip=w.roiPreview=null;
      w.localization=null;
      w.fits={};
      el("wf-component").replaceChildren(...result.spectra.map((_,i)=>new Option("组分 "+(i+1),String(i))));
      const prior=val("wf-background");
      const mapLabels={integral:"统一能窗积分图",total_counts:"EELS 总计数（非 HAADF）",reconstruction_error:"分解重建 RMSE"};
      el("wf-background").replaceChildren(...Object.keys(result.maps).map(key=>new Option(mapLabels[key]||"组分 "+(Number(key.split("_")[1])+1)+" 权重",key)));
      if(w.structure)el("wf-background").add(new Option("已配准结构像","structure"));
      if([...el("wf-background").options].some(option=>option.value===prior))el("wf-background").value=prior;
    }else if(stage==="profiles"){
      w.profiles=result;w.fit=w.preview=w.strip=null;
      w.fits={};
      const coreRows=coreStripOverlapRows(result);
      for(const id of ["wf-strip-view","wf-target-strip"]) el(id).replaceChildren(...result.strips.map(strip=>{
        const measured=(strip.measured_status||strip.status)==="available";
        const option=new Option("条带 "+(strip.index+1)+" · y="+fmt(strip.y_min)+"–"+fmt(strip.y_max)+" nm"+(!measured?"（无有效实测谱）":strip.status!=="available"?"（仅实测谱，扣谱不可用）":""),String(strip.index));
        option.dataset.stripLabel=option.textContent;option.dataset.coreRows=String(coreRows[strip.index]);
        option.disabled=id==="wf-strip-view"?!measured:strip.status!=="available";return option;}));
      const first=result.strips.find(strip=>(strip.measured_status||strip.status)==="available"),firstResidual=result.strips.find(strip=>strip.status==="available");
      if(first)el("wf-strip-view").value=String(first.index);
      if(firstResidual)el("wf-target-strip").value=String(firstResidual.index);
      el("wf-target-min").value=result.x_axis_nm[0];el("wf-target-max").value=result.x_axis_nm.at(-1);
      if(first) await loadStrip();
    }else if(stage==="localization"){EelsWorkflowRegions.load(result);}
    else{const key=resultKey(result);if(!regionFitsSupported())w.fits={};w.fits[key]=result;w.fitInputs[key]=structuredClone(result.settings);if(activate){w.fit=result;w.preview=result;}}
    if(activate)render();
  }
  function invalidateProfiles() {
    w.profiles=w.fit=w.preview=w.strip=null;w.request++;
    w.fits={};
    syncStripColors();
    el("wf-profiles-summary").textContent="分区 / 条带 / 模板已改变，请重新建立体谱。";
    el("wf-fit-table").textContent="";el("wf-comparison").textContent="";
  }
  function syncRoiInputs() {
    const r=w.rois[w.region];if(!r)return;
    for(const [id,key] of [["xmin","x_min"],["xmax","x_max"],["ymin","y_min"],["ymax","y_max"]])el("wf-roi-"+id).value=Number(r[key].toFixed(6));
  }
  async function applyRegionProposal(next,{coreEnabled=checked("wf-core-enabled"),activeRegion=w.region}={}){
    if(w.busy||!w.decomposition)throw Error("请先完成分解并等待当前任务结束。");
    const proposed={...structuredClone(w.rois),...structuredClone(next)},dataset=w.dataset;
    setRunning(true);
    try{
      const preview=await api("roi-preview",{decomposition_id:w.decomposition.result_id,rois:proposed,core_enabled:coreEnabled});
      if(dataset!==w.dataset)throw Error("数据已切换，候选选区未应用。");
      const changed=Object.entries(proposed).some(([name,roi])=>["x_min","x_max","y_min","y_max"].some(key=>roi[key]!==w.rois[name]?.[key]))||coreEnabled!==checked("wf-core-enabled");
      if(changed){w.rois=proposed;el("wf-core-enabled").checked=coreEnabled;invalidateProfiles();w.roiPreview=preview;}
      w.region=activeRegion;el("wf-region").value=activeRegion;syncRoiInputs();
      status(changed?"已应用并验证候选选区；体谱和分峰结果需按新选区重新计算。":"选区没有变化，已有分析结果保留。");
    }finally{setRunning(false);render();}
  }
  function applyRoi() {
    const r={x_min:num("wf-roi-xmin"),x_max:num("wf-roi-xmax"),y_min:num("wf-roi-ymin"),y_max:num("wf-roi-ymax")};
    const info=state.info;
    if(r.x_min>r.x_max||r.y_min>r.y_max||r.x_min<info.x_nm[0]||r.x_max>info.x_nm[1]||r.y_min<info.y_nm[0]||r.y_max>info.y_nm[1])throw Error("选区坐标倒置或超出数据范围。");
    if(JSON.stringify(r)!==JSON.stringify(w.rois[w.region])){w.rois[w.region]=r;invalidateProfiles();w.roiPreview=null;}
    drawRegionMap();EelsWorkflowRegions.render();status("选区坐标已更新；底图切换仍会保留选框。");
  }
  async function roiPreview() {
    if(!w.decomposition)throw Error("请先完成分解。");
    const token=++w.request;
    const result=await api("roi-preview",{decomposition_id:w.decomposition.result_id,rois:w.rois,core_enabled:checked("wf-core-enabled")});
    if(token!==w.request)return;w.roiPreview=result;render();
  }
  async function loadStrip() {
    if(!w.profiles)return;
    const token=++w.request;
    const result=await api("strip?result_id="+w.profiles.result_id+"&index="+val("wf-strip-view"));
    if(token!==w.request)return;w.strip=result;
    for(const option of el("wf-strip-kind").options)option.disabled=!Array.isArray(result[option.value]);
    if(!Array.isArray(result[val("wf-strip-kind")]))el("wf-strip-kind").value="measured";
    render();
  }
  async function preview() {
    if(!w.profiles)throw Error("请先确认分区并建立体谱。");
    const token=++w.request;
    const result=await api("preview",{profiles_id:w.profiles.result_id,selection:selection(),analysis_kind:isBulkTarget()?"bulk_gaussian":"residual"});
    if(token!==w.request)return;w.preview=result;render();
  }

  function syncSeedChoices() {
    const rank=Math.max(2,Math.min(8,Number(val("wf-rank"))||3));
    w.seedIndex=Math.min(w.seedIndex,rank-1);
    el("wf-seed-component").replaceChildren(...Array.from({length:rank},(_,i)=>new Option("初值 "+(i+1)+(w.seeds[i]?.enabled?" · ROI":" · NNDSVDa"),String(i))));
    el("wf-seed-component").value=String(w.seedIndex);
  }
  function syncSeedInputs() {
    const seed=w.seeds[w.seedIndex];if(!seed)return;
    el("wf-seed-enabled").checked=seed.enabled;
    for(const [id,key] of [["xmin","x_min"],["xmax","x_max"],["ymin","y_min"],["ymax","y_max"]])el("wf-seed-"+id).value=Number(seed.roi[key].toFixed(6));
  }
  function seedChanged() {
    w.seedPreview=null;w.seedRequest++;syncSeedChoices();syncSeedInputs();renderSeeds();
    el("wf-seed-summary").textContent="初值设置已改变；可重新预览。已完成的分解与分析选区保持不变，新设置在下次运行时生效。";
  }
  function applySeedRoi() {
    const r={x_min:num("wf-seed-xmin"),x_max:num("wf-seed-xmax"),y_min:num("wf-seed-ymin"),y_max:num("wf-seed-ymax")},info=state.info;
    if(r.x_min>r.x_max||r.y_min>r.y_max||r.x_min<info.x_nm[0]||r.x_max>info.x_nm[1]||r.y_min<info.y_nm[0]||r.y_max>info.y_nm[1])throw Error("初值 ROI 坐标倒置或超出图像范围。");
    if(JSON.stringify(r)!==JSON.stringify(w.seeds[w.seedIndex].roi)){w.seeds[w.seedIndex].roi=r;seedChanged();}
  }
  async function loadSeedMap() {
    if(!roiSupported())return;
    const token=++w.seedMapRequest,dataset=w.dataset;
    const result=await api("initialization-map?fit_min="+num("wf-min")+"&fit_max="+num("wf-max"));
    if(token!==w.seedMapRequest||dataset!==w.dataset)return;
    w.seedMap=result;
    el("wf-seed-background").querySelector('[value="total_counts"]').disabled=!result.maps.total_counts;
    if(!result.maps[val("wf-seed-background")])el("wf-seed-background").value="integral";
    renderSeeds();
  }
  async function previewSeeds() {
    const token=++w.seedRequest,dataset=w.dataset;
    const result=await api("initialization-preview",decompositionSettings());
    if(token!==w.seedRequest||dataset!==w.dataset)return;
    w.seedPreview=result;renderSeeds();
    el("wf-seed-summary").textContent=result.metadata.map(r=>"初值 "+(r.component+1)+"："+r.pixel_count+" 像素，排除 "+r.excluded_pixels+"，负通道 "+r.negative_channels).join("；")+"。 "+result.warnings.join(" ");
    status("ROI 均谱预览已更新；运行分解时会从已应用选框重新提取初值。");
  }
  function renderSeeds() {
    if(!visible()||w.step!==1||val("wf-init")!=="roi")return;
    const d=w.seedMap;
    const g=heatmap("wf-seed-map",d?.maps[val("wf-seed-background")],d?.x_nm,d?.y_nm,{aspect:true,scaleId:"wf-seed-scale"});
    w.seedGeometry=g;
    if(g)for(let i=0;i<num("wf-rank")&&i<w.seeds.length;i++) {
      const seed=w.seeds[i];if(!seed.enabled&&i!==w.seedIndex)continue;
      const a=g.p(seed.roi.x_min,seed.roi.y_max),b=g.p(seed.roi.x_max,seed.roi.y_min),ctx=g.ctx;
      ctx.strokeStyle=componentColors[i%componentColors.length];ctx.lineWidth=i===w.seedIndex?2.6:1.4;ctx.setLineDash(seed.enabled?[]:[4,3]);ctx.strokeRect(a.x,a.y,b.x-a.x,b.y-a.y);ctx.setLineDash([]);
      ctx.fillStyle=ctx.strokeStyle;ctx.font="12px Segoe UI";ctx.textAlign="left";ctx.fillText("初值 "+(i+1),a.x+4,Math.max(g.box.top+12,a.y+13));
      if(i===w.seedIndex)EelsRoi.handles({left:a.x,right:b.x,top:a.y,bottom:b.y}).forEach(h=>{ctx.fillStyle="white";ctx.fillRect(h.x-3,h.y-3,6,6);ctx.strokeRect(h.x-3,h.y-3,6,6);});
    }
    const p=w.seedPreview,index=p?.metadata.findIndex(r=>r.component===w.seedIndex);
    const curves=index>=0?[{y:p.mean_spectra[index],color:"#273039",label:"ROI 原均谱"},{y:p.initial_spectra[index],color:"#2563eb",label:"非负初值（缩放前）",dash:[4,3]}]:[];
    plot("wf-seed-spectrum",p?.energy_meV,curves,"wf-seed-legend");
  }
  function finishSeedDrag(cancel) {
    if(!w.seedDrag)return;
    const d=w.seedDrag,canvas=el("wf-seed-map");w.seedDrag=null;
    if(cancel)w.seeds[d.index].roi=d.original;
    else if(JSON.stringify(w.seeds[d.index].roi)!==JSON.stringify(d.original))seedChanged();
    if(canvas.hasPointerCapture(d.id))canvas.releasePointerCapture(d.id);syncSeedInputs();renderSeeds();
  }
  function bindSeedDrag() {
    const canvas=el("wf-seed-map"),point=event=>{const b=canvas.getBoundingClientRect();return{x:event.clientX-b.left,y:event.clientY-b.top};};
    canvas.addEventListener("pointerdown",event=>{
      if(w.busy||!w.seedGeometry||event.button!==0)return;const g=w.seedGeometry,c=point(event),r=w.seeds[w.seedIndex].roi;
      if(c.x<g.box.left||c.x>g.box.left+g.box.width||c.y<g.box.top||c.y>g.box.top+g.box.height)return;
      const a=g.p(r.x_min,r.y_max),b=g.p(r.x_max,r.y_min),hit=EelsRoi.handles({left:a.x,right:b.x,top:a.y,bottom:b.y}).find(h=>Math.hypot(c.x-h.x,c.y-h.y)<9);
      w.seedDrag={index:w.seedIndex,original:structuredClone(r),start:g.toData(c.x,c.y),mode:hit?.mode||(c.x>a.x&&c.x<b.x&&c.y>a.y&&c.y<b.y?"move":"draw"),id:event.pointerId};
      canvas.setPointerCapture(event.pointerId);canvas.focus({preventScroll:true});event.preventDefault();});
    canvas.addEventListener("pointermove",event=>{
      const d=w.seedDrag;if(!d)return;const c=point(event),map=w.seedMap;
      w.seeds[d.index].roi=EelsRoi.adjust(d.original,d.start,w.seedGeometry.toData(c.x,c.y),d.mode,{x:map.pixel_x_nm,y:map.pixel_y_nm});syncSeedInputs();renderSeeds();});
    canvas.addEventListener("pointerup",()=>finishSeedDrag(false));canvas.addEventListener("pointercancel",()=>finishSeedDrag(true));
    canvas.addEventListener("lostpointercapture",()=>finishSeedDrag(true));canvas.addEventListener("keydown",event=>{if(event.key==="Escape"){finishSeedDrag(true);event.preventDefault();}});
  }

  function plot(id,energy,curves,legendId,ylabel="强度 (a.u.)",xlabel="能量损失 (meV)") {
    const canvas=el(id);if(!canvas||!canvas.getBoundingClientRect().width)return;
    const {context:ctx,width,height}=setupCanvas(canvas);
    const box={left:66,top:14,width:width-84,height:height-59,yLabelOffset:51};
    drawAxis(ctx,box,xlabel,ylabel);
    if(legendId)el(legendId).replaceChildren();
    if(!energy?.length||!curves?.length)return;
    const values=curves.flatMap(curve=>curve.y).filter(n=>n!=null&&Number.isFinite(n));if(!values.length)return;
    const range=extent(values);let ymin=Math.min(0,range[0]),ymax=Math.max(0,range[1]);const pad=Math.max((ymax-ymin)*.09,1e-12);ymin-=pad;ymax+=pad;
    const lo=energy[0],hi=energy.at(-1),p=(x,y)=>({x:box.left+(x-lo)/(hi-lo)*box.width,y:box.top+(ymax-y)/(ymax-ymin)*box.height});
    drawTicks(ctx,box,lo,hi,ymin,ymax,v=>fmt(v));drawLine(ctx,[p(lo,0),p(hi,0)],"#cbd5e1",1,[3,3]);
    ctx.save();ctx.beginPath();ctx.rect(box.left,box.top,box.width,box.height);ctx.clip();
    [...curves].sort((a,b)=>(a.priority??0)-(b.priority??0)).forEach(curve=>{
      ctx.globalAlpha=curve.alpha??1;
      drawLine(ctx,(curve.x||energy).map((e,i)=>p(e,curve.y[i])),curve.color,curve.width||1.8,curve.dash||[]);
    });ctx.restore();
    if(legendId)el(legendId).replaceChildren(...curves.map(curve=>{const span=document.createElement("span");span.style.color=curve.color;span.textContent="━ "+curve.label;return span;}));
  }
  function heatmap(id,map,x,y,{diverging=false,aspect=false,compactTicks=false,xlabel="x (nm)",ylabel="y (nm)",scaleId,image=null}={}) {
    const canvas=el(id);if(!canvas?.getBoundingClientRect().width)return null;
    const {context:ctx,width,height}=setupCanvas(canvas);
    if(!x?.length||!y?.length||(!map&&!image)){if(scaleId)el(scaleId).textContent="尚无数据。";return null;}
    const xmin=x[0],xmax=x.at(-1),ymin=y[0],ymax=y.at(-1);
    const dx=Math.max(xmax-xmin,1e-9),dy=Math.max(ymax-ymin,1e-9);
    let pw=width-86,ph=height-65;
    if(aspect){const s=Math.min(pw/dx,ph/dy);pw=s*dx;ph=s*dy;}
    const box={left:65+(width-86-pw)/2,top:15+(height-65-ph)/2,width:pw,height:ph,yLabelOffset:51,
      xTickStride:compactTicks?(pw<100?4:pw<200?2:1):1};
    const p=(a,b)=>({x:box.left+(a-xmin)/dx*pw,y:box.top+(ymax-b)/dy*ph});
    const values=map?map.flat().filter(n=>n!=null&&Number.isFinite(n)):[];
    const range=values.length?extent(values):[0,1];let low=range[0],high=range[1];
    if(diverging){const a=Math.max(abs(low),abs(high),1e-30);low=-a;high=a;}
    if(image){ctx.save();ctx.translate(box.left+(checked("wf-image-flipx")?pw:0),box.top+(checked("wf-image-flipy")?ph:0));ctx.scale(checked("wf-image-flipx")?-1:1,checked("wf-image-flipy")?-1:1);ctx.drawImage(image,0,0,pw,ph);ctx.restore();}
    else {
      const xe=x.map((a,i)=>i===0?a:(a+x[i-1])/2);xe.push(xmax);
      const ye=y.map((a,i)=>i===0?a:(a+y[i-1])/2);ye.push(ymax);
      for(let iy=0;iy<y.length;iy++)for(let ix=0;ix<x.length;ix++){
        const value=map[iy]?.[ix],a=p(xe[ix],ye[iy]),b=p(xe[ix+1],ye[iy+1]);
        ctx.fillStyle=value==null||!Number.isFinite(value)?"#e3e8e8":colorScale(high===low?.5:(value-low)/(high-low),diverging?[[49,70,173],[244,244,244],[177,5,47]]:mapColors);
        ctx.fillRect(a.x,b.y,Math.max(1,b.x-a.x+.5),Math.max(1,a.y-b.y+.5));
      }
    }
    drawAxis(ctx,box,xlabel,ylabel);drawTicks(ctx,box,xmin,xmax,ymin,ymax,v=>fmt(v));
    if(scaleId)el(scaleId).textContent=image?"外部结构像；坐标对应由用户核对，未自动标定。":"色标 "+fmt(low)+" → "+fmt(high)+(w.decomposition?.display_downsampled&&aspect?"；仅显示降采样，计算保留全部有效像素。":"");
    return {ctx,box,p,toData:(cx,cy)=>({x:xmin+(cx-box.left)/pw*dx,y:ymax-(cy-box.top)/ph*dy})};
  }
  const abs=Math.abs;
  function drawRegionMap() {
    const d=w.decomposition;if(!d)return;
    const key=val("wf-background"),structure=key==="structure";
    if(structure&&(!w.structure||!checked("wf-structure-confirmed"))){status("使用结构像叠加前，请核对同视场、坐标范围与方向。",true);return;}
    const g=heatmap("wf-region-map",d.maps[key],d.x_nm,d.y_nm,{aspect:true,scaleId:"wf-map-scale",image:structure?w.structure.image:null});
    if(!g)return;w.geometry=g;
    const names=checked("wf-core-enabled")?Object.keys(colors):["Si","Ge","Interface"];
    for(const name of names){const r=w.rois[name];if(!r)continue;const a=g.p(r.x_min,r.y_max),b=g.p(r.x_max,r.y_min);
      g.ctx.strokeStyle=colors[name];g.ctx.lineWidth=name===w.region?2.6:1.6;g.ctx.setLineDash(name==="Core"?[5,3]:[]);g.ctx.strokeRect(a.x,a.y,b.x-a.x,b.y-a.y);g.ctx.setLineDash([]);
      g.ctx.font="12px Segoe UI";g.ctx.textAlign="left";g.ctx.fillStyle=colors[name];g.ctx.fillText(name==="Interface"?"总界面":labels[name],a.x+4,Math.max(g.box.top+12,a.y+13));
      if(name===w.region)EelsRoi.handles({left:a.x,right:b.x,top:a.y,bottom:b.y}).forEach(h=>{g.ctx.fillStyle="white";g.ctx.fillRect(h.x-3,h.y-3,6,6);g.ctx.strokeRect(h.x-3,h.y-3,6,6);});
    }
    EelsWorkflowRegions.drawOverlay(g);
  }
  function render() {
    if(!visible())return;syncOptions();const d=w.decomposition,p=w.profiles;
    if(w.step===1&&val("wf-init")==="roi")renderSeeds();
    if(d){const s=d.summary;
      el("wf-decomp-summary").textContent=s.valid_pixels+" 个有效像素；RMSE "+fmt(s.rmse)+"；选中起点 "+(s.selected_start+1)+"。 "+s.warnings.join(" ");
      el("wf-decomp-warnings").textContent="相对原输入的重建 RMSE "+fmt(s.original_input_rmse)+"。 "+s.warnings.join(" ");
      if(s.roi_initialization)el("wf-decomp-warnings").textContent+=" 最终选中起点"+(s.roi_initialization.used_by_selected_start?"使用了 ROI 初值。":"来自随机初始化；ROI 起点不是本次最低误差解。");
      if(w.step===2){const index=Number(val("wf-component"));heatmap("wf-component-map",d.maps["component_"+index],d.x_nm,d.y_nm,{aspect:true,scaleId:"wf-component-scale"});
        const curves=d.spectra.map((spectrum,i)=>({y:spectrum,color:componentColors[i%componentColors.length],label:"组分 "+(i+1),width:i===index?3:1}));
        if(w.reference){let area=0;for(let i=1;i<w.reference.x.length;i++)area+=(w.reference.x[i]-w.reference.x[i-1])*(abs(w.reference.y[i])+abs(w.reference.y[i-1]))/2;
          curves.push({x:w.reference.x,y:w.reference.y.map(v=>v/Math.max(area,1e-30)),color:"#242b31",label:w.reference.name+"（面积归一化）",dash:[4,3]});}
        plot("wf-component-spectrum",d.energy_meV,curves,"wf-component-legend","单位面积谱");
        table("wf-trials",["起点","初始化","迭代","收敛","RMSE"],s.trials.map(t=>[t.start+1,t.initialization,t.iterations,t.converged?"是":"否",fmt(t.rmse)]));}
      if(w.step===3){drawRegionMap();EelsWorkflowRegions.render();const r=w.roiPreview;plot("wf-roi-spectrum",r?.energy_meV,r?Object.entries(r.regions).map(([name,v])=>({y:v.spectrum,color:colors[name],label:labels[name]+" · "+v.pixel_count+" 像素"})):[],"wf-roi-legend");}
    }
    if(w.step===4){
      el("wf-template-help").textContent=independentStripsSupported()?
        "条带实测数据只取决于总界面 y 范围，覆盖完整 x 轴。Si / Ge 选框仅提取整框平均体谱，供后续分峰和扣谱参考，所有条带共用；不再与各条带相交。模板失败时仍保留实测条带。":
        "当前端口仍使用旧的条带 / 体谱耦合规则；请运行 start_web.ps1 使用新版。旧结果保留原有计算设置。";
      const roi=w.rois.Interface,rows=(d?.pixel_y_nm||[]).filter(y=>roi&&y>=roi.y_min&&y<=roi.y_max);
      el("wf-stripe-scope").textContent=interfaceStripesSupported()?
        (rows.length?"按总界面 y="+fmt(rows[0])+"–"+fmt(rows.at(-1))+" nm 内的 "+rows.length+" 行像素分段，起始行为框内第一行；每条覆盖完整 x 轴，末条可不足指定高度。":"请先选择包含像素的总界面 y 范围。"):
        "当前端口仍按整幅图的 y 范围分条带；请运行 start_web.ps1 使用新版规则。已有结果保留原范围。";
      el("wf-stripe-scope").classList.toggle("error",!interfaceStripesSupported());
      if(p){const domain=p.summary.strip_domain;
        el("wf-profiles-summary").textContent="实测条带 "+(p.summary.measured_strips??p.summary.available_strips)+" / "+p.strips.length+"，可扣谱 "+p.summary.available_strips+" / "+p.strips.length+"。"+
          (p.settings.stripe_y_scope==="interface_roi"?"本次范围为总界面 y":"本次范围为全图 y（旧规则）")+
          (domain?"："+fmt(domain.y_min)+"–"+fmt(domain.y_max)+" nm。":"。")+
          (p.alignment.enabled?"横轴为所选 y 行共有的配准距离 d。":"每条覆盖完整原始 x 轴。")+" "+p.summary.missing_strips.map(s=>"条带 "+(s.index+1)+"扣谱不可用："+s.reason).join("；");}
      plot("wf-bulk-spectrum",p?.energy_meV,p?[{y:p.raw_bulk[0],color:"#87a8cc",label:"Si 均谱"},{y:p.templates[0],color:colors.Si,label:"Si 实际模板"},{y:p.raw_bulk[1],color:"#aac7b1",label:"Ge 均谱"},{y:p.templates[1],color:colors.Ge,label:"Ge 实际模板"}]:[],"wf-bulk-legend");
      const strip=w.strip,key=val("wf-strip-kind");
      const map=Array.isArray(strip?.[key])?strip.energy_meV.map((_,e)=>strip[key].map(row=>row[e])):null;
      heatmap("wf-strip-map",map,strip?.x_axis_nm,strip?.energy_meV,{xlabel:p?.alignment.enabled?"d (nm)":"x (nm)",ylabel:"能量 (meV)",diverging:key==="residual",scaleId:"wf-strip-scale"});
      el("wf-strip-data-note").textContent=strip?.reason?"本条带"+((strip.measured_status||strip.status)==="available"?"实测谱仍可查看；":"无有效实测谱；")+"扣谱不可用："+strip.reason:"";
    }
    if(w.step===5){renderResidual();}
  }
  function table(id, headings, rows) {
    const t=document.createElement("table"),head=document.createElement("thead"),body=document.createElement("tbody"),tr=document.createElement("tr");
    headings.forEach(text=>{const th=document.createElement("th");th.textContent=text;tr.appendChild(th);});head.appendChild(tr);
    rows.forEach(row=>{const tr=document.createElement("tr");row.forEach(value=>{const td=document.createElement("td");td.textContent=value;tr.appendChild(td);});body.appendChild(tr);});
    t.append(head,body);el(id).replaceChildren(t);
  }
  function renderResidual() {
    const r=w.preview,f=w.fit,e=r?.energy_meV;
    const cap=f?.settings?.amplitude_limit;
    el("wf-fit-policy").textContent=e?.length?"当前实际能窗："+fmt(e[0],6)+"–"+fmt(e.at(-1),6)+" meV；窗外通道不参与残差和拟合。"+
      (f?(cap?" 本次已完成拟合的峰高上限：邻域同号实测极值的 "+fmt(cap.factor*100)+"%。":" 本次已完成拟合未设置峰高上限（历史参数）。"):""):"";
    if(f&&f.analysis_kind!=="bulk_gaussian")el("wf-fit-policy").textContent+=f.settings.centers?.length?" 本次已完成拟合使用手动峰位。":f.settings.candidate_score==="zero_baseline_prominence_v1"?" 本次已完成拟合使用零线约束评分：min(突出度, 绝对峰高)。":" 本次已完成拟合使用历史突出度评分。";
    if(f?.restoration==="saved_parameters_v1")el("wf-fit-policy").textContent+=" 此结果从 JSON 恢复，峰参数未重拟合；曲线由对应数据和保存参数重建。";
    const states=["Si","Ge","Interface","Core"].map(name=>{
      const fit=w.fits[name],model=fit?.analysis_kind==="bulk_gaussian"?fit.bulk_fit:fit?.two_stage;
      const status=name==="Core"&&!checked("wf-core-enabled")?"未启用":!fit?"未拟合":!model?"未检出峰":model.success&&(fit.joint==null||fit.joint.success)?"已完成":"未收敛";
      return labels[name]+"："+status;
    });
    el("wf-region-fit-status").textContent=states.join("　｜　");
    if(r?.analysis_kind==="bulk_gaussian"||(!r&&isBulkTarget())) {
      const model=f?.bulk_fit;
      const bulkCurves=r?[{y:r.measured,color:"#273039",width:2.7,priority:3,label:labels[r.selection.region]+" 原始均谱"},...(model?[{y:model.fitted,color:"#c13d38",width:1.8,dash:[6,3],priority:2,label:"高斯总拟合"}]:[])]:[];
      plot("wf-subtraction-spectrum",e,bulkCurves,"wf-subtraction-legend");
      plot("wf-residual-spectrum",e,model?[...bulkCurves,...model.peak_components.map((y,i)=>({y,color:componentColors[i%componentColors.length],width:1,alpha:.4,priority:1,label:"高斯峰 "+(i+1)+" · "+fmt(model.centers_meV[i],4)+" meV"}))]:[],"wf-residual-legend");
      el("wf-diagnostics").textContent=r?labels[r.selection.region]+"；"+r.selection.pixel_count+" 个像素。"+(model?" Gaussian 分峰 RMSE="+fmt(model.rmse)+"；"+model.centers_meV.length+" 个峰。":"")+" "+(f?.diagnostics?.warnings||[]).join(" "):"预览体区平均谱后可调整峰数、峰位和宽度范围。";
      if(model)table("wf-fit-table",["高斯峰","峰位 (meV)","FWHM (meV)","振幅","峰高上限","峰高触顶","峰面积","参数边界"],model.centers_meV.map((c,i)=>[i+1,fmt(c,5),fmt(model.fwhm_meV[i]),fmt(model.amplitudes[i]),model.amplitude_constraint?fmt(model.amplitude_constraint.amplitude_abs_upper[i]):"未设置",model.at_amplitude_limit?.[i]?"是":"—",fmt(model.areas[i]),model.at_constraint_boundary[i]?"接近边界":"—"]));
      else el("wf-fit-table").textContent=f?"当前参数未检出可分辨体谱峰，请调整后重试。":"尚无当前体区的高斯分峰结果。";
      el("wf-comparison").textContent="";return;
    }
    plot("wf-subtraction-spectrum",e,r?[{y:r.measured,color:"#273039",width:2.7,priority:3,label:"实测谱"},{y:r.bulk_sum,color:"#c24a40",width:1.6,priority:2,dash:[5,3],label:"体谱贡献和"},
      ...r.templates.map((s,i)=>({y:s.map(v=>v*r.weights[i]),color:i?colors.Ge:colors.Si,width:1,alpha:.4,priority:1,label:i?"Ge 贡献":"Si 贡献"})),
      ...(f?.joint?[{y:f.joint.fitted,color:"#875aac",width:1.8,priority:2,label:"体谱 + 候选峰联合拟合",dash:[4,3]}]:[])]:[],"wf-subtraction-legend");
    const curves=r?[{y:r.residual,color:"#273039",width:2.7,priority:3,label:"实测有符号残差"}]:[];
    if(f?.two_stage){curves.push({y:f.two_stage.fitted,color:"#c13d38",width:1.8,dash:[6,3],priority:2,label:"残差线型拟合"});f.two_stage.peak_components.forEach((y,i)=>curves.push({y,color:componentColors[i%componentColors.length],width:1,alpha:.4,priority:1,label:(f.two_stage.amplitudes[i]<0?"负峰 ":"正峰 ")+(i+1)}));}
    plot("wf-residual-spectrum",e,curves,"wf-residual-legend","残差强度 (a.u.)");
    const source=r?.selection?.kind==="strip"?"条带 "+(r.selection.strip+1)+"，x/d="+fmt(r.selection.x_min)+"–"+fmt(r.selection.x_max)+" nm":labels[r?.selection?.region]||"当前选区";
    el("wf-diagnostics").textContent=r?source+"；权重 Si="+fmt(r.weights[0])+"，Ge="+fmt(r.weights[1])+"；残差 RMSE="+fmt(r.diagnostics.rmse)+
      "；负残差通道="+fmt(r.diagnostics.negative_fraction*100)+"%；正 / 负积分="+fmt(r.diagnostics.positive_area)+" / "+fmt(r.diagnostics.negative_area)+"；与 Si / Ge 体谱导数相关="+r.diagnostics.bulk_derivative_correlations.map(v=>fmt(v)).join(" / ")+"。 "+r.diagnostics.warnings.join(" "):"选择区域或条带范围后预览；负残差会保留。";
    if(f){
      if(f.two_stage)table("wf-fit-table",["候选峰","方向","残差拟合峰位","联合拟合峰位","峰位差 (meV)","残差 FWHM","联合 FWHM","残差振幅","联合振幅","峰高绝对值上限","峰高触顶（残差/联合）","联合峰面积","联合 / 残差振幅"],f.two_stage.centers_meV.map((c,i)=>[i+1,(f.two_stage.peak_signs?.[i]??1)<0?"负峰":"正峰",fmt(c,5),fmt(f.joint.centers_meV[i],5),fmt(f.comparison.center_shift_meV[i]),fmt(f.two_stage.fwhm_meV[i]),fmt(f.joint.fwhm_meV[i]),fmt(f.two_stage.amplitudes[i]),fmt(f.joint.amplitudes[i]),f.two_stage.amplitude_constraint?fmt(f.two_stage.amplitude_constraint.amplitude_abs_upper[i]):"未设置",(f.two_stage.at_amplitude_limit?.[i]?"是":"—")+" / "+(f.joint.at_amplitude_limit?.[i]?"是":"—"),fmt(f.joint.areas[i]),fmt(f.comparison.amplitude_ratio_joint_to_staged[i])]));
      else el("wf-fit-table").textContent="当前方向和阈值下没有满足条件的残差峰，未强制拟合。";
      el("wf-comparison").textContent=f.comparison?"SSE：仅体谱 "+fmt(f.comparison.bulk_only_sse)+"；两步法 "+fmt(f.comparison.two_stage_full_sse)+"；联合拟合 "+fmt(f.comparison.joint_full_sse)+"。 "+f.comparison.note:"";
    }else{el("wf-fit-table").textContent="";el("wf-comparison").textContent="";}
  }
  function bindDrag() {
    const canvas=el("wf-region-map");
    const point=event=>{const r=canvas.getBoundingClientRect();return{x:event.clientX-r.left,y:event.clientY-r.top};};
    canvas.addEventListener("pointerdown",event=>{
      if(w.busy||!w.geometry||event.button!==0)return;const g=w.geometry,c=point(event),r=w.rois[w.region],a=g.p(r.x_min,r.y_max),b=g.p(r.x_max,r.y_min);
      if(c.x<g.box.left||c.x>g.box.left+g.box.width||c.y<g.box.top||c.y>g.box.top+g.box.height)return;
      const handles=EelsRoi.handles({left:a.x,right:b.x,top:a.y,bottom:b.y});const hit=handles.find(h=>Math.hypot(c.x-h.x,c.y-h.y)<9);
      const inside=c.x>a.x&&c.x<b.x&&c.y>a.y&&c.y<b.y;
      w.drag={original:structuredClone(r),start:g.toData(c.x,c.y),mode:hit?.mode||(inside?"move":"draw"),id:event.pointerId};
      canvas.setPointerCapture(event.pointerId);canvas.focus({preventScroll:true});event.preventDefault();});
    canvas.addEventListener("pointermove",event=>{
      if(!w.drag)return;const p=point(event),d=w.drag;
      w.rois[w.region]=EelsRoi.adjust(d.original,d.start,w.geometry.toData(p.x,p.y),d.mode,{x:w.decomposition.pixel_x_nm,y:w.decomposition.pixel_y_nm});
      syncRoiInputs();drawRegionMap();});
    const finish=cancel=>{if(!w.drag)return;const d=w.drag;w.drag=null;
      if(cancel)w.rois[w.region]=d.original;
      else if(JSON.stringify(d.original)!==JSON.stringify(w.rois[w.region])){invalidateProfiles();w.roiPreview=null;status("选区已更新；请确认分区并重新建立体谱。");}
      if(canvas.hasPointerCapture(d.id))canvas.releasePointerCapture(d.id);syncRoiInputs();drawRegionMap();EelsWorkflowRegions.render();};
    canvas.addEventListener("pointerup",()=>finish(false));canvas.addEventListener("pointercancel",()=>finish(true));
    canvas.addEventListener("lostpointercapture",()=>finish(true));canvas.addEventListener("keydown",event=>{if(event.key==="Escape"){finish(true);event.preventDefault();}});
  }
  async function download(arrays=false) {
    if(!w.decomposition)throw Error("尚无可导出的工作流结果。");
    const ids={decomposition:w.decomposition.result_id,...(w.profiles?{profiles:w.profiles.result_id}:{}),...(w.fit?{fit:w.fit.result_id}:{}),...(w.localization?{localization:w.localization.result_id}:{})};
    const suffix=arrays?".npz":".json";
    const path=arrays?"arrays?decomposition_id="+ids.decomposition+(ids.profiles?"&profiles_id="+ids.profiles:"")+(ids.localization?"&localization_id="+ids.localization:""):"export";
    const response=await fetch("/api/workflow/"+path,{method:arrays?"GET":"POST",headers:{"Content-Type":"application/json","X-EELS-Dataset":state.info.dataset_id},...(arrays?{}:{body:JSON.stringify({result_ids:ids,fit_result_ids:regionFitsSupported()?Object.fromEntries(Object.entries(w.fits).map(([key,fit])=>[key,fit.result_id])):{}})})});
    if(!response.ok)throw Error((await response.json()).error||"导出失败。");
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;
    link.download=state.info.source.split(/[\\/]/).pop().replace(/\.[^.]+$/,"")+suffix;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
    status(arrays?"已导出组分、权重和已完成条带的派生数组。":"已汇总导出各区实际完成的分峰结果、状态与复现参数；未拟合或未启用区域已注明。");
  }
  async function mount() {
    const response=await fetch("/static/workflow.html");el("workflow-workspace").innerHTML=await response.text();
    const on=(id,event,fn)=>el(id).addEventListener(event,()=>Promise.resolve().then(fn).catch(error=>status(error.message,true)));
    on("import-workflow","click",()=>{el("workflow-import-message").textContent="";el("workflow-import-dialog").showModal();});
    on("workflow-import-close","click",()=>el("workflow-import-dialog").close());
    el("workflow-import-form").addEventListener("submit",importWorkflow);
    document.querySelectorAll("[data-wf-step],[data-wf-next]").forEach(button=>button.addEventListener("click",()=>showStep(button.dataset.wfStep||button.dataset.wfNext)));
    on("wf-decompose","click",()=>run("decomposition"));on("wf-profiles","click",()=>run("profiles"));on("wf-fit","click",()=>run("fit"));
    on("wf-fit-regions","click",()=>run("fit_regions"));
    on("wf-cancel","click",async()=>{if(w.job){await api("jobs/"+w.job+"/cancel",{});status("正在终止计算进程…");}});
    on("empty-cancel","click",()=>el("wf-cancel").click());
    on("wf-export","click",()=>download());on("wf-arrays","click",()=>download(true));
    on("wf-init","change",async()=>{finishSeedDrag(true);syncOptions();render();if(val("wf-init")==="roi"&&!w.seedMap)await loadSeedMap();});
    on("wf-rank","change",()=>{finishSeedDrag(true);syncSeedChoices();syncSeedInputs();seedChanged();});
    on("wf-seed-component","change",()=>{finishSeedDrag(true);w.seedIndex=num("wf-seed-component");syncSeedInputs();renderSeeds();});
    on("wf-seed-enabled","change",()=>{w.seeds[w.seedIndex].enabled=checked("wf-seed-enabled");seedChanged();});
    on("wf-seed-copy","click",()=>{w.seeds[w.seedIndex].roi=structuredClone(w.rois[val("wf-seed-source")]);seedChanged();});
    on("wf-seed-apply","click",applySeedRoi);on("wf-seed-map-load","click",loadSeedMap);on("wf-seed-preview","click",previewSeeds);
    on("wf-seed-negative","change",seedChanged);on("wf-seed-background","change",renderSeeds);
    for(const id of ["wf-min","wf-max"])on(id,"change",()=>{w.seedMap=null;w.seedGeometry=null;w.seedMapRequest++;seedChanged();});
    for(const id of ["wf-method","wf-template-kind"])on(id,"change",()=>{syncOptions();if(id==="wf-template-kind")invalidateProfiles();});
    on("wf-target","change",selectFitTarget);
    on("wf-region","change",()=>{w.region=val("wf-region");syncRoiInputs();drawRegionMap();});
    on("wf-roi-apply","click",applyRoi);on("wf-roi-preview","click",roiPreview);on("wf-preview","click",preview);
    on("wf-core-enabled","change",()=>{if(!checked("wf-core-enabled")&&w.activeTarget==="Core"){w.fitInputs.Core=fitSettings("Core");restoreFitInputs(settingsForTarget("Interface"));}invalidateProfiles();w.roiPreview=null;syncOptions();drawRegionMap();});
    for(const id of ["wf-background","wf-component","wf-strip-kind","wf-image-flipx","wf-image-flipy","wf-structure-confirmed"])on(id,"change",render);
    on("wf-strip-view","change",()=>{syncStripColors();return loadStrip();});
    for(const id of ["wf-stripe-rows","wf-bulk-scope","wf-template-shape","wf-template-peaks","wf-template-low","wf-template-high","wf-align","wf-anchors"])on(id,"change",()=>{invalidateProfiles();render();});
    for(const id of ["wf-target-strip","wf-target-min","wf-target-max"])on(id,"change",()=>{syncStripColors();w.fit=w.preview=null;w.request++;renderResidual();});
    on("wf-structure-file","change",async()=>{const file=el("wf-structure-file").files[0];if(!file)return;
      const image=new Image(),url=URL.createObjectURL(file);try{await new Promise((resolve,reject)=>{image.onload=resolve;image.onerror=()=>reject(Error("结构图像读取失败。"));image.src=url;});}finally{URL.revokeObjectURL(url);}
      w.structure={image,name:file.name};el("wf-structure-confirmed").checked=false;
      if(![...el("wf-background").options].some(o=>o.value==="structure"))el("wf-background").add(new Option("已配准结构像","structure"));
      status("结构图像已载入；核对同视场坐标和方向后可选择该底图。");});
    on("wf-reference-file","change",async()=>{const file=el("wf-reference-file").files[0];if(!file)return;const obj=JSON.parse(await file.text());
      const x=obj.energy_meV,y=obj.intensity;if(!Array.isArray(x)||!Array.isArray(y)||x.length<3||x.length!==y.length||!x.concat(y).every(Number.isFinite)||x.some((v,i)=>i&&v<=x[i-1]))throw Error("参考谱需具有等长、有限的 energy_meV / intensity 数组，能量须递增。");
      w.reference={x,y,name:String(obj.name||file.name)};render();});
    w.defaultFit=fitSettings();
    EelsWorkflowRegions.bind();
    window.addEventListener("resize",()=>requestAnimationFrame(render));bindDrag();bindSeedDrag();syncOptions();
  }
  function preferenceSnapshot(){
    const inputs=structuredClone(w.fitInputs);
    try{inputs[w.activeTarget]=fitSettings(w.activeTarget);}catch{} // Keep editable text in the controls snapshot too.
    return {rois:structuredClone(w.rois),seeds:structuredClone(w.seeds),seedIndex:w.seedIndex,region:w.region,
      fitInputs:inputs,activeTarget:w.activeTarget,step:w.step,partitionEvidence:EelsWorkflowRegions.evidence()};
  }
  async function restorePreferences(saved,applyControls,sameData){
    const dependent=()=>JSON.stringify([
      ["Si","Ge","Interface","Core"].map(name=>["x_min","x_max","y_min","y_max"].map(key=>w.rois[name]?.[key])),
      checked("wf-core-enabled"),Number(val("wf-stripe-rows")),val("wf-bulk-scope"),
      val("wf-template-kind"),val("wf-template-shape"),Number(val("wf-template-peaks")),Number(val("wf-template-low")),Number(val("wf-template-high")),
      checked("wf-align"),checked("wf-align")?val("wf-anchors").trim().split(/\n/).map(line=>line.trim().split(/[\s,，;]+/).map(Number)):[]]);
    const before=dependent();
    applyControls();
    if(sameData){
      if(saved.rois)for(const [name,roi] of Object.entries(saved.rois))if(Object.hasOwn(w.rois,name)&&EelsPreferences.validRoi(roi))w.rois[name]=structuredClone(roi);
      EelsWorkflowRegions.restoreEvidence(saved.partitionEvidence);
    }
    if(Array.isArray(saved.seeds))saved.seeds.slice(0,8).forEach((seed,i)=>{
      if(typeof seed?.enabled==="boolean")w.seeds[i].enabled=seed.enabled;
      if(sameData&&EelsPreferences.validRoi(seed?.roi))w.seeds[i].roi=structuredClone(seed.roi);
    });
    w.seedIndex=Number.isInteger(saved.seedIndex)?Math.max(0,Math.min(7,saved.seedIndex)):0;
    if(Object.hasOwn(w.rois,saved.region))w.region=saved.region;
    el("wf-region").value=w.region;
    for(const name of ["Si","Ge","Interface","Core","strip"]){
      const cfg=saved.fitInputs?.[name];
      if(cfg&&typeof cfg==="object"&&!Array.isArray(cfg)&&(!cfg.centers||Array.isArray(cfg.centers))){
        w.fitInputs[name]=structuredClone(cfg);
        // Migrate old editable drafts only; completed fit recipes remain unchanged.
        if(amplitudeLimitSupported()&&!Object.hasOwn(cfg,"amplitude_limit"))
          w.fitInputs[name].amplitude_limit={method:"fixed_initial_local_extremum_v1",factor:1.5};
        if(!sameData&&name==="strip")w.fitInputs[name].selection=selection("strip");
      }
    }
    if(["Si","Ge","Interface","Core","strip"].includes(saved.activeTarget))el("wf-target").value=saved.activeTarget;
    syncSeedChoices();syncSeedInputs();syncRoiInputs();applyControls();syncOptions();
    w.activeTarget=val("wf-target");
    if(!sameData){el("wf-preprocessed").checked=false;el("wf-align").checked=false;}
    if(w.profiles&&before!==dependent()){invalidateProfiles();w.roiPreview=null;}
    w.fit=currentTargetFit(w.activeTarget);w.preview=w.fit;
    showStep(Number.isInteger(saved.step)&&saved.step>=1&&saved.step<=5?saved.step:1);
    if(w.profiles&&val("wf-strip-view")!==""&&w.strip?.index!==Number(val("wf-strip-view")))await loadStrip();
    render();
  }
  return {mount,open,resetDataset,render,state:w,api,run,plot,heatmap,applyRegionProposal,preferenceSnapshot,restorePreferences};
})();
