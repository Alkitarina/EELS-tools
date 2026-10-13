const state = {
  info: null,
  map: null,
  signalMap: null,
  mapKind: "signal",
  references: null,
  referenceRequest: null,
  displayRequestId: 0,
  activeRegion: "Si",
  rois: {},
  fits: {},
  pcaResults: {},
  previews: {},
  previewRequestId: 0,
  curveVisibility: {
    raw: true,
    pca: true,
    fit: true,
    baseline: true,
  },
  drag: null,
  busy: false,
  mapLayout: null,
  spectrumLayout: null,
};

const regionColors = {
  Si: "#3976b5",
  Interface: "#dc7a2d",
  Ge: "#438b58",
};

const regionLabels = {
  Si: "Si",
  Interface: "界面",
  Ge: "Ge",
};

const componentColors = ["#3976b5", "#dc7a2d", "#438b58", "#b84742", "#7855aa", "#6b8e23"];

// RGB samples from MATLAB parula(16), interpolated for the signal map only.
const mapColors = [
  [62, 38, 168], [69, 56, 215], [72, 79, 242], [67, 103, 253],
  [47, 128, 250], [39, 151, 235], [28, 170, 223], [0, 185, 199],
  [41, 195, 170], [72, 203, 134], [129, 204, 89], [187, 196, 47],
  [234, 186, 48], [254, 199, 53], [245, 225, 40], [249, 251, 21],
];
const referencePalettes = {
  gray: [[0, 0, 0], [255, 255, 255]],
  diverging: [[49, 70, 173], [244, 244, 244], [177, 5, 47]],
};
const candidateColors = { total_counts: "#252932", spectral_contribution: "#a71c70" };

const byId = (id) => document.getElementById(id);
const regionLabel = (region) => regionLabels[region] || region;
const regionListLabel = (regions) => regions.map(regionLabel).join("、");
const isPcaMode = () => byId("fit-mode").value === "pca";
const hasDataset = () => Boolean(state.info) && state.info.dataset_loaded !== false;

async function requestApi(url, options) {
  const response = await fetch(url, {
    ...options,
    headers: { ...options?.headers, ...(state.info ? { "X-EELS-Dataset": state.info.dataset_id } : {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || "本地分析服务返回了错误。");
  }
  return payload;
}

function numberValue(id) {
  const value = Number(byId(id).value);
  if (!Number.isFinite(value)) {
    throw new Error("请输入有效数值。");
  }
  return value;
}

function positiveNumberValue(id) {
  const value = numberValue(id);
  if (!(value > 0)) {
    throw new Error("该数值必须大于 0。");
  }
  return value;
}

function integerValue(id, lower, upper) {
  const value = numberValue(id);
  if (!Number.isInteger(value) || value < lower || value > upper) {
    throw new Error("请输入 " + lower + " 到 " + upper + " 之间的整数。");
  }
  return value;
}

function settingsPayload() {
  const base = {
    fit_min: numberValue("fit-min"),
    fit_max: numberValue("fit-max"),
  };
  if (base.fit_min >= base.fit_max) {
    throw new Error("起始能量必须小于终止能量。");
  }

  if (isPcaMode()) {
    const prominence = numberValue("pca-min-prominence");
    if (prominence < 0 || prominence >= 100) {
      throw new Error("最小突出度必须在 0% 到 100% 之间。");
    }
    return {
      ...base,
      config: {
        mode: "pca",
        component_count: integerValue("pca-components", 1, 64),
        peak_count: integerValue("pca-peak-count", 1, 16),
        min_peak_distance_meV: positiveNumberValue("pca-min-distance"),
        min_prominence_percent: prominence,
      },
    };
  }

  if (byId("fit-mode").value === "auto") {
    const fwhmLower = positiveNumberValue("auto-fwhm-lower");
    const fwhmUpper = positiveNumberValue("auto-fwhm-upper");
    if (fwhmLower >= fwhmUpper) {
      throw new Error("FWHM 下限必须小于上限。");
    }
    return {
      ...base,
      config: {
        mode: "auto",
        peak_count: integerValue("peak-count", 1, 16),
        search_starts: integerValue("search-starts", 1, 256),
        fwhm_lower: fwhmLower,
        fwhm_upper: fwhmUpper,
        include_baseline: byId("baseline").checked,
      },
    };
  }
  return {
    ...base,
    config: {
      mode: "manual",
      centers: byId("centers").value,
      center_lower: byId("center-lower").value,
      center_upper: byId("center-upper").value,
      fwhm_lower: byId("fwhm-lower").value,
      fwhm_upper: byId("fwhm-upper").value,
      include_baseline: byId("baseline").checked,
    },
  };
}

function syncFitMode() {
  const loaded = hasDataset();
  byId("empty-workspace").hidden = loaded;
  document.querySelector(".method-switch").hidden = !loaded;
  document.querySelector(".preferences-bar").hidden = !loaded;
  if (!loaded) {
    byId("legacy-workspace").hidden = byId("workflow-workspace").hidden = true;
    return;
  }
  const mode = byId("fit-mode").value;
  const workflow = mode === "multivariate";
  byId("legacy-workspace").hidden = workflow;
  byId("workflow-workspace").hidden = !workflow;
  byId("method-description").textContent = workflow
    ? "全像素分解 → 空间分区 → 条带体谱 → 有符号残差 → 联合拟合检查"
    : mode === "pca" ? "PCA 重建谱与可分辨峰测量；原有选区、参数和结果保留。"
    : "高斯线型分峰与不确定度；仅显示当前方法的模型参数。";
  if (workflow) { EelsWorkflow.open(); return; }
  byId("method-settings-title").textContent = mode === "pca" ? "PCA 参数" : "高斯模型参数";
  const automatic = mode === "auto";
  const pca = mode === "pca";
  byId("auto-fields").classList.toggle("is-hidden", !automatic);
  byId("manual-fields").classList.toggle("is-hidden", mode !== "manual");
  byId("pca-fields").classList.toggle("is-hidden", !pca);
  byId("bootstrap-title").textContent = pca ? "空间块自助法" : "残差块自助法";
  byId("block-size-label").textContent = pca ? "空间块边长 (px)" : "能量块长度";
  byId("fit-active").textContent = pca ? "分析当前区域" : "拟合当前区域";
  byId("fit-all").textContent = pca ? "分析全部区域" : "拟合全部区域";
  renderResults();
  drawMap();
}

function roiSignature(roi) {
  if (!roi) {
    return "";
  }
  return [roi.x_min, roi.x_max, roi.y_min, roi.y_max].join("|");
}

function activePreview() {
  return state.previews[state.activeRegion] || null;
}

function activeSpectrum() {
  if (isPcaMode()) {
    const pca = state.pcaResults[state.activeRegion] || null;
    if (pca) {
      return { data: pca, fit: null, pca };
    }
    return { data: activePreview(), fit: null, pca: null };
  }
  const fit = state.fits[state.activeRegion] || null;
  if (fit) {
    return { data: fit, fit, pca: null };
  }
  return { data: activePreview(), fit: null, pca: null };
}

function invalidateRegionAnalysis(region) {
  delete state.fits[region];
  delete state.pcaResults[region];
  delete state.previews[region];
  state.previewRequestId += 1;
}

function invalidateAllAnalyses() {
  state.fits = {};
  state.pcaResults = {};
  state.previews = {};
  state.previewRequestId += 1;
}

function updateLegendButtons(hasFit, hasPca) {
  const series = [
    ["raw", true],
    ["pca", hasPca],
    ["fit", hasFit],
    ["baseline", hasFit],
  ];
  series.forEach(([name, available]) => {
    const button = byId("toggle-" + name);
    button.disabled = !available;
    button.setAttribute(
      "aria-pressed",
      String(Boolean(available && state.curveVisibility[name]))
    );
  });
}

function toggleCurve(name) {
  const { fit, pca } = activeSpectrum();
  if ((name === "fit" || name === "baseline") && !fit) {
    return;
  }
  if (name === "pca" && !pca) {
    return;
  }
  state.curveVisibility[name] = !state.curveVisibility[name];
  drawSpectrum();
}

function setStatus(message, error = false) {
  const status = byId("status");
  status.textContent = message;
  status.classList.toggle("error", error);
}

function setBusy(busy) {
  if (busy && state.drag) finishMapDrag(true);
  state.busy = busy;
  document.querySelectorAll(".action-button, .controls-band input, .controls-band select, #update-map, #map-kind").forEach((button) => {
    button.disabled = busy;
  });
  byId("import-workflow").disabled = busy || state.info?.workflow_import_version !== "saved_workflow_json_v1";
  byId("empty-import-workflow").disabled = byId("import-workflow").disabled;
  EelsRegionFit.syncControls();
}

function updateProgress(completed, total, label) {
  const progress = byId("progress");
  progress.max = Math.max(total, 1);
  progress.value = Math.min(completed, total);
  byId("progress-label").textContent = label;
}

function decimal(value, digits = 3) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "--";
  }
  return Number(value).toFixed(digits);
}

function colorScale(value, palette = mapColors) {
  const position = Math.max(0, Math.min(1, value)) * (palette.length - 1);
  const index = Math.min(Math.floor(position), palette.length - 2);
  const fraction = position - index;
  const rgb = palette[index].map((channel, component) =>
    Math.round(channel + (palette[index + 1][component] - channel) * fraction)
  );
  return "rgb(" + rgb.join(",") + ")";
}

function setupCanvas(canvas) {
  const bounds = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, Math.round(bounds.width));
  const height = Math.max(1, Math.round(bounds.height));
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const context = canvas.getContext("2d");
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, width, height);
  return { context, width, height };
}

