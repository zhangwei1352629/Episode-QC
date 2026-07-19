const state = {
  activeTool: "scan",
  mcapPath: null,
  folderPath: null,
  mode: "file",
  topics: [],
  selectedCandidateIndex: -1,
  lastResult: null,
  annotations: {},
  annotation: {
    rootPath: null,
    index: null,
    selectedFileIndex: -1,
    selectedTopic: null,
    frameIndex: 0,
    currentFrame: null,
    labels: {},
    loadToken: 0
  }
};

const DEFAULT_IMAGE_TOPIC = "/camera/ego_head/image/jpeg";

const els = {
  toolScan: document.querySelector("#tool-scan"),
  toolLabel: document.querySelector("#tool-label"),
  selectFile: document.querySelector("#select-file"),
  selectFolder: document.querySelector("#select-folder"),
  selectedPath: document.querySelector("#selected-path"),
  refreshTopics: document.querySelector("#refresh-topics"),
  topicList: document.querySelector("#topic-list"),
  scanControls: document.querySelector("#scan-controls"),
  annotationControls: document.querySelector("#annotation-controls"),
  threshold: document.querySelector("#threshold"),
  thresholdValue: document.querySelector("#threshold-value"),
  minChange: document.querySelector("#min-change"),
  maxStaleDelta: document.querySelector("#max-stale-delta"),
  historySize: document.querySelector("#history-size"),
  maxPersistenceFrames: document.querySelector("#max-persistence-frames"),
  jobs: document.querySelector("#jobs"),
  limit: document.querySelector("#limit"),
  resize: document.querySelector("#resize"),
  scan: document.querySelector("#scan"),
  saveReport: document.querySelector("#save-report"),
  saveAnnotations: document.querySelector("#save-annotations"),
  workspaceTitle: document.querySelector("#workspace-title"),
  runSummary: document.querySelector("#run-summary"),
  statusPill: document.querySelector("#status-pill"),
  scanMetrics: document.querySelector("#scan-metrics"),
  scanResults: document.querySelector("#scan-results"),
  metricFrames: document.querySelector("#metric-frames"),
  metricCandidates: document.querySelector("#metric-candidates"),
  metricErrors: document.querySelector("#metric-errors"),
  metricTopics: document.querySelector("#metric-topics"),
  candidateList: document.querySelector("#candidate-list"),
  previewImage: document.querySelector("#preview-image"),
  previewWrap: document.querySelector(".preview-image-wrap"),
  reviewActions: document.querySelector("#review-actions"),
  candidateDetails: document.querySelector("#candidate-details"),
  annotationLayout: document.querySelector("#annotation-layout"),
  annotationFileList: document.querySelector("#annotation-file-list"),
  annotationFrame: document.querySelector("#annotation-frame"),
  annotationFrameCount: document.querySelector("#annotation-frame-count"),
  annotationPrev: document.querySelector("#annotation-prev"),
  annotationNext: document.querySelector("#annotation-next"),
  annotationCurrentTitle: document.querySelector("#annotation-current-title"),
  annotationCurrentSubtitle: document.querySelector("#annotation-current-subtitle"),
  annotationCounter: document.querySelector("#annotation-counter"),
  annotationImage: document.querySelector("#annotation-image"),
  annotationImageWrap: document.querySelector(".annotation-image-wrap"),
  annotationActions: document.querySelector("#annotation-actions"),
  annotationNote: document.querySelector("#annotation-note"),
  annotationDetails: document.querySelector("#annotation-details")
};

els.toolScan.addEventListener("click", () => {
  setActiveTool("scan");
});

els.toolLabel.addEventListener("click", () => {
  setActiveTool("label");
});

els.selectFile.addEventListener("click", async () => {
  const filePath = await window.episodeQc.selectMcap();
  if (!filePath) {
    return;
  }
  if (state.activeTool === "label") {
    await loadAnnotationRoot(filePath);
    return;
  }
  state.mcapPath = filePath;
  state.folderPath = null;
  state.mode = "file";
  els.selectedPath.textContent = filePath;
  await loadTopics();
});

els.selectFolder.addEventListener("click", async () => {
  const folderPath = await window.episodeQc.selectFolder();
  if (!folderPath) {
    return;
  }
  if (state.activeTool === "label") {
    await loadAnnotationRoot(folderPath);
    return;
  }
  state.mcapPath = null;
  state.folderPath = folderPath;
  state.mode = "folder";
  els.selectedPath.textContent = folderPath;
  renderFolderTopics();
  resetMetrics();
  setStatus("Idle", "");
  els.runSummary.textContent = "Folder ready for recursive MCAP scan";
});

