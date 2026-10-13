// Optional contrast proposals are separate from image switching and peak analysis.
const EelsRegionFit = (() => {
  let result = null;
  let pending = null;
  const timeoutMs = 12000;
  const ids = ["region-fit-si", "region-fit-low", "region-fit-high", "region-fit-ge"];
  const keys = ["si_bulk_percent", "interface_low_percent", "interface_high_percent", "ge_bulk_percent"];
  const enabled = () => byId("region-fit-enabled").checked;
  const supported = () => state.info?.region_fit_version === "contrast_region_fit_v1";
  const message = (text, error = false) => {
    byId("region-fit-status").textContent = text;
    byId("region-fit-status").classList.toggle("error", error);
  };
  function settings() {
    const thresholds = Object.fromEntries(ids.map((id, index) => {
      if (!byId(id).value.trim()) throw new Error("请填写完整的边界百分比。");
      return [keys[index], numberValue(id)];
    }));
    return { source: byId("region-fit-source").value, thresholds };
  }
  function current() {
    try {
      return enabled() && result?.dataset_id === state.info?.dataset_id && result.signature === JSON.stringify(settings())
        ? result.models[byId("region-fit-model").value] : null;
    } catch { return null; }
  }
  function syncControls() {
    const active = enabled();
    byId("region-fit-controls").hidden = !active;
    byId("region-fit-run").disabled = !active || !supported() || state.busy || Boolean(pending);
    byId("region-fit-cancel").disabled = !pending;
    byId("region-fit-apply").disabled = !active || state.busy || Boolean(pending) || !current()?.rois;
    for (const id of [...ids, "region-fit-source", "region-fit-model", "region-fit-full-y"]) byId(id).disabled = state.busy;
    if (active && state.info && !supported()) message("此会话尚不支持区域拟合，请通过 start_web.ps1 打开新版服务；当前选框保留。", true);
  }
  function cancel(text = "已取消拟合；本次结果不会应用，原选框保持不变。") {
    if (!pending) return;
    const task = pending;
    pending = null;
    clearTimeout(task.timer);
    task.controller.abort();
    message(text);
    syncControls();
  }
  function changed() {
    cancel();
    result = null;
    render();
    message("设置已修改，请重新拟合预览。现有选框和分析结果保持不变。");
    drawMap();
  }
  function resetForDataset() {
    cancel();
    result = null;
    render();
    message("请对当前数据拟合预览；只有应用到选框才会修改区域。");
  }
  async function run() {
    if (!enabled() || state.busy || pending || !supported()) return;
    let inputs;
    try { inputs = settings(); } catch (error) { message(error.message, true); return; }
    const task = { controller: new AbortController(), datasetId: state.info.dataset_id, signature: JSON.stringify(inputs) };
    pending = task;
    result = null;
    render();
    drawMap();
    message("正在拟合 erf 和 logistic…可随时取消。");
    task.timer = setTimeout(() => {
      if (pending !== task) return;
      cancel("等待拟合响应超时；原选框保持不变，可以重试。");
      byId("region-fit-status").classList.add("error");
    }, timeoutMs);
    try {
      const response = await requestApi("/api/region-fit", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(inputs), signal: task.controller.signal });
      if (pending !== task || !enabled()) return;
      if (state.info.dataset_id !== task.datasetId || response.dataset_id !== task.datasetId
          || JSON.stringify(settings()) !== task.signature) {
        message("数据或参数已变化，本次拟合未采用；请重新拟合。", true);
        return;
      }
      if (!response.models?.erf || !response.models?.logistic) throw new Error("拟合响应不完整，请重试。");
      result = { ...response, signature: task.signature };
      render();
      const selected = current();
      message(selected?.status !== "available" ? (selected?.reason || "拟合不可用，可切换模型检查。")
        : selected.apply_error || "拟合完成。可切换模型比较；点击应用到选框后仍可手动调整。",
        selected?.status !== "available" || Boolean(selected.apply_error));
      drawMap();
    } catch (error) {
      if (pending === task) message(error.name === "AbortError" ? "拟合已取消；原选框保持不变。" : error.message, true);
    } finally {
      clearTimeout(task.timer);
      if (pending === task) pending = null;
      syncControls();
    }
  }
  function render() {
    byId("region-fit-result").hidden = !result;
    const table = byId("region-fit-models");
    table.replaceChildren();
    if (result) for (const name of ["erf", "logistic"]) {
      const fit = result.models[name];
      const row = document.createElement("tr");
      row.classList.toggle("selected", name === byId("region-fit-model").value);
      for (const text of [name, fit.status === "available" ? decimal(fit.center_nm, 3) : "未确定",
        decimal(fit.width_10_90_nm, 3), fit.rmse == null ? "--" : fit.rmse.toExponential(2), decimal(fit.r_squared, 4)]) {
        const cell = document.createElement("td"); cell.textContent = text; row.appendChild(cell);
      }
      if (fit.reason) row.title = fit.reason;
      table.appendChild(row);
    }
    const fit = current();
    byId("region-fit-profile").hidden = fit?.status !== "available";
    byId("region-fit-boundaries").textContent = fit?.boundaries ?
      `Si 截止 ${decimal(fit.boundaries.si_end_nm, 3)} nm；界面 ${decimal(fit.boundaries.interface_start_nm, 3)}–${decimal(fit.boundaries.interface_end_nm, 3)} nm；Ge 起点 ${decimal(fit.boundaries.ge_start_nm, 3)} nm。`
      + (fit.apply_error || "间隔区域不纳入分析。") : "";
    syncControls();
    drawProfile();
  }
  function apply() {
    const fit = current();
    if (state.busy || pending || !fit?.rois) return;
    if (state.drag) finishMapDrag(true);
    const next = structuredClone(fit.rois);
    if (!byId("region-fit-full-y").checked) for (const region of Object.keys(next)) {
      next[region].y_min = state.rois[region].y_min;
      next[region].y_max = state.rois[region].y_max;
    }
    const changedRegions = Object.keys(next).filter(region => roiSignature(next[region]) !== roiSignature(state.rois[region]));
    for (const region of changedRegions) {
      state.rois[region] = next[region];
      invalidateRegionAnalysis(region);
    }
    drawMap(); renderResults();
    if (changedRegions.length) {
      message("已应用拟合选区。可继续拖动或缩放选框，程序不会自动复位。");
      setStatus("已按 " + byId("region-fit-model").value + " 拟合更新区域，已清除变更区域的旧分析结果。");
      previewActiveRegion({ silent: true });
    } else message("选框与拟合区域一致，保留现有分析结果。");
  }
  function lines(fit) {
    const b = fit.boundaries;
    return [[b.si_end_nm, regionColors.Si], [b.interface_start_nm, regionColors.Interface],
      [fit.center_nm, "#8d286e"], [b.interface_end_nm, regionColors.Interface], [b.ge_start_nm, regionColors.Ge]];
  }
  function drawOverlay(context) {
    const fit = current();
    if (fit?.status !== "available" || !byId("region-fit-show").checked) return;
    context.save();
    context.setLineDash([3, 4]);
    for (const [x, color] of lines(fit)) {
      if (x < state.info.x_nm[0] || x > state.info.x_nm[1]) continue;
      const start = mapPoint(x, state.info.y_nm[0]), end = mapPoint(x, state.info.y_nm[1]);
      for (const [stroke, width] of [["white", 3], [color, 1.5]]) {
        context.strokeStyle = stroke; context.lineWidth = width;
        context.beginPath(); context.moveTo(start.x, start.y); context.lineTo(end.x, end.y); context.stroke();
      }
    }
    context.restore();
  }
  function drawProfile() {
    if (!enabled() || !result) return;
    const canvas = byId("region-fit-canvas");
    const { context, width, height } = setupCanvas(canvas);
    const fit = current();
    if (fit?.status !== "available") return;
    const points = fit.profile_contrast;
    const ymin = points.reduce((a,b) => Math.min(a,b), -.08), ymax = points.reduce((a,b) => Math.max(a,b), 1.08);
    const xmin = state.info.x_nm[0], xmax = state.info.x_nm[1];
    const px = x => 42 + (x-xmin)/(xmax-xmin)*(width-55);
    const py = y => height-30-(y-ymin)/(ymax-ymin)*(height-44);
    context.font = "10px sans-serif";
    for (const y of [0, .5, 1]) {
      context.strokeStyle = "#e0e6e9"; context.beginPath(); context.moveTo(42, py(y)); context.lineTo(width-13, py(y)); context.stroke();
      context.fillStyle = "#526875"; context.fillText(Math.round(y*100)+"%", 7, py(y)+3);
    }
    for (let i=0; i<=4; i++) {
      const x = xmin+(xmax-xmin)*i/4;
      context.fillText(decimal(x, 1), px(x)-8, height-14);
    }
    context.fillText("x (nm)", width-41, height-2);
    context.save(); context.beginPath(); context.rect(42, 4, width-55, height-34); context.clip();
    context.setLineDash([3, 4]);
    for (const [x, color] of lines(fit)) {
      context.strokeStyle=color; context.beginPath(); context.moveTo(px(x), 4); context.lineTo(px(x), height-30); context.stroke();
    }
    context.setLineDash([]); context.strokeStyle="#2563eb"; context.lineWidth=2;
    context.beginPath(); fit.curve_x_nm.forEach((x, i) => i ? context.lineTo(px(x), py(fit.curve_contrast[i])) : context.moveTo(px(x), py(fit.curve_contrast[i]))); context.stroke();
    context.fillStyle="#364651"; result.profile_x_nm.forEach((x, i) => {
      if (i % Math.max(1, Math.ceil(points.length/2000))) return;
      context.beginPath(); context.arc(px(x), py(points[i]), 2.3, 0, Math.PI*2); context.fill();
    });
    context.restore();
  }
  function manualChanged() {
    if (current()) message("选框已手动调整；拟合边界仅作参考，不会自动复位。");
  }
  function bind() {
    byId("region-fit-enabled").addEventListener("change", () => {
      if (!enabled()) cancel();
      render(); drawMap();
    });
    for (const id of [...ids, "region-fit-source"]) byId(id).addEventListener("input", changed);
    byId("region-fit-model").addEventListener("change", () => {
      render(); drawMap();
      const fit = current();
      if (fit) message(fit.reason || fit.apply_error || "已切换拟合预览；点击应用到选框才会改变区域。", Boolean(fit.reason || fit.apply_error));
    });
    byId("region-fit-show").addEventListener("change", drawMap);
    byId("region-fit-run").addEventListener("click", run);
    byId("region-fit-cancel").addEventListener("click", () => cancel());
    byId("region-fit-apply").addEventListener("click", apply);
    window.addEventListener("resize", drawProfile);
    syncControls();
  }
  return { bind, syncControls, resetForDataset, drawOverlay, drawProfile, manualChanged };
})();