function drawAxis(context, layout, xLabel, yLabel) {
  const { left, top, width, height } = layout;
  const yLabelOffset = layout.yLabelOffset || 38;
  context.strokeStyle = "#cbd5e1";
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(left, top);
  context.lineTo(left, top + height);
  context.lineTo(left + width, top + height);
  context.stroke();
  context.fillStyle = "#64748b";
  context.font = "12px Segoe UI, Arial, sans-serif";
  context.textAlign = "center";
  context.fillText(xLabel, left + width / 2, top + height + 30);
  context.save();
  context.translate(left - yLabelOffset, top + height / 2);
  context.rotate(-Math.PI / 2);
  context.fillText(yLabel, 0, 0);
  context.restore();
}

function drawTicks(context, layout, xMin, xMax, yMin, yMax, formatter = null) {
  const tick = formatter || ((value) => decimal(value, 1));
  const { left, top, width, height } = layout;
  context.strokeStyle = "#e8eef6";
  context.fillStyle = "#64748b";
  context.lineWidth = 1;
  context.font = "11px Segoe UI, Arial, sans-serif";
  for (let index = 0; index <= 4; index += 1) {
    const fraction = index / 4;
    const x = left + width * fraction;
    const y = top + height * (1 - fraction);
    context.beginPath();
    context.moveTo(x, top);
    context.lineTo(x, top + height);
    context.moveTo(left, y);
    context.lineTo(left + width, y);
    context.stroke();
    context.textAlign = "center";
    if (index % (layout.xTickStride || 1) === 0) context.fillText(tick(xMin + (xMax - xMin) * fraction), x, top + height + 16);
    context.textAlign = "right";
    context.fillText(tick(yMin + (yMax - yMin) * fraction), left - 7, y + 4);
  }
}

function mapPoint(x, y) {
  const layout = state.mapLayout;
  const xRange = state.info.x_nm;
  const yRange = state.info.y_nm;
  return {
    x: layout.left + ((x - xRange[0]) / (xRange[1] - xRange[0] || 1)) * layout.width,
    y: layout.top + layout.height - ((y - yRange[0]) / (yRange[1] - yRange[0] || 1)) * layout.height,
  };
}

function clientToMap(clientX, clientY, allowOutside = false) {
  const canvas = byId("map-canvas");
  const bounds = canvas.getBoundingClientRect();
  const layout = state.mapLayout;
  if (!layout || !state.info) return null;
  const px = clientX - bounds.left;
  const py = clientY - bounds.top;
  const inside = (
    px >= layout.left &&
    px <= layout.left + layout.width &&
    py >= layout.top &&
    py <= layout.top + layout.height
  );
  if (!inside && !allowOutside) {
    return null;
  }
  const xRange = state.info.x_nm;
  const yRange = state.info.y_nm;
  return {
    x: xRange[0] + ((px - layout.left) / layout.width) * (xRange[1] - xRange[0]),
    y: yRange[0] + ((layout.top + layout.height - py) / layout.height) * (yRange[1] - yRange[0]),
  };
}