els.refreshTopics.addEventListener("click", async () => {
  if (state.activeTool === "label" && state.annotation.rootPath) {
    await loadAnnotationRoot(state.annotation.rootPath, { preserveLabels: true });
    return;
  }
  if (state.mode === "file" && state.mcapPath) {
    await loadTopics();
  }
});

els.threshold.addEventListener("input", () => {
  els.thresholdValue.textContent = Number(els.threshold.value).toFixed(2);
});

els.saveReport.addEventListener("click", async () => {
  if (!state.lastResult) {
    return;
  }

  try {
    const filePath = await window.episodeQc.saveReport(buildReport());
    if (filePath) {
      setStatus("Saved", "");
      els.runSummary.textContent = `Report saved to ${filePath}`;
    }
  } catch (error) {
    setStatus("Error", "error");
    els.runSummary.textContent = error.message || String(error);
  }
});

els.saveAnnotations.addEventListener("click", async () => {
  try {
    const filePath = await window.episodeQc.saveAnnotations(buildAnnotationsReport());
    if (filePath) {
      setStatus("Saved", "");
      els.runSummary.textContent = `Labels saved to ${filePath}`;
    }
  } catch (error) {
    setStatus("Error", "error");
    els.runSummary.textContent = error.message || String(error);
  }
});

els.reviewActions.addEventListener("click", (event) => {
  const button = event.target.closest("[data-review]");
  if (button) {
    markSelectedCandidate(button.dataset.review);
  }
});

els.annotationPrev.addEventListener("click", () => {
  goToAnnotationFrame(state.annotation.frameIndex - 1);
});

els.annotationNext.addEventListener("click", () => {
  goToAnnotationFrame(state.annotation.frameIndex + 1);
});

els.annotationFrame.addEventListener("change", () => {
  goToAnnotationFrame(Number(els.annotationFrame.value));
});

els.annotationActions.addEventListener("click", (event) => {
  const button = event.target.closest("[data-frame-label]");
  if (button) {
    markAnnotationFrame(button.dataset.frameLabel);
  }
});

els.annotationNote.addEventListener("input", () => {
  syncAnnotationNote();
});

els.scan.addEventListener("click", async () => {
  if (!currentInputPath()) {
    return;
  }

  const topics = selectedTopics();
  if (topics.length === 0) {
    setStatus("Select at least one topic", "error");
    return;
  }

  setStatus("Scanning", "running");
  els.scan.disabled = true;
  els.saveReport.disabled = true;
  els.runSummary.textContent = "Running Python detector through uv";
  clearCandidates("Scanning...");

  try {
    const result = await window.episodeQc.scanStaleRegions({
      mode: state.mode,
      mcapPath: state.mcapPath,
      folderPath: state.folderPath,
      topics,
      detector: "camera-tearing",
      threshold: Number(els.threshold.value),
      limit: parseLimit(els.limit.value),
      historySize: parseLimit(els.historySize.value) || 3,
      maxPersistenceFrames: parseLimit(els.maxPersistenceFrames.value) || 12,
      jobs: parseLimit(els.jobs.value) || 4,
      minChange: parseOptionalFloat(els.minChange.value) || 0.08,
      maxStaleDelta: parseOptionalFloat(els.maxStaleDelta.value) || 0.035,
      resize: els.resize.value
    });
    state.lastResult = result;
    state.selectedCandidateIndex = -1;
    state.annotations = {};
    renderResult(result);
    setStatus("Complete", "");
  } catch (error) {
    setStatus("Error", "error");
    els.runSummary.textContent = error.message || String(error);
    clearCandidates("Scan failed");
  } finally {
    updateScanEnabled();
  }
});

