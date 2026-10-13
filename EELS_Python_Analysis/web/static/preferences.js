// Persist editable UI drafts only. Result IDs, fitted arrays and imported file contents are excluded.
const EelsPreferences=(()=>{
  const VERSION="eels_ui_preferences_v1",writerId=crypto.randomUUID();
  let enabled=false,ready=false,bound=false,timer=null,inflight=false,again=false,sequence=0,lastSaved="",restored=null;
  const ignored=new Set(["import-file","import-path","wf-structure-file","wf-reference-file","wf-structure-confirmed","wf-core-candidate"]);
  const spatialFields=new Set(["wf-preprocessed","wf-align","wf-anchors","wf-roi-xmin","wf-roi-xmax","wf-roi-ymin","wf-roi-ymax",
    "wf-seed-xmin","wf-seed-xmax","wf-seed-ymin","wf-seed-ymax","wf-target-strip","wf-target-min","wf-target-max"]);
  const fields=()=>[...document.querySelectorAll("#fit-mode, #legacy-workspace input[id], #legacy-workspace select[id], #legacy-workspace textarea[id], #workflow-workspace input[id], #workflow-workspace select[id], #workflow-workspace textarea[id]")]
    .filter(node=>!ignored.has(node.id)&&node.type!=="file"&&node.type!=="radio");
  const message=(text,error=false)=>{const node=byId("preferences-status");node.textContent=text;node.classList.toggle("error",error);};
  const dataset=()=>({source:state.info.source,x_nm:state.info.x_nm,y_nm:state.info.y_nm,energy_meV:state.info.energy_meV,shape:state.info.shape});
  const datasetKey=d=>JSON.stringify(d&&[d.source,d.x_nm,d.y_nm,d.energy_meV,d.shape?.x,d.shape?.y,d.shape?.energy]);
  function validRoi(roi){
    if(!roi||![roi.x_min,roi.x_max,roi.y_min,roi.y_max].every(Number.isFinite))return false;
    const [xl,xr]=state.info.x_nm,[yl,yr]=state.info.y_nm;
    return roi.x_min<=roi.x_max&&roi.y_min<=roi.y_max&&roi.x_min>=xl&&roi.x_max<=xr&&roi.y_min>=yl&&roi.y_max<=yr;
  }
  function snapshot(){
    return {dataset:dataset(),controls:Object.fromEntries(fields().map(node=>[node.id,node.type==="checkbox"?node.checked:node.value])),
      details:Object.fromEntries([...document.querySelectorAll("details[id]")].map(node=>[node.id,node.open])),
      legacy:{rois:structuredClone(state.rois),activeRegion:state.activeRegion,curveVisibility:{...state.curveVisibility}},
      workflow:EelsWorkflow.preferenceSnapshot()};
  }
  function applyFields(controls,sameData){
    for(const node of fields()){
      if(!Object.hasOwn(controls,node.id)||(!sameData&&spatialFields.has(node.id)))continue;
      const value=controls[node.id];
      if(node.type==="checkbox"){if(typeof value==="boolean")node.checked=value;}
      else if(typeof value==="string"&&(node.tagName!=="SELECT"||[...node.options].some(option=>option.value===value&&!option.disabled)))node.value=value;
    }
  }
  async function call(options={}){
    const controller=new AbortController(),limit=setTimeout(()=>controller.abort(),4000);
    try{
      const response=await fetch("/api/ui-preferences",{...options,signal:controller.signal,headers:{"Content-Type":"application/json"}});
      const data=await response.json();if(!response.ok)throw Error(data.error||"参数保存失败。");return data;
    }catch(error){throw Error(error.name==="AbortError"?"参数记忆请求超时；可稍后重试保存。":error.message);}finally{clearTimeout(limit);}
  }
  async function restore({applySaved=true}={}){
    if(!hasDataset())return;
    if(state.info.ui_preferences_version!==VERSION){message("当前端口尚未支持重启后记忆参数，请运行 start_web.ps1 打开新版服务。");return;}
    try{
      const reply=await call();enabled=true;
      if(reply.record&&applySaved){
        restored=reply.record.snapshot;const sameData=datasetKey(restored.dataset)===datasetKey(dataset());
        await EelsWorkflow.open(); // Restore completed results before overlaying editable drafts.
        if(EelsWorkflow.state.busy){enabled=false;message("当前计算完成后刷新页面恢复参数记忆；运行中的任务保持不变。");return;}
        await EelsWorkflow.restorePreferences(restored.workflow||{},()=>applyFields(restored.controls||{},sameData),sameData);
        const legacy=restored.legacy||{};
        if(sameData)for(const [name,roi] of Object.entries(legacy.rois||{}))if(Object.hasOwn(state.rois,name)&&validRoi(roi))state.rois[name]=structuredClone(roi);
        if(["Si","Ge","Interface"].includes(legacy.activeRegion))state.activeRegion=legacy.activeRegion;
        document.querySelectorAll("input[name='region']").forEach(node=>node.checked=node.value===state.activeRegion);
        for(const key of Object.keys(state.curveVisibility))if(typeof legacy.curveVisibility?.[key]==="boolean")state.curveVisibility[key]=legacy.curveVisibility[key];
        for(const [id,open] of Object.entries(restored.details||{})){const node=byId(id);if(node?.tagName==="DETAILS")node.open=!!open;}
        EelsRegionFit.syncControls();
        message(sameData?"已恢复上次参数、选项及 ROI；分析结果需由当前服务提供或重新计算。":"已恢复数值与方法选项；当前文件不同，ROI 和预处理确认使用当前数据设置。");
      }else message("参数自动记忆已启用；修改后保存到项目，重启服务仍可恢复。");
    }catch(error){enabled=false;message(error.message+" 未覆盖已有参数文件；可刷新页面重试。",true);}
  }
  async function save(force=false){
    if(!hasDataset()||!enabled||!ready||state.drag||EelsWorkflow.state.drag||EelsWorkflow.state.seedDrag)return;
    clearTimeout(timer);const data=snapshot(),signature=JSON.stringify(data);
    if(!force&&signature===lastSaved)return;
    if(inflight){again=true;return;}
    inflight=true;message("正在记忆参数…");
    try{await call({method:"POST",body:JSON.stringify({snapshot:data,writer:{id:writerId,sequence:++sequence}})});lastSaved=signature;message("参数已记忆（含下拉选项、开关和 ROI）；重启后自动恢复。");}
    catch(error){message(error.message+" 现有分析不受影响，可点击保存重试。",true);}
    finally{inflight=false;if(again){again=false;schedule();}}
  }
  function schedule(){if(!hasDataset()||!enabled||!ready)return;clearTimeout(timer);timer=setTimeout(()=>save(),350);}
  function bind(){
    if(bound||!hasDataset())return;
    bound=true;
    ready=true;lastSaved=JSON.stringify(snapshot());
    byId("preferences-save").disabled=!enabled;
    byId("preferences-save").addEventListener("click",()=>save(true));
    for(const event of ["input","change","click","pointerup","pointercancel","keydown","toggle"])document.addEventListener(event,e=>{
      if(e.target.closest?.("#import-dialog")||e.target.id==="preferences-save")return;
      setTimeout(schedule,0); // Existing handlers apply selections and per-region inputs first.
    },true);
    window.addEventListener("pagehide",()=>{
      if(!hasDataset()||!enabled||!ready)return;const data=snapshot();if(JSON.stringify(data)===lastSaved)return;
      fetch("/api/ui-preferences",{method:"POST",keepalive:true,headers:{"Content-Type":"application/json"},
        body:JSON.stringify({snapshot:data,writer:{id:writerId,sequence:++sequence}})}).catch(()=>{});
    });
  }
  return {restore,bind,save,schedule,snapshot,validRoi};
})();