function drawMap() {
  const canvas = byId("map-canvas");
  if (!state.map || !state.info) {
    setupCanvas(canvas);
    state.mapLayout = null;
    byId("map-window").textContent = "暂无信号图";
    byId("map-min").textContent = "";
    byId("map-max").textContent = "";
    byId("roi-readout").textContent = "";
    return;
  }
  const drawing = setupCanvas(canvas);
  const { context, width, height } = drawing;
  const rows = state.map.values.length;
  const columns = state.map.values[0].length;
  const xSpan = state.info.x_nm[1] - state.info.x_nm[0];
  const ySpan = state.info.y_nm[1] - state.info.y_nm[0];
  const aspect = xSpan > 0 && ySpan > 0 ? xSpan / ySpan : columns / rows;
  const plotWidth = Math.min(width - 92, (height - 20) * aspect);
  const plotHeight = plotWidth / aspect;
  const layout = {
    left: (width - plotWidth - 72) / 2,
    top: (height - plotHeight) / 2,
    width: plotWidth,
    height: plotHeight,
  };
  state.mapLayout = layout;
  const cellWidth = layout.width / columns;
  const cellHeight = layout.height / rows;
  // Match imagesc's automatic color limits, without clipping map percentiles.
  let displayMin = Infinity;
  let displayMax = -Infinity;
  state.map.values.forEach((row) => row.forEach((value) => {
    if (value !== null && Number.isFinite(value)) {
      displayMin = Math.min(displayMin, value);
      displayMax = Math.max(displayMax, value);
    }
  }));
  const range = displayMax - displayMin || 1;
  const palette = referencePalettes[state.map.palette] || mapColors;

  context.save();
  context.beginPath();
  context.rect(layout.left, layout.top, layout.width, layout.height);
  context.clip();
  for (let row = 0; row < rows; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      const value = state.map.values[row][column];
      const normalized = (value - displayMin) / range;
      context.fillStyle = value !== null && Number.isFinite(value) ? colorScale(normalized, palette) : "#ffffff";
      context.fillRect(
        layout.left + column * cellWidth,
        layout.top + layout.height - (row + 1) * cellHeight,
        Math.ceil(cellWidth) + 0.5,
        Math.ceil(cellHeight) + 0.5
      );
    }
  }
  context.restore();

  // Display overlays share the physical map coordinates and never enter ROI hit testing.
  if (byId("show-centers").checked) {
    for (const center of state.references?.centers || []) {
      if (center.status !== "available") continue;
      const from = mapPoint(center.x_nm, state.info.y_nm[0]);
      const to = mapPoint(center.x_nm, state.info.y_nm[1]);
      context.save();
      context.setLineDash([6, 5]);
      for (const [color, lineWidth] of [["#ffffff", 4], [candidateColors[center.kind], 2]]) {
        context.strokeStyle = color;
        context.lineWidth = lineWidth;
        context.beginPath();
        context.moveTo(from.x, from.y);
        context.lineTo(to.x, to.y);
        context.stroke();
      }
      context.restore();
    }
  }

  EelsRegionFit.drawOverlay(context);
  const orderedRegions = Object.entries(state.rois).sort(([a], [b]) =>
    Number(a === state.activeRegion) - Number(b === state.activeRegion)
  );
  orderedRegions.forEach(([region, roi]) => {
    const origin = mapPoint(roi.x_min, roi.y_min);
    const end = mapPoint(roi.x_max, roi.y_max);
    const active = region === state.activeRegion;
    if (active) {
      context.strokeStyle = "#ffffff";
      context.lineWidth = 4;
      context.strokeRect(origin.x, end.y, end.x - origin.x, origin.y - end.y);
    }
    context.strokeStyle = regionColors[region];
    context.lineWidth = active ? 2 : 1.5;
    context.globalAlpha = active ? 1 : 0.6;
    context.strokeRect(
      origin.x,
      end.y,
      Math.max(1, end.x - origin.x),
      Math.max(1, origin.y - end.y)
    );
    context.globalAlpha = 1;
    if (active) {
      EelsRoi.handles(roiCanvasRect(roi)).forEach(({ x, y }) => {
        context.fillStyle = "#ffffff";
        context.fillRect(x - 4, y - 4, 8, 8);
        context.lineWidth = 1.5;
        context.strokeRect(x - 4, y - 4, 8, 8);
      });
    }
  });

  byId("map-min").textContent = displayMin.toExponential(2);
  byId("map-max").textContent = displayMax.toExponential(2);
  const scale = document.querySelector(".map-scale");
  scale.style.left = (10 + layout.left + layout.width + 16) + "px";
  scale.style.top = (10 + layout.top) + "px";
  const scaleBar = scale.querySelector("i");
  scaleBar.style.height = Math.max(12, layout.height - 38) + "px";
  scaleBar.style.background = "linear-gradient(to top," +
    palette.map((rgb) => "rgb(" + rgb.join(",") + ")").join(",") + ")";
  byId("map-title").textContent = state.map.label || "信号图";
  byId("map-window").textContent =
    state.map.description || ("积分范围：" + decimal(state.map.fit_min, 2) + " 至 " + decimal(state.map.fit_max, 2) + " meV");
  renderRoiReadout();
}

function renderCandidateReadout() {
  const readout = byId("candidate-readout");
  readout.replaceChildren();
  for (const center of state.references?.centers || []) {
    const label = document.createElement("span");
    label.style.color = candidateColors[center.kind];
    if (center.status === "available") {
      label.appendChild(document.createElement("i"));
      label.appendChild(document.createTextNode(center.label + " ≈ " + decimal(center.x_nm, 1) + " nm"));
      label.title = "沿 y 平均的误差函数过渡中点；不代表结构界面精度。";
    } else {
      label.textContent = center.label + "：未确定";
      label.title = center.reason;
    }
    readout.appendChild(label);
  }
}

async function loadReferenceMaps() {
  if (state.references) return state.references;
  if (state.referenceRequest) return state.referenceRequest;
  if (state.info?.reference_maps_version !== "reference_maps_v1") {
    throw new Error("此会话使用旧版服务；通过 start_web.ps1 打开新版后可用参考图。");
  }
  const datasetId = state.info.dataset_id;
  const pending = requestApi("/api/reference-maps").then((data) => {
    if (state.info.dataset_id !== datasetId || data.dataset_id !== datasetId) return null;
    state.references = data;
    renderCandidateReadout();
    drawMap();
    return data;
  }).finally(() => {
    if (state.referenceRequest === pending) state.referenceRequest = null;
  });
  state.referenceRequest = pending;
  return pending;
}

async function switchMapKind() {
  // Intentionally separate from updateMap: no analysis invalidation, preview,
  // default ROIs, clipping, or writes to any selected region on a display switch.
  const kind = byId("map-kind").value;
  const requestId = ++state.displayRequestId;
  const datasetId = state.info?.dataset_id;
  const note = byId("reference-note");
  try {
    const references = kind === "signal" ? state.references : await loadReferenceMaps();
    if (requestId !== state.displayRequestId || datasetId !== state.info?.dataset_id) return;
    const nextMap = kind === "signal" ? state.signalMap : references?.maps[kind];
    if (!nextMap || nextMap.available === false) {
      throw new Error(nextMap?.reason || "当前底图尚未就绪，请先更新信号图。");
    }
    state.map = nextMap;
    state.mapKind = kind;
    note.textContent = references?.note || "切换底图仅改变显示，保留三个区域选框。";
    note.classList.remove("error");
    drawMap();
  } catch (error) {
    if (requestId !== state.displayRequestId || datasetId !== state.info?.dataset_id) return;
    byId("map-kind").value = state.mapKind;
    note.textContent = error.message + " 原底图与选框保持不变。";
    note.classList.add("error");
  }
}

async function prepareReferenceDisplay() {
  try {
    await loadReferenceMaps();
    const note = byId("reference-note");
    note.textContent = state.references?.note || "参考图尚未就绪。";
    note.classList.remove("error");
  } catch (error) {
    byId("reference-note").textContent = error.message;
  }
}