function setActiveTool(tool) {
  state.activeTool = tool;
  const isLabel = tool === "label";

  els.toolScan.classList.toggle("is-active", !isLabel);
  els.toolLabel.classList.toggle("is-active", isLabel);
  els.scanControls.hidden = isLabel;
  els.annotationControls.hidden = !isLabel;
  els.scanMetrics.hidden = isLabel;
  els.scanResults.hidden = isLabel;
  els.annotationLayout.hidden = !isLabel;
  els.saveReport.hidden = isLabel;
  els.saveAnnotations.hidden = !isLabel;
  els.workspaceTitle.textContent = isLabel ? "Frame Annotation" : "Stale Region Events";

  if (isLabel) {
    els.selectedPath.textContent = state.annotation.rootPath || "No folder selected";
    renderAnnotationTopics();
    renderAnnotationFiles();
    renderAnnotationFrameState();
    setStatus("Idle", "");
    if (!state.annotation.index) {
      els.runSummary.textContent = "Select a folder to index MCAP files for frame labeling";
    }
  } else {
    els.selectedPath.textContent = currentInputPath() || "No file selected";
    if (state.mode === "folder" && state.folderPath) {
      renderFolderTopics();
    } else if (state.topics.length > 0) {
      renderTopics();
    } else {
      els.topicList.innerHTML = '<div class="empty-state">Select an MCAP file</div>';
    }
    setStatus("Idle", "");
    updateScanEnabled();
  }
}

async function loadAnnotationRoot(rootPath, { preserveLabels = false } = {}) {
  setStatus("Indexing", "running");
  els.selectedPath.textContent = rootPath;
  els.runSummary.textContent = "Reading MCAP summaries for annotation";
  els.annotationFileList.innerHTML = '<div class="empty-state large">Indexing folder...</div>';
  clearAnnotationFrame("No frame loaded");

  try {
    const index = await window.episodeQc.indexAnnotationFolder(rootPath);
    const previousLabels = preserveLabels ? state.annotation.labels : {};
    state.annotation = {
      rootPath,
      index,
      selectedFileIndex: -1,
      selectedTopic: null,
      frameIndex: 0,
      currentFrame: null,
      labels: previousLabels,
      loadToken: state.annotation.loadToken + 1
    };
    renderAnnotationFiles();
    selectFirstAnnotationFile();
    setStatus("Idle", "");
    const summary = index.summary || {};
    els.runSummary.textContent = `${formatNumber(summary.scanned_files || 0)} files, ${formatNumber(
      summary.topics || 0
    )} topics, ${formatNumber(summary.frames || 0)} frames indexed`;
  } catch (error) {
    state.annotation.index = null;
    setStatus("Error", "error");
    els.runSummary.textContent = error.message || String(error);
    renderAnnotationFiles();
    renderAnnotationTopics();
    clearAnnotationFrame("Index failed");
  }
}

function selectFirstAnnotationFile() {
  const files = annotationFiles();
  const index = files.findIndex((file) => file.ok && Array.isArray(file.topics) && file.topics.length > 0);
  if (index >= 0) {
    selectAnnotationFile(index);
  } else {
    renderAnnotationTopics();
    clearAnnotationFrame("No image topics found");
  }
}

function renderAnnotationFiles() {
  const files = annotationFiles();
  if (!state.annotation.index) {
    els.annotationFileList.innerHTML = '<div class="empty-state large">Select a folder to index MCAP files</div>';
    updateAnnotationCounter();
    return;
  }
  if (files.length === 0) {
    els.annotationFileList.innerHTML = '<div class="empty-state large">No MCAP files found</div>';
    updateAnnotationCounter();
    return;
  }

  els.annotationFileList.innerHTML = "";
  files.forEach((file, index) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "annotation-file-row";
    row.classList.toggle("is-selected", index === state.annotation.selectedFileIndex);
    const topics = Array.isArray(file.topics) ? file.topics : [];
    const frameCount = topics.reduce((total, topic) => total + Number(topic.message_count || 0), 0);
    row.innerHTML = `
      <span title="${escapeAttribute(file.path || "")}">${escapeHtml(file.episode || file.path || "MCAP")}</span>
      <span class="annotation-number">${formatNumber(topics.length)}</span>
      <span class="annotation-number">${formatNumber(frameCount)}</span>
      <span class="annotation-number">${formatNumber(annotationCountForFile(file.path))}</span>
    `;
    row.addEventListener("click", () => selectAnnotationFile(index));
    els.annotationFileList.append(row);
  });
  updateAnnotationCounter();
}

function selectAnnotationFile(index) {
  const file = annotationFiles()[index];
  if (!file || !file.ok) {
    return;
  }
  const topics = Array.isArray(file.topics) ? file.topics.filter((topic) => topic.message_count > 0) : [];
  state.annotation.selectedFileIndex = index;
  state.annotation.selectedTopic = preferredAnnotationTopic(topics);
  state.annotation.frameIndex = 0;
  state.annotation.currentFrame = null;
  renderAnnotationFiles();
  renderAnnotationTopics();
  loadAnnotationFrame();
}