function renderRoiReadout() {
  const roi = state.rois[state.activeRegion];
  if (!roi) {
    return;
  }
  byId("roi-readout").textContent =
    regionLabel(state.activeRegion) +
    "：x " + decimal(roi.x_min, 2) + "-" + decimal(roi.x_max, 2) +
    " nm，y " + decimal(roi.y_min, 2) + "-" + decimal(roi.y_max, 2) + " nm";
}

function drawLine(context, points, color, width, dash) {
  if (!points.length) {
    return;
  }
  context.beginPath();
  context.setLineDash(dash || []);
  points.forEach((point, index) => {
    if (index === 0) {
      context.moveTo(point.x, point.y);
    } else {
      context.lineTo(point.x, point.y);
    }
  });
  context.strokeStyle = color;
  context.lineWidth = width;
  context.stroke();
  context.setLineDash([]);
}

function drawSpectrum() {
  const canvas = byId("spectrum-canvas");
  const drawing = setupCanvas(canvas);
  const { context, width, height } = drawing;
  const layout = {
    left: 76,
    top: 16,
    width: width - 94,
    height: height - 57,
    yLabelOffset: 60,
  };
  state.spectrumLayout = layout;
  const { data, fit, pca } = activeSpectrum();
  const hasFit = Boolean(fit);
  const hasPca = Boolean(pca);
  updateLegendButtons(hasFit, hasPca);
  context.fillStyle = "#64748b";
  context.font = "13px Segoe UI, Arial, sans-serif";

  if (!data) {
    drawAxis(context, layout, "能量损失 (meV)", "平均强度 (a.u.)");
    context.textAlign = "center";
    context.fillText("在信号图上拖拽选区后将在此显示原始谱。", layout.left + layout.width / 2, layout.top + layout.height / 2);
    return;
  }

  const values = [...data.intensity];
  if (hasFit) {
    values.push(...fit.fitted, ...fit.baseline, ...fit.components.flat());
  }
  if (hasPca) {
    values.push(...pca.reconstructed);
  }
  const finiteValues = values.filter(Number.isFinite);
  if (!finiteValues.length) {
    drawAxis(context, layout, "能量损失 (meV)", "平均强度 (a.u.)");
    context.textAlign = "center";
    context.fillText("所选区域没有可绘制的有效信号。", layout.left + layout.width / 2, layout.top + layout.height / 2);
    return;
  }
  let yMin = Math.min(...finiteValues);
  let yMax = Math.max(...finiteValues);
  const padding = Math.max((yMax - yMin) * 0.08, 1);
  yMin -= padding;
  yMax += padding;
  const xMin = data.energy_meV[0];
  const xMax = data.energy_meV[data.energy_meV.length - 1];
  const toPoint = (x, y) => ({
    x: layout.left + ((x - xMin) / (xMax - xMin)) * layout.width,
    y: layout.top + layout.height - ((y - yMin) / (yMax - yMin)) * layout.height,
  });

  drawAxis(context, layout, "能量损失 (meV)", "平均强度 (a.u.)");
  drawTicks(context, layout, xMin, xMax, yMin, yMax);

  if (state.curveVisibility.raw) {
    const raw = data.energy_meV.map((energy, index) => toPoint(energy, data.intensity[index]));
    context.fillStyle = "#1e2528";
    raw.forEach((point, index) => {
      if (index % 2 === 0) {
        context.fillRect(point.x - 1, point.y - 1, 2, 2);
      }
    });
  }

  if (hasPca && state.curveVisibility.pca) {
    drawLine(
      context,
      pca.energy_meV.map((energy, index) => toPoint(energy, pca.reconstructed[index])),
      "#2563eb",
      2.3
    );
    pca.peaks.forEach((peak) => {
      const left = toPoint(peak.left_meV, peak.width_height);
      const right = toPoint(peak.right_meV, peak.width_height);
      const apex = toPoint(peak.center_meV, peak.height);
      context.strokeStyle = "#2563eb";
      context.lineWidth = 1.4;
      context.beginPath();
      context.moveTo(left.x, left.y);
      context.lineTo(right.x, right.y);
      context.moveTo(left.x, left.y - 4);
      context.lineTo(left.x, left.y + 4);
      context.moveTo(right.x, right.y - 4);
      context.lineTo(right.x, right.y + 4);
      context.stroke();
      context.fillStyle = "#2563eb";
      context.beginPath();
      context.arc(apex.x, apex.y, 3.2, 0, Math.PI * 2);
      context.fill();
    });
  }

  if (hasFit && state.curveVisibility.fit) {
    drawLine(
      context,
      fit.energy_meV.map((energy, index) => toPoint(energy, fit.fitted[index])),
      "#c13d38",
      2.3
    );
    fit.components.forEach((component, componentIndex) => {
      drawLine(
        context,
        fit.energy_meV.map((energy, index) => toPoint(energy, component[index])),
        componentColors[componentIndex % componentColors.length],
        1.25
      );
    });
  }

  if (hasFit && state.curveVisibility.baseline) {
    drawLine(
      context,
      fit.energy_meV.map((energy, index) => toPoint(energy, fit.baseline[index])),
      "#728088",
      1.2,
      [5, 4]
    );
  }

  const anyVisible =
    state.curveVisibility.raw ||
    (hasPca && state.curveVisibility.pca) ||
    (hasFit && (state.curveVisibility.fit || state.curveVisibility.baseline));
  if (!anyVisible) {
    context.fillStyle = "#64748b";
    context.textAlign = "center";
    context.fillText("当前没有显示曲线。", layout.left + layout.width / 2, layout.top + layout.height / 2);
  }
}

function renderResults() {
  const table = byId("peak-table");
  if (isPcaMode()) {
    byId("width-heading").textContent = "半突出度宽度 (meV)";
    byId("measure-heading").textContent = "突出度";
    const pca = state.pcaResults[state.activeRegion];
    if (pca) {
      if (pca.peaks.length) {
        table.innerHTML = pca.peaks.map((peak) => {
          const centerCi = peak.center_ci95
            ? decimal(peak.center_ci95[0]) + " - " + decimal(peak.center_ci95[1])
            : "--";
          const widthCi = peak.width_ci95
            ? decimal(peak.width_ci95[0]) + " - " + decimal(peak.width_ci95[1])
            : "--";
          return (
            "<tr>" +
            "<td>" + regionLabel(pca.region) + "</td>" +
            "<td>峰 " + peak.index + "</td>" +
            "<td>" + decimal(peak.center_meV) + "</td>" +
            "<td>" + centerCi + "</td>" +
            "<td>" + decimal(peak.width_meV) + "</td>" +
            "<td>" + widthCi + "</td>" +
            "<td>" + decimal(peak.prominence, 2) + "</td>" +
            "</tr>"
          );
        }).join("");
      } else {
        table.innerHTML = "<tr><td colspan='7'>当前阈值下没有分辨出局部峰。</td></tr>";
      }
      byId("fit-state").textContent = pca.bootstrap ? "PCA 不确定度完成" : "PCA 分析完成";
      byId("fit-caption").textContent =
        regionLabel(pca.region) + " 区域，" + pca.pixel_count + " 个像素，" +
        pca.component_count + " 个主成分保留 " +
        decimal(pca.retained_variance_ratio * 100, 2) + "% 方差，重建 RMSE " +
        decimal(pca.reconstruction_rmse, 3);
      if (pca.bootstrap) {
        byId("result-note").textContent =
          "95% 区间来自重新计算 PCA 的空间块自助法：" +
          pca.bootstrap.successful + "/" + pca.bootstrap.requested + " 次成功；" +
          "宽度为半突出度宽度，不是高斯 FWHM。";
      } else if (pca.unresolved_peak_count > 0) {
        byId("result-note").textContent =
          "目标 " + pca.requested_peak_count + " 个峰，当前只分辨出 " +
          pca.detected_peak_count + " 个；其余峰没有形成满足阈值的独立局部极大值。";
      } else {
        byId("result-note").textContent =
          "峰位和宽度均直接测量自 PCA 重建谱；宽度为半突出度宽度，不是高斯 FWHM。";
      }
      drawSpectrum();
      return;
    }
  } else {
    byId("width-heading").textContent = "FWHM (meV)";
    byId("measure-heading").textContent = "面积";
  }

  const fit = isPcaMode() ? null : state.fits[state.activeRegion];
  if (!fit) {
    table.innerHTML = "<tr><td colspan='7'>尚无分析结果。</td></tr>";
    const preview = activePreview();
    if (preview) {
      byId("fit-state").textContent = "原始谱预览";
      byId("fit-caption").textContent =
        regionLabel(preview.region) + " 区域，" + preview.pixel_count + " 个像素，原始平均谱预览";
      byId("result-note").textContent = isPcaMode()
        ? "当前显示所选 ROI 的原始平均谱；执行 PCA 分析后将在此显示可分辨峰。"
        : "当前显示所选 ROI 的原始平均谱；执行拟合后将在此显示峰参数。";
    } else {
      byId("fit-state").textContent = "未分析";
      byId("fit-caption").textContent = "选择空间区域后分析其平均谱";
      byId("result-note").textContent = "完成分析后将在此显示参数。";
    }
    drawSpectrum();
    return;
  }

  table.innerHTML = fit.peaks.map((peak) => {
    const centerCi = peak.center_ci95 ? decimal(peak.center_ci95[0]) + " - " + decimal(peak.center_ci95[1]) : "--";
    const widthCi = peak.fwhm_ci95 ? decimal(peak.fwhm_ci95[0]) + " - " + decimal(peak.fwhm_ci95[1]) : "--";
    return (
      "<tr>" +
      "<td>" + regionLabel(fit.region) + "</td>" +
      "<td>峰 " + peak.index + "</td>" +
      "<td>" + decimal(peak.center_meV) + "</td>" +
      "<td>" + centerCi + "</td>" +
      "<td>" + decimal(peak.fwhm_meV) + "</td>" +
      "<td>" + widthCi + "</td>" +
      "<td>" + decimal(peak.area, 2) + "</td>" +
      "</tr>"
    );
  }).join("");
  byId("fit-state").textContent = fit.bootstrap ? "不确定度完成" : "拟合完成";
  byId("fit-caption").textContent =
    regionLabel(fit.region) + " 区域，" + fit.pixel_count + " 个像素，残差 RMSE " + decimal(fit.rmse, 3);
  if (fit.bootstrap) {
    byId("result-note").textContent =
      "95% 区间来自残差块自助法：" + fit.bootstrap.successful + "/" + fit.bootstrap.requested + " 次重拟合成功。";
  } else if (fit.auto_search) {
    byId("result-note").textContent =
      "自动寻优：" + fit.auto_search.starts_successful + "/" + fit.auto_search.starts_requested + " 个起点得到有效解；已选取已尝试起点中残差 RMSE 最小的结果。";
  } else {
    byId("result-note").textContent = "当前结果使用手动约束；尚未计算不确定度。";
  }
  drawSpectrum();
}

async function previewActiveRegion({ silent = false } = {}) {
  const region = state.activeRegion;
  const roi = state.rois[region];
  if (!state.info || !roi) {
    return false;
  }

  let fitMin;
  let fitMax;
  try {
    fitMin = numberValue("fit-min");
    fitMax = numberValue("fit-max");
  } catch (error) {
    setStatus(error.message, true);
    return false;
  }
  if (fitMin >= fitMax) {
    setStatus("起始能量必须小于终止能量。", true);
    return false;
  }

  const requestId = ++state.previewRequestId;
  const signature = roiSignature(roi);
  if (!silent) {
    byId("fit-state").textContent = "正在预览";
    byId("fit-caption").textContent = "正在读取所选区域的原始平均谱";
    drawSpectrum();
  }

  try {
    const preview = await requestApi("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        region,
        roi,
        fit_min: fitMin,
        fit_max: fitMax,
      }),
    });
    const currentWindowMatches =
      Number(byId("fit-min").value) === fitMin &&
      Number(byId("fit-max").value) === fitMax;
    if (
      requestId !== state.previewRequestId ||
      roiSignature(state.rois[region]) !== signature ||
      !currentWindowMatches
    ) {
      return false;
    }
    state.previews[region] = preview;
    if (region === state.activeRegion) {
      renderResults();
      if (!silent) {
        setStatus(regionLabel(region) + " 区域的原始谱预览已更新。");
      }
    }
    return true;
  } catch (error) {
    if (requestId === state.previewRequestId && region === state.activeRegion) {
      setStatus(error.message, true);
    }
    return false;
  }
}

function constrainRoisToMap() {
  const notices = [];
  for (const region of Object.keys(state.info.default_rois)) {
    const previous = state.rois[region] || state.info.default_rois[region];
    const roi = { ...previous };
    let reset = false;
    for (const axis of ["x", "y"]) {
      const values = state.map[axis + "_nm"];
      const low = axis + "_min";
      const high = axis + "_max";
      roi[low] = Math.max(previous[low], values[0]);
      roi[high] = Math.min(previous[high], values[values.length - 1]);
      if (!values.some((value) => value >= roi[low] && value <= roi[high])) {
        const fallback = state.info.default_rois[region];
        roi[low] = fallback[low];
        roi[high] = fallback[high];
        if (!values.some((value) => value >= roi[low] && value <= roi[high])) {
          roi[low] = values[0];
          roi[high] = values[values.length - 1];
        }
        reset = true;
      }
    }
    state.rois[region] = roi;
    if (roiSignature(previous) !== roiSignature(roi)) {
      invalidateRegionAnalysis(region);
      notices.push(regionLabel(region) + (reset
        ? " 选区未包含可用像素，已调整到新数据内，请确认位置。"
        : " 选区超出新数据边界，已裁剪到可用范围，请确认位置。"));
    }
  }
  return notices;
}