function preferredAnnotationTopic(topics) {
  if (topics.some((topic) => topic.name === state.annotation.selectedTopic)) {
    return state.annotation.selectedTopic;
  }
  const defaultTopic = topics.find((topic) => topic.name === DEFAULT_IMAGE_TOPIC);
  return (defaultTopic || topics[0] || {}).name || null;
}

function renderAnnotationTopics() {
  if (state.activeTool !== "label") {
    return;
  }
  const file = selectedAnnotationFile();
  const topics = file && Array.isArray(file.topics) ? file.topics.filter((topic) => topic.message_count > 0) : [];
  if (topics.length === 0) {
    els.topicList.innerHTML = '<div class="empty-state">No indexed topics</div>';
    return;
  }

  els.topicList.innerHTML = "";
  for (const topic of topics) {
    const label = document.createElement("label");
    label.className = "topic-item";
    const checked = topic.name === state.annotation.selectedTopic ? "checked" : "";
    label.innerHTML = `
      <input type="radio" name="annotation-topic" value="${escapeAttribute(topic.name)}" ${checked} />
      <span class="topic-name" title="${escapeAttribute(topic.name)}">${escapeHtml(topic.name)}</span>
      <span class="topic-count">${formatNumber(topic.message_count)}</span>
    `;
    label.querySelector("input").addEventListener("change", () => {
      state.annotation.selectedTopic = topic.name;
      state.annotation.frameIndex = 0;
      state.annotation.currentFrame = null;
      loadAnnotationFrame();
    });
    els.topicList.append(label);
  }
  updateAnnotationNav();
}

function goToAnnotationFrame(frameIndex) {
  const topic = currentAnnotationTopic();
  if (!topic) {
    return;
  }
  const maxIndex = Math.max(0, Number(topic.message_count || 1) - 1);
  const nextIndex = Math.min(Math.max(0, Math.trunc(frameIndex || 0)), maxIndex);
  if (nextIndex === state.annotation.frameIndex && state.annotation.currentFrame) {
    els.annotationFrame.value = String(nextIndex);
    return;
  }
  state.annotation.frameIndex = nextIndex;
  loadAnnotationFrame();
}

async function loadAnnotationFrame() {
  const file = selectedAnnotationFile();
  const topic = currentAnnotationTopic();
  if (!file || !topic) {
    clearAnnotationFrame("No frame loaded");
    return;
  }

  const maxIndex = Math.max(0, Number(topic.message_count || 1) - 1);
  state.annotation.frameIndex = Math.min(Math.max(0, state.annotation.frameIndex), maxIndex);
  const token = state.annotation.loadToken + 1;
  state.annotation.loadToken = token;
  clearAnnotationFrame("Loading frame...");
  setStatus("Loading", "running");
  updateAnnotationNav();

  try {
    const frame = await window.episodeQc.exportAnnotationFrame({
      mcapPath: file.path,
      topic: topic.name,
      frameIndex: state.annotation.frameIndex
    });
    if (token !== state.annotation.loadToken) {
      return;
    }
    state.annotation.currentFrame = frame;
    renderAnnotationFrameState();
    setStatus("Idle", "");
  } catch (error) {
    if (token !== state.annotation.loadToken) {
      return;
    }
    state.annotation.currentFrame = null;
    setStatus("Error", "error");
    els.runSummary.textContent = error.message || String(error);
    clearAnnotationFrame("Frame load failed");
  }
}

function renderAnnotationFrameState() {
  const file = selectedAnnotationFile();
  const topic = currentAnnotationTopic();
  const frame = state.annotation.currentFrame;
  updateAnnotationNav();
  updateAnnotationCounter();

  if (!file || !topic || !frame) {
    return;
  }

  els.annotationCurrentTitle.textContent = `${file.episode || "MCAP"} frame ${frame.frame_index}`;
  els.annotationCurrentSubtitle.textContent = topic.name;
  els.annotationImage.src = frame.frame_url;
  els.annotationImageWrap.classList.add("has-image");

  const annotation = currentFrameAnnotation();
  els.annotationNote.value = annotation?.note || "";
  updateAnnotationButtons(annotation?.label || "unlabeled");
  renderAnnotationDetails(frame, annotation);
}