async function updateMap() {
  try {
    const fitMin = numberValue("fit-min");
    const fitMax = numberValue("fit-max");
    if (fitMin >= fitMax) {
      throw new Error("起始能量必须小于终止能量。");
    }
    const windowChanged =
      !state.signalMap ||
      state.signalMap.fit_min !== fitMin ||
      state.signalMap.fit_max !== fitMax;
    if (windowChanged) {
      invalidateAllAnalyses();
    }
    const nextSignal = await requestApi(
      "/api/map?fit_min=" + encodeURIComponent(fitMin) + "&fit_max=" + encodeURIComponent(fitMax)
    );
    state.signalMap = nextSignal;
    if (state.mapKind === "signal" || !state.map) state.map = nextSignal;
    const notices = constrainRoisToMap();
    const [lower, upper] = state.info.energy_meV;
    if (fitMin < lower || fitMax > upper) {
      notices.push("能量设置保持不变；新数据仅覆盖 " + decimal(lower, 2) + " 至 " +
        decimal(upper, 2) + " meV，当前仅使用交集内的通道，请确认拟合范围。");
    }
    drawMap();
    const previewed = await previewActiveRegion({ silent: true });
    if (previewed) {
      setStatus("信号图和原始谱预览已更新。" + notices.join(""));
    } else if (notices.length) {
      setStatus(byId("status").textContent + notices.join(""), true);
    }
    return previewed;
  } catch (error) {
    const message = error.message === "The fit range contains too few energy channels."
      ? "当前能量范围在此数据中不足 8 个通道；已保留输入值，请调整能量范围后更新信号图。"
      : error.message;
    setStatus(message, true);
    return false;
  }
}

async function analyzeRegions(regions) {
  try {
    setBusy(true);
    const pcaMode = isPcaMode();
    const common = settingsPayload();
    for (const region of regions) {
      const payload = {
        ...common,
        region,
        roi: state.rois[region],
      };
      const result = await requestApi(pcaMode ? "/api/pca" : "/api/fit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (pcaMode) {
        state.pcaResults[region] = result;
      } else {
        state.fits[region] = result;
      }
    }
    renderResults();
    setStatus(
      "已完成 " + regionListLabel(regions) +
      (pcaMode ? " 区域的 PCA 峰分析。" : " 区域的拟合。")
    );
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function pollBootstrap(jobId) {
  const job = await requestApi("/api/jobs/" + encodeURIComponent(jobId));
  updateProgress(job.completed, job.total, job.completed + " / " + job.total +
    (job.workers ? " · " + job.workers + (job.backend === "thread" ? " 线程" : " 进程") : ""));
  if (job.status === "queued" || job.status === "running") {
    window.setTimeout(() => {
      pollBootstrap(jobId).catch((error) => {
        setBusy(false);
        setStatus(error.message, true);
      });
    }, 350);
    return;
  }
  if (job.status === "error") {
    setBusy(false);
    setStatus(job.error || "不确定度计算失败。", true);
    return;
  }
  const pcaMode = job.analysis_type === "pca";
  const results = await requestApi(pcaMode ? "/api/pca/results" : "/api/results");
  const collection = pcaMode ? state.pcaResults : state.fits;
  for (const region of job.regions) {
    if (results[region]) collection[region] = results[region];
  }
  renderResults();
  updateProgress(job.total, job.total, "完成");
  setBusy(false);
  setStatus("已完成 " + regionListLabel(job.regions) + " 区域的不确定度计算。");
}

async function bootstrapRegions(regions) {
  try {
    setBusy(true);
    const pcaMode = isPcaMode();
    const common = settingsPayload();
    const response = await requestApi(pcaMode ? "/api/pca/bootstrap" : "/api/bootstrap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...common,
        regions,
        rois: state.rois,
        samples: byId("samples").value,
        block_size: byId("block-size").value,
        workers: byId("bootstrap-workers").value,
      }),
    });
    updateProgress(response.completed, response.total, "排队中");
    setStatus("已开始 " + regionListLabel(regions) + " 区域的不确定度计算" +
      (response.workers ? "（" + response.workers + " 个 CPU " +
        (response.backend === "thread" ? "线程" : "进程") + "）" : "") + "。");
    await pollBootstrap(response.job_id);
  } catch (error) {
    setBusy(false);
    setStatus(error.message, true);
  }
}

function roiCanvasRect(roi) {
  const lower = mapPoint(roi.x_min, roi.y_min);
  const upper = mapPoint(roi.x_max, roi.y_max);
  return { left: lower.x, right: upper.x, top: upper.y, bottom: lower.y };
}

function mapHitTest(event) {
  if (!state.mapLayout || !state.rois[state.activeRegion]) return null;
  const bounds = byId("map-canvas").getBoundingClientRect();
  const x = event.clientX - bounds.left;
  const y = event.clientY - bounds.top;
  const rect = roiCanvasRect(state.rois[state.activeRegion]);
  const radius = event.pointerType === "touch" ? 14 : 7;
  const handles = EelsRoi.handles(rect).map((handle) => ({
    ...handle, distance: Math.hypot(x - handle.x, y - handle.y),
  })).sort((a, b) => a.distance - b.distance);
  if (handles[0].distance <= radius) return handles[0].mode;
  if (x >= rect.left && x <= rect.right) {
    if (Math.abs(y - rect.top) <= radius) return "n";
    if (Math.abs(y - rect.bottom) <= radius) return "s";
  }
  if (y >= rect.top && y <= rect.bottom) {
    if (Math.abs(x - rect.left) <= radius) return "w";
    if (Math.abs(x - rect.right) <= radius) return "e";
  }
  if (x > rect.left && x < rect.right && y > rect.top && y < rect.bottom) return "move";
  return clientToMap(event.clientX, event.clientY) ? "draw" : null;
}

function mapCursor(mode, dragging = false) {
  if (state.busy) return "wait";
  if (mode === "move") return dragging ? "grabbing" : "grab";
  if (!mode) return "default";
  return mode === "draw" ? "crosshair" : mode + "-resize";
}

function finishMapDrag(cancel = false) {
  const drag = state.drag;
  if (!drag) return;
  state.drag = null;
  const canvas = byId("map-canvas");
  if (canvas.hasPointerCapture(drag.pointerId)) canvas.releasePointerCapture(drag.pointerId);
  canvas.style.cursor = "crosshair";
  const changed = roiSignature(state.rois[drag.region]) !== roiSignature(drag.original);
  if (cancel || !changed) {
    state.rois[drag.region] = drag.original;
    if (drag.changed) {
      state.previewRequestId += 1;
      for (const name of ["fits", "pcaResults", "previews"]) {
        if (drag.saved[name]) state[name][drag.region] = drag.saved[name];
        else delete state[name][drag.region];
      }
    }
  }
  drawMap();
  renderResults();
  if (!cancel && changed) {
    EelsRegionFit.manualChanged();
    setStatus(regionLabel(drag.region) + " 区域已更新，正在读取原始谱预览。");
    previewActiveRegion();
  } else if (drag.changed) {
    setStatus(cancel ? "已取消本次选区调整。" : "选区未改变，保留原有分析结果。");
  }
}