function clearAnnotationFrame(message) {
  state.annotation.currentFrame = null;
  els.annotationImage.removeAttribute("src");
  els.annotationImageWrap.classList.remove("has-image");
  els.annotationCurrentTitle.textContent = "No frame selected";
  els.annotationCurrentSubtitle.textContent = "Choose an indexed file and topic";
  els.annotationDetails.innerHTML = "";
  els.annotationNote.value = "";
  updateAnnotationButtons("unlabeled");
  updateAnnotationNav();
  const empty = document.querySelector("#annotation-empty");
  if (empty) {
    empty.textContent = message;
  }
}

function markAnnotationFrame(label) {
  const frame = state.annotation.currentFrame;
  const file = selectedAnnotationFile();
  if (!frame || !file) {
    return;
  }
  const key = annotationKey(file.path, frame.topic, frame.frame_index);
  if (label === "clear") {
    delete state.annotation.labels[key];
  } else {
    state.annotation.labels[key] = annotationRecord(label, els.annotationNote.value);
  }
  renderAnnotationFrameState();
  renderAnnotationFiles();
}

function syncAnnotationNote() {
  const frame = state.annotation.currentFrame;
  const file = selectedAnnotationFile();
  if (!frame || !file) {
    return;
  }
  const key = annotationKey(file.path, frame.topic, frame.frame_index);
  const note = els.annotationNote.value;
  const existing = state.annotation.labels[key];
  if (!existing && note.trim().length === 0) {
    return;
  }
  state.annotation.labels[key] = annotationRecord(existing?.label || "note", note);
  updateAnnotationCounter();
  renderAnnotationFiles();
}

function annotationRecord(label, note) {
  const frame = state.annotation.currentFrame;
  const file = selectedAnnotationFile();
  return {
    label,
    note: note.trim(),
    mcap_path: file.path,
    episode: file.episode || "",
    topic: frame.topic,
    frame_index: frame.frame_index,
    log_time_ns: frame.log_time_ns,
    publish_time_ns: frame.publish_time_ns,
    sequence: frame.sequence,
    timestamp_ns: frame.timestamp_ns,
    frame_id: frame.frame_id,
    updated_at: new Date().toISOString()
  };
}

function renderAnnotationDetails(frame, annotation) {
  const file = selectedAnnotationFile();
  const details = [
    ["Label", formatFrameLabelStatus(annotation?.label || "unlabeled")],
    ["Episode", file?.episode || ""],
    ["MCAP", file?.path || ""],
    ["Topic", frame.topic],
    ["Frame", frame.frame_index],
    ["Sequence", frame.sequence],
    ["Log Time", frame.log_time_ns],
    ["Timestamp", frame.timestamp_ns ?? ""],
    ["Frame ID", frame.frame_id || ""],
    ["Format", frame.format || ""],
    ["Preview", frame.output_path || ""],
    ["Updated", annotation?.updated_at || ""]
  ];

  els.annotationDetails.innerHTML = "";
  for (const [label, value] of details) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    els.annotationDetails.append(dt, dd);
  }
}

function updateAnnotationNav() {
  const topic = currentAnnotationTopic();
  const frameCount = Number(topic?.message_count || 0);
  const maxIndex = Math.max(0, frameCount - 1);
  els.annotationFrame.value = String(state.annotation.frameIndex || 0);
  els.annotationFrame.max = String(maxIndex);
  els.annotationFrame.disabled = frameCount === 0;
  els.annotationFrameCount.textContent = formatNumber(frameCount);
  els.annotationPrev.disabled = frameCount === 0 || state.annotation.frameIndex <= 0;
  els.annotationNext.disabled = frameCount === 0 || state.annotation.frameIndex >= maxIndex;
}

function updateAnnotationButtons(activeLabel) {
  els.annotationActions.querySelectorAll("[data-frame-label]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.frameLabel === activeLabel);
  });
}

function updateAnnotationCounter() {
  const annotations = Object.values(state.annotation.labels);
  els.annotationCounter.textContent = `${formatNumber(annotations.length)} labels`;
  els.saveAnnotations.disabled = annotations.length === 0;
}

function currentFrameAnnotation() {
  const frame = state.annotation.currentFrame;
  const file = selectedAnnotationFile();
  if (!frame || !file) {
    return null;
  }
  return state.annotation.labels[annotationKey(file.path, frame.topic, frame.frame_index)] || null;
}

function annotationFiles() {
  return state.annotation.index?.files || [];
}

function selectedAnnotationFile() {
  return annotationFiles()[state.annotation.selectedFileIndex] || null;
}

function currentAnnotationTopic() {
  const file = selectedAnnotationFile();
  const topics = file && Array.isArray(file.topics) ? file.topics : [];
  return topics.find((topic) => topic.name === state.annotation.selectedTopic) || null;
}

function annotationKey(mcapPath, topic, frameIndex) {
  return `${mcapPath}#${topic}#${frameIndex}`;
}

function annotationCountForFile(mcapPath) {
  return Object.values(state.annotation.labels).filter((item) => item.mcap_path === mcapPath).length;
}

function buildAnnotationsReport() {
  const annotations = Object.values(state.annotation.labels).sort((left, right) => {
    const leftKey = `${left.mcap_path}#${left.topic}#${String(left.frame_index).padStart(9, "0")}`;
    const rightKey = `${right.mcap_path}#${right.topic}#${String(right.frame_index).padStart(9, "0")}`;
    return leftKey.localeCompare(rightKey);
  });
  const summary = {
    labels: annotations.length,
    good: annotations.filter((item) => item.label === "good").length,
    defect: annotations.filter((item) => item.label === "defect").length,
    unsure: annotations.filter((item) => item.label === "unsure").length,
    note: annotations.filter((item) => item.label === "note").length
  };

  return {
    report_version: 1,
    type: "frame_annotations",
    generated_at: new Date().toISOString(),
    rootPath: state.annotation.rootPath,
    source_summary: state.annotation.index?.summary || {},
    summary,
    annotations
  };
}

async function loadTopics() {
  setStatus("Loading", "running");
  els.scan.disabled = true;
  els.topicList.innerHTML = '<div class="empty-state">Reading MCAP summary...</div>';

  try {
    const topics = await window.episodeQc.listTopics(state.mcapPath);
    state.topics = topics.filter((topic) => topic.messageCount > 0);
    renderTopics();
    resetMetrics();
    setStatus("Idle", "");
    els.runSummary.textContent = `${state.topics.length} image topics ready`;
  } catch (error) {
    state.topics = [];
    renderTopics(error.message || String(error));
    setStatus("Error", "error");
  }
}

function renderFolderTopics() {
  state.topics = [{ name: DEFAULT_IMAGE_TOPIC, messageCount: 0 }];
  els.topicList.innerHTML = "";
  const label = document.createElement("label");
  label.className = "topic-item";
  label.innerHTML = `
    <input type="checkbox" value="${escapeAttribute(DEFAULT_IMAGE_TOPIC)}" checked />
    <span class="topic-name" title="${escapeAttribute(DEFAULT_IMAGE_TOPIC)}">${escapeHtml(DEFAULT_IMAGE_TOPIC)}</span>
    <span class="topic-count">folder</span>
  `;
  label.querySelector("input").addEventListener("change", updateScanEnabled);
  els.topicList.append(label);
  updateScanEnabled();
}

function renderTopics(errorText) {
  if (errorText) {
    els.topicList.innerHTML = `<div class="empty-state">${escapeHtml(errorText)}</div>`;
    return;
  }

  if (state.topics.length === 0) {
    els.topicList.innerHTML = '<div class="empty-state">No JPEG image topics found</div>';
    els.scan.disabled = true;
    return;
  }

  els.topicList.innerHTML = "";
  for (const topic of state.topics) {
    const label = document.createElement("label");
    label.className = "topic-item";
    const checked = topic.name === DEFAULT_IMAGE_TOPIC ? "checked" : "";
    label.innerHTML = `
      <input type="checkbox" value="${escapeAttribute(topic.name)}" ${checked} />
      <span class="topic-name" title="${escapeAttribute(topic.name)}">${escapeHtml(topic.name)}</span>
      <span class="topic-count">${formatNumber(topic.messageCount)}</span>
    `;
    label.querySelector("input").addEventListener("change", updateScanEnabled);
    els.topicList.append(label);
  }
  updateScanEnabled();
}