function registerMapInteraction() {
  const canvas = byId("map-canvas");
  canvas.addEventListener("pointerdown", (event) => {
    if (state.busy || state.drag || event.button !== 0) return;
    const mode = mapHitTest(event);
    if (!mode) return;
    event.preventDefault();
    canvas.focus({ preventScroll: true });
    const region = state.activeRegion;
    state.drag = {
      mode, region, pointerId: event.pointerId,
      start: clientToMap(event.clientX, event.clientY, true),
      clientStart: { x: event.clientX, y: event.clientY },
      original: { ...state.rois[region] },
      saved: { fits: state.fits[region], pcaResults: state.pcaResults[region], previews: state.previews[region] },
      changed: false,
    };
    canvas.setPointerCapture(event.pointerId);
    canvas.style.cursor = mapCursor(mode, true);
  });
  const move = (event) => {
    const drag = state.drag;
    if (!drag) {
      const mode = mapHitTest(event);
      canvas.style.cursor = mapCursor(mode);
      canvas.title = mode === "move" ? "拖动选框" :
        mode && mode !== "draw" ? "拖动边界调整大小" : "拖动重新选区";
      return;
    }
    if (event.pointerId !== drag.pointerId) return;
    if (!drag.changed && Math.hypot(
      event.clientX - drag.clientStart.x, event.clientY - drag.clientStart.y
    ) < 3) return;
    const point = clientToMap(event.clientX, event.clientY, true);
    const roi = EelsRoi.adjust(drag.original, drag.start, point, drag.mode, {
      x: state.map.x_nm, y: state.map.y_nm,
    });
    if (roiSignature(roi) === roiSignature(state.rois[drag.region])) return;
    if (!drag.changed) {
      invalidateRegionAnalysis(drag.region);
      drag.changed = true;
      renderResults();
    }
    state.rois[drag.region] = roi;
    drawMap();
  };
  canvas.addEventListener("pointermove", move);
  canvas.addEventListener("pointerup", (event) => {
    if (event.pointerId !== state.drag?.pointerId) return;
    move(event);
    finishMapDrag();
  });
  canvas.addEventListener("pointercancel", (event) => {
    if (event.pointerId === state.drag?.pointerId) finishMapDrag(true);
  });
  canvas.addEventListener("lostpointercapture", (event) => {
    if (event.pointerId === state.drag?.pointerId) finishMapDrag(true);
  });
  canvas.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.drag) {
      event.preventDefault();
      finishMapDrag(true);
    }
  });
}

function bindControls() {
  EelsRegionFit.bind();
  byId("empty-import-data").addEventListener("click", () => byId("import-data").click());
  byId("empty-import-workflow").addEventListener("click", () => byId("import-workflow").click());
  byId("map-kind").addEventListener("change", switchMapKind);
  byId("show-centers").addEventListener("change", drawMap);
  byId("import-data").addEventListener("click", () => {
    byId("import-message").textContent = "";
    syncZestImportOptions();
    byId("import-dialog").showModal();
  });
  byId("import-cancel").addEventListener("click", () => byId("import-dialog").close());
  byId("import-dialog").addEventListener("cancel", (event) => {
    if (state.busy) event.preventDefault();
  });
  byId("import-file").addEventListener("change", () => { byId("import-path").value = ""; syncZestImportOptions(); });
  byId("import-path").addEventListener("input", () => { byId("import-file").value = ""; syncZestImportOptions(); });
  byId("import-form").addEventListener("submit", importData);
  document.querySelectorAll("input[name='region']").forEach((radio) => {
    radio.addEventListener("change", (event) => {
      finishMapDrag(true);
      state.activeRegion = event.target.value;
      drawMap();
      renderResults();
      setStatus("当前区域：" + regionLabel(state.activeRegion) + "。");
      previewActiveRegion({ silent: true });
    });
  });
  byId("toggle-raw").addEventListener("click", () => toggleCurve("raw"));
  byId("toggle-pca").addEventListener("click", () => toggleCurve("pca"));
  byId("toggle-fit").addEventListener("click", () => toggleCurve("fit"));
  byId("toggle-baseline").addEventListener("click", () => toggleCurve("baseline"));
  byId("fit-mode").addEventListener("change", () => {
    syncFitMode();
    if (byId("fit-mode").value !== "multivariate") setStatus(isPcaMode() ? "当前使用纯 PCA 峰分析。" : "当前使用高斯分峰。" );
  });
  byId("update-map").addEventListener("click", updateMap);
  byId("fit-active").addEventListener("click", () => analyzeRegions([state.activeRegion]));
  byId("fit-all").addEventListener("click", () => analyzeRegions(["Si", "Interface", "Ge"]));
  byId("bootstrap-active").addEventListener("click", () => bootstrapRegions([state.activeRegion]));
  byId("bootstrap-all").addEventListener("click", () => bootstrapRegions(["Si", "Interface", "Ge"]));
  byId("download-results").addEventListener("click", exportResults);
  window.addEventListener("resize", () => {
    window.requestAnimationFrame(() => {
      drawMap();
      drawSpectrum();
    });
  });
  registerMapInteraction();
}

function renderDatasetOverview() {
  const loaded = hasDataset();
  const format = (value) => Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  const info = state.info;
  byId("metric-pixels").textContent = loaded ? format(info.shape.y * info.shape.x) : "—";
  byId("metric-spatial-shape").textContent = loaded ? info.shape.y + " × " + info.shape.x + " · y × x" : "导入光谱图像以开始";
  byId("metric-channels").textContent = loaded ? format(info.shape.energy) : "—";
  byId("metric-energy").textContent = loaded ? info.energy_meV.map(format).join(" – ") : "—";
  byId("metric-extent").textContent = loaded ? format(info.x_nm[1] - info.x_nm[0]) + " × " + format(info.y_nm[1] - info.y_nm[0]) : "—";
}