function renderResult(result) {
  const summary = result.summary || {};
  const items = displayItems(result);
  const eventCount = summary.events ?? (Array.isArray(result.events) ? result.events.length : null);
  const metricCount = eventCount ?? summary.candidates ?? items.length;
  const itemLabel = eventCount === null ? "candidates" : "events";
  els.metricFrames.textContent = formatNumber(summary.decoded_frames || 0);
  els.metricCandidates.textContent = formatNumber(metricCount || 0);
  els.metricErrors.textContent = formatNumber(summary.decode_errors || 0);
  els.metricTopics.textContent = formatNumber(summary.topics || 0);
  const fileText = summary.files ? ` across ${formatNumber(summary.scanned_files || 0)} files` : "";
  const candidateText =
    eventCount === null || eventCount === summary.candidates
      ? ""
      : ` / ${formatNumber(summary.candidates || 0)} frame candidates`;
  els.runSummary.textContent = `${formatNumber(metricCount || 0)} ${itemLabel}${candidateText} from ${formatNumber(
    summary.decoded_frames || 0
  )} decoded frames${fileText}`;

  els.saveReport.disabled = false;
  if (items.length === 0) {
    clearCandidates("No candidates found");
    return;
  }

  els.candidateList.innerHTML = "";
  items.forEach((candidate, index) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "candidate-row";
    const topicLabel = candidate.episode ? `${candidate.episode}:${shortTopic(candidate.topic)}` : shortTopic(candidate.topic);
    const score = Number(candidate.event_max_score ?? candidate.score ?? 0);
    row.innerHTML = `
      <span title="${escapeAttribute(candidate.topic)}">${escapeHtml(topicLabel)}</span>
      <span>${escapeHtml(formatFrameLabel(candidate))}</span>
      <span class="score">${score.toFixed(3)}</span>
      <span>${escapeHtml(formatReviewStatus(candidateReview(candidate)))}</span>
    `;
    row.addEventListener("click", () => selectCandidate(index));
    els.candidateList.append(row);
  });

  selectCandidate(0);
}

function selectCandidate(index) {
  const candidates = displayItems(state.lastResult);
  const candidate = candidates[index];
  if (!candidate) {
    return;
  }

  state.selectedCandidateIndex = index;
  document.querySelectorAll(".candidate-row").forEach((row, rowIndex) => {
    row.classList.toggle("is-selected", rowIndex === index);
  });

  if (candidate.snapshot_url) {
    els.previewImage.src = candidate.snapshot_url;
    els.previewWrap.classList.add("has-image");
  } else {
    els.previewImage.removeAttribute("src");
    els.previewWrap.classList.remove("has-image");
  }

  updateReviewButtons(candidateReview(candidate));

  els.candidateDetails.innerHTML = "";
  const details = [
    ["Detector", candidate.detector || "stale_region"],
    ["Episode", candidate.episode || ""],
    ["MCAP", candidate.mcap_path || ""],
    ["Topic", candidate.topic],
    ["Frame", candidate.frame_index],
    ["Frame Range", formatFrameLabel(candidate)],
    ["Frame Count", candidate.event_frame_count || ""],
    ["Event Candidates", candidate.event_candidate_count || ""],
    ["Region", candidate.region_index],
    ["Score", Number(candidate.event_max_score ?? candidate.score ?? 0).toFixed(4)],
    ["Mean Score", candidate.event_mean_score ? Number(candidate.event_mean_score).toFixed(4) : ""],
    ["Reference Lag", candidate.reference_lag],
    ["BBox", formatBBox(candidate.bbox)],
    ["Area", `${(Number(candidate.area_ratio) * 100).toFixed(3)}%`],
    ["Rectangularity", Number(candidate.rectangularity).toFixed(3)],
    ["Stale Delta", Number(candidate.stale_delta).toFixed(5)],
    ["Localized Change", Number(candidate.localized_change || 0).toFixed(5)],
    ["Texture Increase", Number(candidate.texture_increase || 0).toFixed(5)],
    ["Motion Residual", Number(candidate.motion_residual || 0).toFixed(5)],
    ["Future Change", Number(candidate.future_change).toFixed(5)],
    ["Temporal Contrast", Number(candidate.temporal_contrast).toFixed(5)],
    ["Frame Gap Ratio", Number(candidate.frame_gap_ratio || 1).toFixed(3)],
    ["Sequence Gap", candidate.sequence_gap || 1],
    ["Event Start", candidate.event_start_frame ?? ""],
    ["Event Offset", candidate.event_frame_offset || 0],
    ["Review", formatReviewStatus(candidateReview(candidate))],
    ["Log Time", candidate.log_time_ns],
    ["Snapshot", candidate.snapshot_path || ""]
  ];

  for (const [label, value] of details) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    els.candidateDetails.append(dt, dd);
  }
}

function clearCandidates(message) {
  els.candidateList.innerHTML = `<div class="empty-state large">${escapeHtml(message)}</div>`;
  els.previewImage.removeAttribute("src");
  els.previewWrap.classList.remove("has-image");
  els.candidateDetails.innerHTML = "";
  updateReviewButtons("unreviewed");
}

function resetMetrics() {
  els.metricFrames.textContent = "0";
  els.metricCandidates.textContent = "0";
  els.metricErrors.textContent = "0";
  els.metricTopics.textContent = "0";
  els.saveReport.disabled = true;
  clearCandidates("No scan results yet");
}

function markSelectedCandidate(reviewStatus) {
  const candidate = displayItems(state.lastResult)[state.selectedCandidateIndex];
  if (!candidate) {
    return;
  }

  const key = candidateKey(candidate);
  if (reviewStatus === "unreviewed") {
    delete state.annotations[key];
  } else {
    state.annotations[key] = {
      status: reviewStatus,
      updated_at: new Date().toISOString()
    };
  }

  renderResult(state.lastResult);
  selectCandidate(state.selectedCandidateIndex);
}

function candidateReview(candidate) {
  return state.annotations[candidateKey(candidate)]?.status || "unreviewed";
}

function candidateKey(candidate) {
  if (candidate.event_id) {
    return candidate.event_id;
  }
  return `${candidate.mcap_path || ""}#${candidate.topic}#${candidate.frame_index}`;
}

function updateReviewButtons(activeStatus) {
  els.reviewActions.querySelectorAll("[data-review]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.review === activeStatus);
  });
}

function buildReport() {
  const result = JSON.parse(JSON.stringify(state.lastResult));
  const candidates = Array.isArray(result.events) && result.events.length > 0 ? result.events : result.candidates || [];
  const reviewSummary = {
    confirmed: 0,
    false_positive: 0,
    unreviewed: 0
  };

  for (const candidate of candidates) {
    const status = candidateReview(candidate);
    candidate.review_status = status;
    candidate.review = state.annotations[candidateKey(candidate)] || null;
    reviewSummary[status] += 1;
  }

  return {
    report_version: 1,
    generated_at: new Date().toISOString(),
    mode: state.mode,
    mcapPath: state.mcapPath || state.folderPath,
    review_summary: reviewSummary,
    scan: result
  };
}

function displayItems(result) {
  if (Array.isArray(result?.events) && result.events.length > 0) {
    return result.events;
  }
  return result?.candidates || [];
}

function updateScanEnabled() {
  if (state.activeTool !== "scan") {
    els.scan.disabled = true;
    return;
  }
  els.scan.disabled = !currentInputPath() || selectedTopics().length === 0;
}

function currentInputPath() {
  return state.mode === "folder" ? state.folderPath : state.mcapPath;
}

function selectedTopics() {
  return [...els.topicList.querySelectorAll("input[type='checkbox']:checked")].map((input) => input.value);
}

function parseLimit(value) {
  if (!value) {
    return null;
  }
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function parseOptionalFloat(value) {
  if (!value) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function setStatus(text, mode) {
  els.statusPill.textContent = text;
  els.statusPill.classList.toggle("is-running", mode === "running");
  els.statusPill.classList.toggle("is-error", mode === "error");
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(value);
}

function shortTopic(topic) {
  return topic.replace("/camera/", "");
}

function formatFrameLabel(candidate) {
  const start = candidate.event_frame_start ?? candidate.frame_index;
  const end = candidate.event_frame_end ?? candidate.frame_index;
  if (start === undefined || start === null) {
    return "";
  }
  return start === end ? String(start) : `${start}-${end}`;
}

function formatBBox(value) {
  if (!Array.isArray(value) || value.length !== 4) {
    return "";
  }
  return `x=${value[0]}, y=${value[1]}, w=${value[2]}, h=${value[3]}`;
}

function formatReviewStatus(value) {
  if (value === "confirmed") {
    return "Confirmed";
  }
  if (value === "false_positive") {
    return "False Positive";
  }
  return "Unreviewed";
}

function formatFrameLabelStatus(value) {
  if (value === "good") {
    return "Good";
  }
  if (value === "defect") {
    return "Defect";
  }
  if (value === "unsure") {
    return "Unsure";
  }
  if (value === "note") {
    return "Note";
  }
  return "Unlabeled";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}