async function loadDatasetInfo({ preserveSettings = false } = {}) {
    state.info = await requestApi("/api/info");
    byId("app-version").textContent = state.info.app_version ? "v" + state.info.app_version : "";
    renderDatasetOverview();
    EelsWorkflow.resetDataset(state.info, preserveSettings);
    if (!hasDataset()) {
      state.rois = {}; state.map = state.signalMap = state.references = null;
      invalidateAllAnalyses();
      byId("dataset-name").textContent = "尚未导入数据";
      byId("dataset-shape").textContent = "";
      byId("dataset-import-note").hidden = true;
      syncFitMode();setBusy(false);
      return;
    }
    byId("empty-workspace").hidden = true;
    document.querySelector(".method-switch").hidden = false;
    document.querySelector(".preferences-bar").hidden = false;
    byId("fit-mode").querySelector('[value="multivariate"]').disabled = !state.info.workflow_version;
    EelsRegionFit.resetForDataset();
    const parallel = state.info.bootstrap_engine === "cpu_parallel_v1";
    byId("bootstrap-workers").closest("label").hidden = !parallel;
    Array.from(byId("bootstrap-workers").options).forEach((option) => {
      option.disabled = Number(option.value) > (state.info.max_workers || 1);
    });
    invalidateAllAnalyses();
    state.map = null;
    state.signalMap = null;
    state.mapKind = "signal";
    state.references = null;
    state.referenceRequest = null;
    state.displayRequestId += 1;
    byId("map-kind").value = "signal";
    renderCandidateReadout();
    if (!preserveSettings) state.rois = structuredClone(state.info.default_rois);
    drawMap();
    renderResults();
    updateProgress(0, 1, "空闲");
    byId("dataset-name").textContent = state.info.source.split(/[\\/]/).pop();
    byId("dataset-shape").textContent =
      state.info.shape.y + " × " + state.info.shape.x + " × " + state.info.shape.energy + "（y × x × 能量通道）";
    const preparation = state.info.input_preparation;
    byId("dataset-import-note").hidden = !preparation;
    byId("dataset-import-note").textContent = preparation
      ? "ZEST · " + preparation.signal_label + " · " + (preparation.options.energy_axis === "raw" ? "原能量轴" : "校正能量轴 / 线性插值") +
        " · 有效像素 " + preparation.valid_pixels + "，缺失 " + preparation.missing_pixels +
        " · " + (preparation.total_counts_status === "available" ? "已保留原始 EELS 总计数" : "未提供原始 EELS 总计数") : "";
    // Dataset-derived defaults apply only on first load, never on file import.
    if (preserveSettings) return;
    if (state.info.zlp_fwhm_meV != null && Number.isFinite(Number(state.info.zlp_fwhm_meV))) {
      const lowerFwhm = Math.max(0.5, Number(state.info.zlp_fwhm_meV) / 2).toFixed(2);
      byId("auto-fwhm-lower").value = lowerFwhm;
      byId("fwhm-lower").value = lowerFwhm;
    }
    const [lower, upper] = state.info.energy_meV;
    byId("fit-min").value = Math.max(lower, 0);
    byId("fit-max").value = Math.min(upper, 120);
    const step = (upper - lower) / (state.info.shape.energy - 1);
    if (Number(byId("fit-max").value) - Number(byId("fit-min").value) < step * 8) {
      byId("fit-min").value = lower;
      byId("fit-max").value = upper;
    }
}

function syncZestImportOptions() {
  const name = byId("import-file").files[0]?.name || byId("import-path").value.trim().replace(/^"|"$/g, "");
  const enabled = /\.zip$/i.test(name);
  byId("zest-import-options").hidden = !enabled;
  byId("zest-import-options").disabled = !enabled;
  return enabled;
}

async function importData(event) {
  event.preventDefault();
  const firstImport = !hasDataset();
  const file = byId("import-file").files[0];
  const path = byId("import-path").value.trim();
  if (!file && !path) {
    byId("import-message").textContent = "请选择文件或填写本地绝对路径。";
    return;
  }
  const isZest = syncZestImportOptions();
  const zestOptions = isZest ? {signal: byId("zest-signal").value, energy_axis: byId("zest-energy-axis").value} : null;
  setBusy(true);
  state.previewRequestId++;
  byId("import-form").querySelectorAll("button, input, select").forEach((control) => { control.disabled = true; });
  try {
    let options;
    if (file) {
      const form = new FormData();
      form.append("file", file);
      if (isZest) form.append("zest_options", JSON.stringify(zestOptions));
      options = { method: "POST", body: form };
    } else {
      options = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path, ...(isZest ? {zest_options: zestOptions} : {}) }) };
    }
    const message = isZest ? "正在读取 EELS-ZEST ZIP 并恢复数据立方体……" : /\.mat$/i.test(file?.name || path.replace(/"/g, ""))
      ? "正在后台启动 MATLAB 并转换 EELS 数据，请稍候……" : "正在读取并检查 HDF5 数据……";
    byId("import-message").textContent = message;
    setStatus(message);
    const started = await requestApi("/api/import", options);
    while (true) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      const job = await requestApi("/api/jobs/" + encodeURIComponent(started.job_id));
      if (isZest && job.status === "running" && job.total > 1) {
        byId("import-message").textContent = "正在读取 ZEST 像素谱线：" + job.completed + " / " + job.total;
      }
      if (job.status === "error") throw new Error(job.error || "导入失败。 ");
      if (job.status === "done") break;
    }
    await loadDatasetInfo({ preserveSettings: !firstImport });
    if (firstImport) await EelsPreferences.restore();
    syncFitMode();
    const previewed = await updateMap();
    await prepareReferenceDisplay();
    renderResults();
    byId("import-dialog").close();
    if (firstImport) EelsPreferences.bind();
    EelsPreferences.schedule();
    setStatus((firstImport ? "数据已导入，可以开始分析。" : "数据导入完成，已沿用上一次的设置；旧分析结果已清空。") +
      byId("status").textContent, !previewed);
  } catch (error) {
    byId("import-message").textContent = error.message;
    setStatus(error.message, true);
  } finally {
    byId("import-form").querySelectorAll("button, input, select").forEach((control) => { control.disabled = false; });
    syncZestImportOptions();
    setBusy(false);
  }
}

async function exportResults() {
  const results = [];
  for (const [analysis_type, collection] of [["pca", state.pcaResults], ["gaussian", state.fits]]) {
    for (const [region, result] of Object.entries(collection)) {
      results.push({ analysis_type, region, result_id: result.result_id });
    }
  }
  if (!results.length) {
    setStatus("尚无可导出的结果，请先运行区域分析。", true);
    return;
  }
  setBusy(true);
  setStatus("正在导出结果、实际运行参数和数据校验信息……");
  try {
    const response = await fetch("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-EELS-Dataset": state.info.dataset_id },
      body: JSON.stringify({ results }),
    });
    if (!response.ok) throw new Error((await response.json()).error || "导出失败。");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    // Use the current dataset name, including when an older server is still running.
    const sourceName = (state.info.source || "").split(/[\\/]/).pop();
    link.download = sourceName ? sourceName.replace(/\.[^.]+$/, "") + ".json" : "eels_results.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    setStatus("已导出 " + results.length + " 组区域结果及复现参数（JSON）；未计算的不确定度为 null，请保留输入数据。");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function initialize() {
  try {
    await EelsWorkflow.mount();
    bindControls();
    await loadDatasetInfo();
    if (!hasDataset()) {
      await EelsWorkflow.open();
      return;
    }
    await EelsPreferences.restore();
    syncFitMode();
    await updateMap();
    await prepareReferenceDisplay();
    if (byId("map-kind").value !== state.mapKind) await switchMapKind();
    renderResults();
    EelsPreferences.bind();
  } catch (error) {
    setStatus(error.message, true);
  }
}

document.addEventListener("DOMContentLoaded", initialize);
