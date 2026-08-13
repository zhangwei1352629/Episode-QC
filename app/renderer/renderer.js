import { G1Viewer } from "./g1-viewer.bundle.js";
import {
  labelSupportsTarget,
  resolveSelectedTarget,
  targetTypesDescription,
} from "./target-selection.mjs";

const $ = (id) => document.getElementById(id);

const els = {
  workspaceName: $("workspace-name"), reviewerName: $("reviewer-name"), importLabels: $("import-labels"),
  exportFormat: $("export-format"), exportResults: $("export-results"), addSource: $("add-source"), saveState: $("save-state"),
  toggleEpisodes: $("toggle-episodes"), toggleLabels: $("toggle-labels"), toolMenu: $("tool-menu"),
  episodeTotal: $("episode-total"), episodeDone: $("episode-done"), episodeErrors: $("episode-errors"),
  episodeSearch: $("episode-search"), statusFilter: $("status-filter"), episodeList: $("episode-list"),
  currentEpisode: $("current-episode"), episodeMeta: $("episode-meta"), previousEpisode: $("previous-episode"),
  nextEpisode: $("next-episode"), togglePlay: $("toggle-play"), playbackRate: $("playback-rate"),
  currentTime: $("current-time"), durationTime: $("duration-time"), cacheStatus: $("cache-status"),
  motionCard: $("motion-card"), motionCanvas: $("motion-canvas"), motionEmpty: $("motion-empty"), jointLabelLayer: $("joint-label-layer"),
  motionSource: $("motion-source"), jointSelector: $("joint-selector"), jointLabels: $("joint-labels"), resetView: $("reset-view"), selectedJoint: $("selected-joint"),
  cameraGrid: $("camera-grid"), selectionLabel: $("selection-label"), markIn: $("mark-in"), markOut: $("mark-out"),
  loopSelection: $("loop-selection"), coverageTracks: $("coverage-tracks"), annotationTrack: $("annotation-track"),
  timelineRange: $("timeline-range"), timelineEnd: $("timeline-end"), scopeTabs: $("scope-tabs"),
  labelSearch: $("label-search"), labelGroupFilter: $("label-group-filter"), labelCount: $("label-count"),
  labelSetMeta: $("label-set-meta"), targetContext: $("target-context"), labelList: $("label-list"),
  annotationComment: $("annotation-comment"), undo: $("undo"), redo: $("redo"),
  annotationCount: $("annotation-count"), annotationList: $("annotation-list"), decisionGrid: $("decision-grid"), decisionCurrent: $("decision-current"),
  needsRecheck: $("needs-recheck"), toastStack: $("toast-stack"), annotationEditor: $("annotation-editor"),
  editId: $("edit-id"), editStart: $("edit-start"), editEnd: $("edit-end"), editSeverity: $("edit-severity"),
  editAction: $("edit-action"), editComment: $("edit-comment"), deleteAnnotation: $("delete-annotation"),
  saveEdit: $("save-edit"), currentTaskName: $("current-task-name"), currentTaskCode: $("current-task-code"),
  currentTaskPath: $("current-task-path"), currentTaskStatus: $("current-task-status"),
  openTaskCenter: $("open-task-center"), rescanTask: $("rescan-task"), taskCenter: $("task-center"),
  closeTaskCenter: $("close-task-center"), taskCenterSummary: $("task-center-summary"),
  taskList: $("task-list"), taskCenterImport: $("task-center-import"), submitFlowTask: $("submit-flow-task"),
  clearLocalTaskHistory: $("clear-local-task-history"),
  flowTaskStatus: $("flow-task-status"), flowTaskList: $("flow-task-list"),
  flowTaskPanel: $("flow-task-panel"), localTaskPanel: $("local-task-panel"),
  localTaskTitle: $("local-task-title"), localTaskDescription: $("local-task-description"),
  taskCenterNote: $("task-center-note"),
  flowLoginForm: $("flow-login-form"), flowBaseUrl: $("flow-base-url"),
  flowReviewerSelect: $("flow-reviewer-select"), refreshFlowReviewers: $("refresh-flow-reviewers"), flowLogin: $("flow-login"),
  flowLogout: $("flow-logout"), refreshFlowJobs: $("refresh-flow-jobs"),
  labelLibraryStatus: $("label-library-status"), labelSetList: $("label-set-list"), refreshLabelSets: $("refresh-label-sets")
};

const state = {
  workspace: null,
  tasks: [],
  currentTask: null,
  currentTaskId: null,
  episodes: [],
  filteredEpisodes: [],
  labelSchema: null,
  detail: null,
  cache: null,
  currentEpisodeId: null,
  playbackEpisodeId: null,
  loadToken: 0,
  playheadNs: 0,
  durationNs: 0,
  playing: false,
  playbackRate: 1,
  lastTick: performance.now(),
  lastVisualRequest: 0,
  visualPending: false,
  selectionStartNs: null,
  selectionEndNs: null,
  scope: "time_range",
  selectedCameraId: null,
  selectedJoint: null,
  selectedBaseTarget: "global",
  motionFrame: null,
  robotActionFrame: null,
  motionSource: "policy",
  projectedJoints: [],
  cameraYaw: -0.15,
  cameraPitch: 0.12,
  cameraZoom: 1,
  drag: null,
  timelineSelecting: false,
  timelineAnchorNs: null,
  reviewerTimer: null,
  platform: { enabled: true, connected: false, jobs: [] },
  platformReviewers: [],
  labelSets: [],
  pendingFlowJobCode: null,
  flowPollTimer: null
};

const g1Viewer = new G1Viewer(els.motionCanvas, (status, error) => {
  if (status === "ready") {
    els.motionCard.classList.add("model-ready");
    if (state.motionFrame?.positions?.length || state.robotActionFrame?.jointPositions?.length) els.motionEmpty.hidden = true;
    drawMotion();
  } else if (status === "error") {
    els.motionEmpty.hidden = false;
    els.motionEmpty.textContent = "G1 29DOF 模型载入失败";
    console.error("G1 29DOF model load failed", error);
  }
});

const WHOLE_BODY_JOINT = "whole_body";

async function initialize() {
  restoreWorkspaceLayout();
  bindEvents();
  syncInteractiveState();
  setSaveState("saving", "打开中…");
  try {
    const taskPayload = await window.episodeQc.getTasks();
    state.tasks = taskPayload.tasks || [];
    const savedTaskId = window.localStorage.getItem("episodeQcActiveTaskId");
    state.currentTaskId = state.tasks.some((item) => item.id === savedTaskId)
      ? savedTaskId
      : state.tasks.find((item) => item.last_episode_id)?.id || state.tasks[0]?.id || null;
    await refreshWorkspace({ preserveEpisode: false });
    await refreshLabelSets({ quiet: true });
    await refreshPlatformJobs({ quiet: true });
    setSaveState("saved", "已保存");
    const recentEpisodeId = state.currentTask?.last_episode_id;
    if (recentEpisodeId && state.episodes.some((item) => item.id === recentEpisodeId)) {
      openEpisode(recentEpisodeId);
    } else if (state.episodes.length) {
      openEpisode(state.episodes[0].id);
    }
  } catch (error) {
    setSaveState("error", "打开失败");
    toast(error.message || String(error), "error", 7000);
  }
  requestAnimationFrame(playbackLoop);
}

async function refreshWorkspace({ preserveEpisode = true } = {}) {
  const payload = await window.episodeQc.getWorkspaceState(state.currentTaskId);
  state.workspace = payload.workspace;
  state.tasks = payload.tasks || [];
  state.currentTask = payload.selected_task || null;
  state.currentTaskId = state.currentTask?.id || null;
  state.episodes = payload.episodes || [];
  state.labelSchema = payload.label_schema;
  if (state.currentTaskId) window.localStorage.setItem("episodeQcActiveTaskId", state.currentTaskId);
  else window.localStorage.removeItem("episodeQcActiveTaskId");
  els.workspaceName.textContent = payload.workspace.name;
  if (document.activeElement !== els.reviewerName) els.reviewerName.value = payload.workspace.reviewer_name || "";
  renderTaskContext();
  renderEpisodeList();
  renderLabels();
  if (!preserveEpisode || !state.episodes.some((item) => item.id === state.currentEpisodeId)) {
    state.currentEpisodeId = null;
    clearEpisodeView();
  }
}

function bindEvents() {
  window.episodeQc.onEpisodeCacheReady(handleWorkerEvent);
  els.toggleEpisodes.addEventListener("click", () => toggleWorkspacePanel("episodes"));
  els.toggleLabels.addEventListener("click", () => toggleWorkspacePanel("labels"));
  document.addEventListener("pointerdown", (event) => {
    if (els.toolMenu.open && !els.toolMenu.contains(event.target)) els.toolMenu.open = false;
  });
  els.toolMenu.addEventListener("click", (event) => {
    if (event.target.closest("#import-labels, .download-button")) window.setTimeout(() => { els.toolMenu.open = false; }, 0);
  });
  els.toolMenu.addEventListener("toggle", () => {
    if (els.toolMenu.open) refreshLabelSets({ quiet: true });
  });
  els.refreshLabelSets.addEventListener("click", () => refreshLabelSets());
  els.labelSetList.addEventListener("click", handleLabelSetAction);
  els.addSource.addEventListener("click", addSource);
  els.taskCenterImport.addEventListener("click", addSource);
  els.openTaskCenter.addEventListener("click", () => { renderTaskContext(); refreshPlatformJobs({ quiet: true }); els.taskCenter.showModal(); });
  els.closeTaskCenter.addEventListener("click", () => els.taskCenter.close());
  els.rescanTask.addEventListener("click", rescanCurrentTask);
  els.submitFlowTask.addEventListener("click", submitCurrentFlowTask);
  els.flowLoginForm.addEventListener("submit", loginPlatform);
  els.refreshFlowReviewers.addEventListener("click", loadPlatformReviewers);
  els.flowLogout.addEventListener("click", logoutPlatform);
  els.refreshFlowJobs.addEventListener("click", () => refreshPlatformJobs());
  els.flowTaskList.addEventListener("click", handleFlowTaskAction);
  els.taskList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-task-id]");
    if (item) switchTask(item.dataset.taskId);
  });
  els.clearLocalTaskHistory.addEventListener("click", clearLocalTaskHistory);
  els.importLabels.addEventListener("click", importLabels);
  els.exportResults.addEventListener("click", exportResults);
  els.episodeSearch.addEventListener("input", renderEpisodeList);
  els.statusFilter.addEventListener("change", renderEpisodeList);
  els.episodeList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-episode-id]");
    if (item) openEpisode(item.dataset.episodeId);
  });
  els.previousEpisode.addEventListener("click", () => moveEpisode(-1));
  els.nextEpisode.addEventListener("click", () => moveEpisode(1));
  els.togglePlay.addEventListener("click", togglePlayback);
  els.playbackRate.addEventListener("change", () => { state.playbackRate = Number(els.playbackRate.value); });
  els.timelineRange.addEventListener("input", () => seekTo((Number(els.timelineRange.value) / 1_000_000) * state.durationNs));
  els.markIn.addEventListener("click", markSelectionStart);
  els.markOut.addEventListener("click", markSelectionEnd);
  els.scopeTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-scope]");
    if (!button) return;
    state.scope = button.dataset.scope;
    els.scopeTabs.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    renderLabels();
  });
  els.labelSearch.addEventListener("input", renderLabels);
  els.labelGroupFilter.addEventListener("change", renderLabels);
  els.targetContext.addEventListener("click", (event) => {
    const button = event.target.closest("[data-target-type]");
    if (button) selectAnnotationTarget(button.dataset.targetType, button.dataset.targetKey || null);
  });
  els.targetContext.addEventListener("change", (event) => {
    if (event.target.matches("[data-target-joint]")) {
      selectAnnotationTarget(event.target.value ? "joint" : "mocap", event.target.value || null);
    }
  });
  els.labelList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-label-code]");
    if (button) createAnnotation(button.dataset.labelCode);
  });
  els.annotationList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-annotation-id]");
    if (item) openAnnotationEditor(item.dataset.annotationId);
  });
  els.annotationTrack.addEventListener("click", (event) => {
    const item = event.target.closest("[data-annotation-id]");
    if (item) openAnnotationEditor(item.dataset.annotationId);
  });
  els.annotationTrack.addEventListener("pointerdown", beginTimelineSelection);
  window.addEventListener("pointermove", updateTimelineSelection);
  window.addEventListener("pointerup", endTimelineSelection);
  els.annotationTrack.addEventListener("dblclick", (event) => {
    if (event.target.closest("[data-annotation-id]")) return;
    seekTo(timelineTimeFromPointer(event));
    state.scope = "time_point";
    els.scopeTabs.querySelectorAll("button").forEach((button) => button.classList.toggle("active", button.dataset.scope === state.scope));
    renderLabels();
  });
  els.undo.addEventListener("click", undo);
  els.redo.addEventListener("click", redo);
  els.decisionGrid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-decision]");
    if (button) setDecision(button.dataset.decision);
  });
  els.needsRecheck.addEventListener("click", () => setReviewStatus("needs_recheck"));
  els.resetView.addEventListener("click", resetMotionView);
  els.motionSource.addEventListener("change", () => {
    state.motionSource = els.motionSource.value;
    state.robotActionFrame = null;
    drawMotion();
    requestVisualFrames(true);
  });
  els.jointSelector.addEventListener("change", () => selectJoint(els.jointSelector.value || null));
  els.jointLabels.addEventListener("change", drawMotion);
  els.jointLabelLayer.addEventListener("click", (event) => {
    const button = event.target.closest("[data-joint-name]");
    if (button) selectJoint(button.dataset.jointName);
  });
  els.motionCanvas.addEventListener("pointerdown", motionPointerDown);
  window.addEventListener("pointermove", motionPointerMove);
  window.addEventListener("pointerup", motionPointerUp);
  els.motionCanvas.addEventListener("wheel", motionWheel, { passive: false });
  els.motionCanvas.addEventListener("click", selectJointAtPointer);
  window.addEventListener("resize", drawMotion);
  document.addEventListener("keydown", handleKeyboard);
  els.reviewerName.addEventListener("input", scheduleReviewerSave);
  els.saveEdit.addEventListener("click", saveAnnotationEdit);
  els.deleteAnnotation.addEventListener("click", deleteCurrentAnnotation);
  window.addEventListener("beforeunload", () => {
    state.workerEventsClose?.();
    if (state.flowPollTimer) window.clearInterval(state.flowPollTimer);
    savePlayhead();
  });
}

function restoreWorkspaceLayout() {
  setWorkspacePanel("episodes", window.localStorage.getItem("episodeQcEpisodesVisible") !== "false", false);
  setWorkspacePanel("labels", window.localStorage.getItem("episodeQcLabelsVisible") !== "false", false);
}

function toggleWorkspacePanel(panel) {
  const hiddenClass = panel === "episodes" ? "episodes-collapsed" : "labels-collapsed";
  setWorkspacePanel(panel, document.body.classList.contains(hiddenClass));
}

function setWorkspacePanel(panel, visible, persist = true) {
  const isEpisodes = panel === "episodes";
  const hiddenClass = isEpisodes ? "episodes-collapsed" : "labels-collapsed";
  const button = isEpisodes ? els.toggleEpisodes : els.toggleLabels;
  document.body.classList.toggle(hiddenClass, !visible);
  button.setAttribute("aria-pressed", String(visible));
  button.classList.toggle("active", visible);
  if (persist) window.localStorage.setItem(isEpisodes ? "episodeQcEpisodesVisible" : "episodeQcLabelsVisible", String(visible));
  window.setTimeout(drawMotion, 190);
}

function handleWorkerEvent(payload) {
  if (payload?.type === "platform_job") {
    const job = state.platform?.jobs?.find((item) => item.code === payload.jobCode);
    if (job?.local_caching) {
      job.local_progress = payload;
      renderPlatformJobs();
    }
    return;
  }
  handleEpisodeCacheReady(payload);
}

function handleEpisodeCacheReady(payload) {
  if (!payload || payload.episodeId !== state.playbackEpisodeId) return;
  if (payload.error) {
    setCacheStatus("ready", "优先流可用 · 后台完整缓存失败");
    toast(`后台缓存失败：${payload.error}`, "error", 7000);
    return;
  }
  state.cache = payload.cache;
  syncInteractiveState();
  renderCameras();
  renderMotionAvailability();
  setCacheStatus("ready", "完整播放缓存已就绪");
  requestVisualFrames(true);
}

function renderTaskContext() {
  const task = state.currentTask;
  els.currentTaskName.textContent = task?.task_name || "尚未导入任务";
  els.currentTaskCode.textContent = task?.task_code || "—";
  const taskPath = task?.local_source_path || task?.source_uri || "点击“导入新任务”选择数据目录";
  els.currentTaskPath.textContent = taskPath;
  els.currentTaskPath.title = taskPath;
  els.currentTaskStatus.textContent = task
    ? `${taskStatusName(task.status)} · ${task.completed_count}/${task.episode_count}`
    : "未加载";
  els.currentTaskStatus.dataset.status = task?.status || "empty";
  els.rescanTask.disabled = !task;
  const flowTask = Boolean(task?.origin === "flow" && task?.flow_job_code);
  els.submitFlowTask.hidden = !flowTask || !["completed", "submitted"].includes(task.status);
  els.submitFlowTask.disabled = task?.status === "submitted";
  els.submitFlowTask.textContent = task?.status === "submitted" ? "已提交 Flow" : "提交到 Flow";

  const completedTasks = state.tasks.filter((item) => ["completed", "submitted", "archived"].includes(item.status)).length;
  const failedTasks = state.tasks.filter((item) => item.status === "failed").length;
  const clearableLocalTasks = state.tasks.filter(
    (item) => item.origin !== "flow" && item.id !== state.currentTaskId,
  );
  els.clearLocalTaskHistory.disabled = !clearableLocalTasks.length;
  els.clearLocalTaskHistory.textContent = clearableLocalTasks.length
    ? `清空历史导入 (${clearableLocalTasks.length})`
    : "无历史导入";
  els.taskCenterSummary.innerHTML = [
    `<span>任务 ${state.tasks.length}</span>`,
    `<span>进行中 ${state.tasks.length - completedTasks - failedTasks}</span>`,
    `<span>已完成 ${completedTasks}</span>`,
    `<span>异常 ${failedTasks}</span>`,
  ].join("");
  if (!state.tasks.length) {
    els.taskList.innerHTML = '<div class="empty-panel">暂无任务，导入数据目录后开始质检</div>';
    return;
  }
  els.taskList.innerHTML = state.tasks.map((item) => {
    const path = item.local_source_path || item.source_uri || "";
    const issue = item.import_error ? ` · ${item.import_error}` : "";
    return `
      <button class="task-list-item ${item.id === state.currentTaskId ? "active" : ""}" data-task-id="${escapeHtml(item.id)}" type="button">
        <span class="task-list-copy">
          <strong>${escapeHtml(item.task_name)}</strong>
          <small>${escapeHtml(item.task_code)} · ${escapeHtml(taskStatusName(item.status))}${escapeHtml(issue)}</small>
          <span title="${escapeHtml(path)}">${escapeHtml(path)}</span>
        </span>
        <span class="task-list-progress"><strong>${item.completed_count}/${item.episode_count}</strong><span>${formatBytes(item.source_size_bytes)} · 异常 ${item.error_count}</span></span>
      </button>`;
  }).join("");
}

async function refreshPlatformJobs({ quiet = false } = {}) {
  try {
    const payload = await window.episodeQc.getPlatformJobs();
    state.platform = payload;
    renderPlatformJobs();
    const claimed = payload.jobs?.find(
      (item) => item.code === state.pendingFlowJobCode && item.local_task_id,
    );
    if (claimed) {
      state.pendingFlowJobCode = null;
      if (!claimed.local_caching) stopFlowPolling();
      toast(
        claimed.local_caching
          ? `任务 ${claimed.code} 的首个 Episode 已就绪，后台继续缓存`
          : `任务 ${claimed.code} 已缓存并进入质检`,
        "success",
        5500,
      );
      await switchTask(claimed.local_task_id);
      return;
    }
    const stillCaching = payload.jobs?.some(
      (item) => item.local_caching || (!item.local_task_id && ["claimed", "caching", "cache_ready"].includes(item.status)),
    );
    if (!stillCaching) stopFlowPolling();
  } catch (error) {
    if (!quiet) toast(error.message || String(error), "error", 6500);
    els.flowTaskStatus.textContent = "连接失败";
  }
}

function renderPlatformJobs() {
  const platform = state.platform || { enabled: true, connected: false, jobs: [] };
  const standalone = platform.enabled === false;
  els.taskCenter.classList.toggle("standalone-mode", standalone);
  els.flowTaskPanel.hidden = standalone;
  els.localTaskTitle.textContent = standalone ? "单机 QC 任务" : "本地 QC 任务";
  els.localTaskDescription.textContent = standalone
    ? "无需 Flow，直接导入本机或已挂载 NAS 目录"
    : "已领取任务和手工导入任务";
  els.taskCenterNote.textContent = standalone
    ? "单机模式只保存本地索引、缓存和质检结果，不连接 Flow，也不会修改原始数据。"
    : "清空历史导入只删除本地索引和派生缓存，不删除原始数据，也不影响 Flow 任务。";
  if (standalone) return;
  els.flowLoginForm.hidden = Boolean(platform.connected);
  els.flowLogout.hidden = !platform.connected;
  els.refreshFlowJobs.hidden = !platform.connected;
  if (!platform.connected) {
    els.flowTaskStatus.textContent = platform.error || "尚未选择质检员";
    els.flowTaskList.innerHTML = '<div class="empty-panel">刷新并选择质检员后显示可领取任务</div>';
    if (!els.flowBaseUrl.value) {
      els.flowBaseUrl.value = window.localStorage.getItem("episodeQcFlowUrl") || platform.default_base_url || "http://127.0.0.1:8000";
    }
    return;
  }
  els.flowTaskStatus.textContent = `${platform.reviewer || platform.username} · ${platform.jobs.length} 个批次`;
  if (!platform.jobs.length) {
    els.flowTaskList.innerHTML = '<div class="empty-panel">当前没有可见的 Flow 质检任务</div>';
    return;
  }
  els.flowTaskList.innerHTML = platform.jobs.map((job) => {
    const action = flowJobAction(job);
    const progress = (job.local_caching || job.cache_complete === false || ["claimed", "caching", "cache_ready"].includes(job.status))
      ? ` · ${flowJobProgressLabel(job)}`
      : "";
    return `
      <div class="flow-task-item">
        <div>
          <strong>${escapeHtml(job.task_name || job.asset_id || job.code)}</strong>
          <small>${escapeHtml(job.code)} · ${escapeHtml(flowJobStatusName(job.status))}${escapeHtml(progress)}</small>
          <span>${escapeHtml(job.collector || "未知采集员")} · ${job.required_episode_count || job.episodes?.length || 0} Episode · ${formatBytes(job.asset_size_bytes)}</span>
        </div>
        <button type="button" data-flow-job-code="${escapeHtml(job.code)}" data-flow-action="${action.name}" ${action.disabled ? "disabled" : ""}>${escapeHtml(action.label)}</button>
      </div>`;
  }).join("");
}

function flowJobProgressLabel(job) {
  const local = job.local_progress || {};
  if (local.phase === "verifying" && Number(local.total_files) > 0) {
    return `校验 ${Number(local.verified_files || 0)}/${Number(local.total_files)} 个文件`;
  }
  const cachedEpisodes = Number(local.cached_episode_count ?? job.cached_episode_count ?? 0);
  const totalEpisodes = Number(local.total_episode_count ?? job.total_episode_count ?? 0);
  const progress = Number(job.cache_progress || local.progress || 0);
  if (totalEpisodes > 0) return `已缓存 ${cachedEpisodes}/${totalEpisodes} Episode · ${progress}%`;
  return `缓存 ${progress}%`;
}

function flowJobAction(job) {
  if (job.local_task_id && job.cache_complete === false && !job.local_caching) {
    return { name: "claim", label: "继续缓存", disabled: false };
  }
  if (job.local_task_id) return { name: "open", label: job.local_task_status === "submitted" ? "已提交" : "打开任务", disabled: false };
  if (job.local_caching) return { name: "none", label: flowJobProgressLabel(job), disabled: true };
  if (["claimed", "caching", "cache_ready"].includes(job.status)) return { name: "claim", label: "继续缓存", disabled: false };
  if (job.status === "pending") return { name: "claim", label: "领取并缓存", disabled: false };
  if (job.status === "failed") return { name: "claim", label: "重试缓存", disabled: false };
  if (job.status === "completed") return { name: "none", label: "已完成", disabled: true };
  return { name: "none", label: "等待数据", disabled: true };
}

async function loginPlatform(event) {
  event.preventDefault();
  const employeeNo = els.flowReviewerSelect.value;
  if (!employeeNo) return toast("请先刷新并选择质检员", "error");
  setBusyButton(els.flowLogin, true, "加载中…");
  try {
    const baseUrl = els.flowBaseUrl.value.trim();
    state.platform = await window.episodeQc.loginPlatform({ baseUrl, employeeNo });
    window.localStorage.setItem("episodeQcFlowUrl", baseUrl);
    window.localStorage.setItem("episodeQcFlowReviewer", employeeNo);
    renderPlatformJobs();
    if (!els.reviewerName.value.trim() && state.platform.reviewer) {
      els.reviewerName.value = state.platform.reviewer;
      scheduleReviewerSave();
    }
    toast(`已选择质检员：${state.platform.reviewer || employeeNo}`, "success");
  } catch (error) {
    toast(error.message || String(error), "error", 6500);
  } finally {
    setBusyButton(els.flowLogin, false, "选择并加载任务");
  }
}

async function loadPlatformReviewers() {
  const baseUrl = els.flowBaseUrl.value.trim();
  if (!baseUrl) return toast("请填写 Flow 地址", "error");
  setBusyButton(els.refreshFlowReviewers, true, "刷新中…");
  try {
    const payload = await window.episodeQc.getPlatformReviewers(baseUrl);
    state.platformReviewers = payload.reviewers || [];
    const saved = window.localStorage.getItem("episodeQcFlowReviewer") || "";
    els.flowReviewerSelect.innerHTML = [
      '<option value="">请选择质检员</option>',
      ...state.platformReviewers.map((reviewer) => `<option value="${escapeHtml(reviewer.employee_no)}">${escapeHtml(reviewer.display_name)} · ${escapeHtml(reviewer.team_name || reviewer.employee_no)}</option>`),
    ].join("");
    if (state.platformReviewers.some((item) => item.employee_no === saved)) {
      els.flowReviewerSelect.value = saved;
    } else if (state.platformReviewers.length === 1) {
      els.flowReviewerSelect.value = state.platformReviewers[0].employee_no;
    }
    window.localStorage.setItem("episodeQcFlowUrl", baseUrl);
    els.flowTaskStatus.textContent = `已加载 ${state.platformReviewers.length} 名质检员`;
    toast(`已刷新 ${state.platformReviewers.length} 名质检员`, "success");
  } catch (error) {
    toast(error.message || String(error), "error", 6500);
  } finally {
    setBusyButton(els.refreshFlowReviewers, false, "刷新质检员");
  }
}

async function logoutPlatform() {
  try {
    state.platform = await window.episodeQc.logoutPlatform();
    renderPlatformJobs();
    toast("已退出 Flow");
  } catch (error) { toast(error.message || String(error), "error", 6500); }
}

async function handleFlowTaskAction(event) {
  const button = event.target.closest("[data-flow-job-code]");
  if (!button || button.disabled) return;
  const job = state.platform.jobs.find((item) => item.code === button.dataset.flowJobCode);
  if (!job) return;
  if (button.dataset.flowAction === "open" && job.local_task_id) {
    try {
      if (!["completed", "submitted", "archived"].includes(job.local_task_status)) {
        await window.episodeQc.startPlatformJob(job.code);
      }
      await switchTask(job.local_task_id);
    } catch (error) {
      toast(error.message || String(error), "error", 7000);
      await refreshPlatformJobs({ quiet: true });
    }
    return;
  }
  if (button.dataset.flowAction !== "claim") return;
  setBusyButton(button, true, "领取中…");
  try {
    const result = await window.episodeQc.claimPlatformJob(job.code);
    state.pendingFlowJobCode = job.code;
    toast(result.accepted ? `已领取 ${job.code}，正在后台完整缓存` : `${job.code} 已在本机处理`, "success", 5500);
    startFlowPolling();
    await refreshPlatformJobs({ quiet: true });
  } catch (error) {
    toast(error.message || String(error), "error", 7000);
    await refreshPlatformJobs({ quiet: true });
  }
}

function startFlowPolling() {
  if (state.flowPollTimer) return;
  state.flowPollTimer = window.setInterval(() => refreshPlatformJobs({ quiet: true }), 1500);
}

function stopFlowPolling() {
  if (!state.flowPollTimer) return;
  window.clearInterval(state.flowPollTimer);
  state.flowPollTimer = null;
}

async function submitCurrentFlowTask() {
  const task = state.currentTask;
  if (!task?.flow_job_code || task.status !== "completed") return;
  if (!window.confirm(`确认把 ${task.flow_job_code} 的全部 Episode 质检结论提交到 Flow？`)) return;
  setBusyButton(els.submitFlowTask, true, "提交中…");
  try {
    await window.episodeQc.submitPlatformJob(task.flow_job_code);
    await refreshWorkspace();
    await refreshPlatformJobs({ quiet: true });
    toast(`质检结果已提交到 Flow：${task.flow_job_code}`, "success", 6000);
  } catch (error) {
    toast(error.message || String(error), "error", 7000);
  } finally {
    renderTaskContext();
  }
}

async function switchTask(taskId) {
  if (!taskId || taskId === state.currentTaskId) {
    els.taskCenter.close();
    return;
  }
  setSaveState("saving", "切换任务…");
  try {
    await savePlayhead();
    state.playing = false;
    state.currentTaskId = taskId;
    state.currentEpisodeId = null;
    await refreshWorkspace({ preserveEpisode: false });
    els.taskCenter.close();
    const episodeId = state.currentTask?.last_episode_id;
    const targetEpisode = state.episodes.some((item) => item.id === episodeId) ? episodeId : state.episodes[0]?.id;
    if (targetEpisode) openEpisode(targetEpisode);
    setSaveState("saved", "已切换");
  } catch (error) {
    setSaveState("error", "切换失败");
    toast(error.message || String(error), "error", 7000);
  }
}

async function clearLocalTaskHistory() {
  const candidates = state.tasks.filter(
    (item) => item.origin !== "flow" && item.id !== state.currentTaskId,
  );
  if (!candidates.length) return;
  if (!window.confirm(
    `确认清空 ${candidates.length} 个历史导入任务？\n\n只删除 QC 本地索引和派生缓存，原始数据目录不会被删除；当前任务和 Flow 任务会保留。`,
  )) return;
  setBusyButton(els.clearLocalTaskHistory, true, "清理中…");
  try {
    const result = await window.episodeQc.clearLocalTaskHistory(state.currentTaskId);
    await refreshWorkspace({ preserveEpisode: true });
    toast(`已清空 ${result.removed_count} 个历史导入；原始数据未删除`, "success", 6000);
  } catch (error) {
    toast(error.message || String(error), "error", 7000);
  } finally {
    renderTaskContext();
  }
}

async function rescanCurrentTask() {
  if (!state.currentTaskId) return;
  setBusyButton(els.rescanTask, true, "扫描中…");
  try {
    const result = await window.episodeQc.rescanTask(state.currentTaskId);
    await refreshWorkspace();
    toast(
      `任务“${result.task.task_name}”扫描完成：发现 ${result.discovered}，成功 ${result.ready}，失败 ${result.failed}`,
      result.failed ? "error" : "success",
      6500,
    );
  } catch (error) {
    toast(error.message || String(error), "error", 7000);
  } finally {
    setBusyButton(els.rescanTask, false, "重新扫描");
  }
}

async function addSource() {
  setBusyButton(els.addSource, true, "导入中…");
  setBusyButton(els.taskCenterImport, true, "导入中…");
  try {
    const result = await window.episodeQc.addSource();
    if (!result) return;
    if (!result.ready) {
      const taskPayload = await window.episodeQc.getTasks();
      state.tasks = taskPayload.tasks || [];
      renderTaskContext();
      toast(`任务没有加载到可用 Episode：${result.task?.import_error || "请检查目录结构"}`, "error", 7500);
      return;
    }
    state.currentTaskId = result.task_id;
    state.currentEpisodeId = null;
    await refreshWorkspace({ preserveEpisode: false });
    els.taskCenter.close();
    const targetEpisode = state.currentTask?.last_episode_id || state.episodes[0]?.id;
    if (targetEpisode) openEpisode(targetEpisode);
    toast(
      `${result.existing_task ? "已有任务已重新扫描" : "新任务加载成功"}：${result.task.task_name}；发现 ${result.discovered}，成功 ${result.ready}，失败 ${result.failed}`,
      result.failed ? "error" : "success",
      7500,
    );
  } catch (error) {
    toast(error.message || String(error), "error", 7000);
  } finally {
    setBusyButton(els.addSource, false, "导入新任务");
    setBusyButton(els.taskCenterImport, false, "导入新任务");
  }
}

async function importLabels() {
  setBusyButton(els.importLabels, true, "校验中…");
  try {
    const result = await window.episodeQc.importLabelSchema();
    if (!result) return;
    if (!result.readyToConfirm) {
      toast(`标签库校验失败：${result.preview.errors.join("；")}`, "error", 8500);
      return;
    }
    const preview = result.preview;
    const templateText = preview.template_mode === "simple" ? "中文简易模板" : "高级完整模板";
    const confirmed = window.confirm(
      `${templateText}：${preview.schema.schema.label_set_name} v${preview.version}\n新增 ${preview.added.length}，更新 ${preview.updated.length}，不变 ${preview.unchanged.length}，保留旧标签 ${preview.preserved.length}\n\n确认导入并激活吗？`
    );
    if (!confirmed) return;
    const imported = await window.episodeQc.confirmLabelSchema();
    toast(`标签库 ${imported.label_set_id} ${imported.version} 已导入：新增 ${imported.added.length}，更新 ${imported.updated.length}`, "success", 6000);
    await refreshWorkspace();
    await refreshLabelSets({ quiet: true });
    if (state.currentEpisodeId) await reloadCurrentEpisode();
  } catch (error) {
    toast(error.message || String(error), "error", 7000);
  } finally {
    setBusyButton(els.importLabels, false, "导入标签库");
  }
}

async function refreshLabelSets({ quiet = false } = {}) {
  try {
    const payload = await window.episodeQc.getLabelSets();
    state.labelSets = payload.label_sets || [];
    renderLabelSets();
  } catch (error) {
    els.labelLibraryStatus.textContent = "读取失败";
    if (!quiet) toast(error.message || String(error), "error", 6500);
  }
}

function renderLabelSets() {
  const labelSets = state.labelSets || [];
  els.labelLibraryStatus.textContent = labelSets.length
    ? `${labelSets.length} 个版本 · ${labelSets.reduce((total, item) => total + item.label_count, 0)} 个标签`
    : "尚未导入";
  if (!labelSets.length) {
    els.labelSetList.innerHTML = '<div class="empty-panel">尚未导入标签库</div>';
    return;
  }
  els.labelSetList.innerHTML = labelSets.map((item) => `
    <div class="label-set-item${item.active ? " active" : ""}">
      <div class="label-set-copy">
        <div class="label-set-title"><strong>${escapeHtml(item.name)}</strong>${item.active ? "<em>当前启用</em>" : ""}</div>
        <small>v${escapeHtml(item.version)} · ${item.label_count} 标签 · ${item.annotation_count} 条标注引用</small>
      </div>
      <div class="label-set-actions">
        ${item.active ? "" : `<button type="button" data-label-set-id="${escapeHtml(item.id)}" data-label-set-action="activate">启用</button>`}
        <button type="button" data-label-set-id="${escapeHtml(item.id)}" data-label-set-action="delete" ${labelSets.length <= 1 ? "disabled title=\"至少保留一个标签库\"" : ""}>删除</button>
      </div>
    </div>`).join("");
}

async function handleLabelSetAction(event) {
  const button = event.target.closest("[data-label-set-id]");
  if (!button || button.disabled) return;
  const item = state.labelSets.find((entry) => entry.id === button.dataset.labelSetId);
  if (!item) return;
  setBusyButton(button, true, button.dataset.labelSetAction === "delete" ? "删除中…" : "启用中…");
  try {
    if (button.dataset.labelSetAction === "delete") {
      if (!window.confirm(`确认删除标签库“${item.name}” v${item.version}？\n历史标注不会被删除。`)) {
        renderLabelSets();
        return;
      }
      const result = await window.episodeQc.deleteLabelSet(item.id);
      state.labelSets = result.label_sets || [];
      toast(`已删除标签库：${item.name} v${item.version}`, "success");
    } else {
      const result = await window.episodeQc.activateLabelSet(item.id);
      state.labelSets = result.label_sets || [];
      toast(`已启用标签库：${item.name} v${item.version}`, "success");
    }
    await refreshWorkspace();
    if (state.currentEpisodeId) await reloadCurrentEpisode();
    renderLabelSets();
  } catch (error) {
    toast(error.message || String(error), "error", 7000);
    await refreshLabelSets({ quiet: true });
  }
}

async function exportResults() {
  if (!state.currentTaskId) return toast("请先选择 QC 任务", "error");
  setBusyButton(els.exportResults, true, "导出中…");
  try {
    const episodeIds = state.episodes.map((item) => item.id);
    const result = await window.episodeQc.exportWorkspace({
      taskId: state.currentTaskId,
      episodeIds,
      format: els.exportFormat.value,
    });
    if (result) {
      const outputText = result.output_files?.join("；") || result.output_file;
      toast(`已导出 ${result.task_count || 1} 个任务、${result.episode_count} 条 Episode、${result.annotation_count} 条标注：${outputText}`, "success", 8000);
    }
  } catch (error) {
    toast(error.message || String(error), "error", 7000);
  } finally {
    setBusyButton(els.exportResults, false, "导出结果");
  }
}

function renderEpisodeList() {
  const query = els.episodeSearch.value.trim().toLowerCase();
  const status = els.statusFilter.value;
  state.filteredEpisodes = state.episodes.filter((episode) => {
    const text = `${episode.episode_name} ${episode.relative_path} ${episode.data_group}`.toLowerCase();
    return (!query || text.includes(query)) && (status === "all" || episode.review_status === status);
  });
  els.episodeTotal.textContent = state.episodes.length;
  els.episodeDone.textContent = state.episodes.filter((item) => ["completed", "reviewed"].includes(item.review_status)).length;
  els.episodeErrors.textContent = state.episodes.filter((item) => item.import_status !== "ready").length;
  if (!state.filteredEpisodes.length) {
    els.episodeList.innerHTML = `<div class="empty-panel">${state.episodes.length ? "没有符合筛选的 Episode" : "添加数据目录后开始质检"}</div>`;
    syncInteractiveState();
    return;
  }
  els.episodeList.innerHTML = state.filteredEpisodes.map((episode) => `
    <button class="episode-item ${episode.id === state.currentEpisodeId ? "active" : ""}" data-episode-id="${escapeHtml(episode.id)}" data-status="${escapeHtml(episode.review_status)}" type="button">
      <span class="review-dot"></span>
      <span class="episode-copy">
        <strong>${escapeHtml(episode.episode_name)}</strong>
        <span title="${escapeHtml(episode.relative_path)}">${escapeHtml(episode.relative_path)}</span>
        <span class="episode-badges"><em class="status-badge">${escapeHtml(reviewStatusName(episode.review_status))}</em>${episode.quality_decision ? `<em class="decision-badge">${escapeHtml(decisionName(episode.quality_decision))}</em>` : ""}<em>${episode.camera_count} CAM</em><em>${episode.mocap_available ? "MOCAP" : "无 MOCAP"}</em><em>${episode.annotation_count} 标签</em></span>
      </span>
      <time>${formatDuration(episode.duration_sec || 0)}</time>
    </button>`).join("");
  syncInteractiveState();
}

async function openEpisode(episodeId) {
  if (!episodeId || episodeId === state.currentEpisodeId && state.cache) return;
  const token = ++state.loadToken;
  if (state.currentEpisodeId) await savePlayhead();
  state.playing = false;
  state.cache = null;
  state.detail = null;
  state.playbackEpisodeId = null;
  state.currentEpisodeId = episodeId;
  window.episodeQc.updateWorkspaceSettings({ lastEpisodeId: episodeId, taskId: state.currentTaskId }).catch(() => {});
  state.selectionStartNs = null;
  state.selectionEndNs = null;
  state.selectedCameraId = null;
  state.selectedJoint = null;
  state.selectedBaseTarget = "global";
  state.motionFrame = null;
  state.robotActionFrame = null;
  state.motionSource = "policy";
  syncJointSelectionUi();
  updatePlaybackButton();
  syncInteractiveState();
  renderEpisodeList();
  setCacheStatus("busy", "读取 Episode 元信息…");
  try {
    const detail = await window.episodeQc.getEpisode(episodeId);
    if (token !== state.loadToken) return;
    state.detail = detail;
    state.selectedBaseTarget = detail.episode.mocap_available ? "mocap" : "global";
    state.labelSchema = detail.label_schema || state.labelSchema;
    state.durationNs = Number(detail.episode.duration_ns || 0);
    state.playheadNs = Math.min(Number(detail.episode.last_playhead_ns || 0), state.durationNs);
    state.playbackEpisodeId = episodeId;
    renderEpisodeDetail();
    setCacheStatus("busy", "首次打开：正在建立只读播放缓存…");
    const cache = await window.episodeQc.prepareEpisode(state.playbackEpisodeId);
    if (token !== state.loadToken) return;
    state.cache = cache;
    syncInteractiveState();
    renderCameras();
    renderMotionAvailability();
    const cacheMessage = cache.complete
      ? (cache.reused ? "完整播放缓存已复用" : "完整播放缓存已就绪")
      : "默认相机与 Policy 已就绪 · 其余流后台缓存中…";
    setCacheStatus("ready", `${cacheMessage}${cache.decode_errors?.length ? ` · ${cache.decode_errors.length} 个解析提示` : ""}`);
    await requestVisualFrames(true);
  } catch (error) {
    if (token !== state.loadToken) return;
    setCacheStatus("error", "载入失败");
    toast(error.message || String(error), "error", 8000);
  }
}

async function reloadCurrentEpisode() {
  if (!state.currentEpisodeId) return;
  const detail = await window.episodeQc.getEpisode(state.currentEpisodeId);
  state.detail = detail;
  state.labelSchema = detail.label_schema || state.labelSchema;
  renderEpisodeDetail();
}

function renderEpisodeDetail() {
  if (!state.detail) return;
  const episode = state.detail.episode;
  els.currentEpisode.textContent = episode.episode_name;
  els.episodeMeta.textContent = `${episode.data_group} · ${episode.camera_count} 路相机 · ${episode.mocap_available ? "Mocap 可解析" : "Mocap 不可用"} · ${episode.relative_path}`;
  els.durationTime.textContent = formatClock(state.durationNs);
  els.timelineEnd.textContent = formatDuration(state.durationNs / 1e9);
  renderClock();
  renderCoverageTracks();
  renderAnnotations();
  renderLabels();
  renderDecision();
  renderTargetContext();
  syncInteractiveState();
}

function clearEpisodeView() {
  state.playbackEpisodeId = null;
  state.detail = null;
  state.cache = null;
  state.motionFrame = null;
  state.robotActionFrame = null;
  state.playing = false;
  state.selectionStartNs = null;
  state.selectionEndNs = null;
  state.durationNs = 0;
  state.playheadNs = 0;
  els.currentEpisode.textContent = "未选择 Episode";
  els.episodeMeta.textContent = "请选择左侧数据";
  els.cameraGrid.innerHTML = '<div class="empty-panel">当前 Episode 的有效相机会在此动态显示</div>';
  els.motionEmpty.hidden = false;
  els.motionEmpty.textContent = "选择 Episode 后显示 G1 29DOF 机器人";
  els.coverageTracks.innerHTML = "";
  els.annotationTrack.innerHTML = "";
  els.annotationList.innerHTML = '<div class="empty-panel">暂无标注</div>';
  els.annotationCount.textContent = "0";
  renderJointOptions([]);
  renderMotionSourceOptions();
  state.selectedCameraId = null;
  state.selectedJoint = null;
  state.selectedBaseTarget = "global";
  renderTargetContext();
  renderLabels();
  renderClock();
  renderSelection();
  renderDecision();
  updatePlaybackButton();
  syncInteractiveState();
  drawMotion();
}

function syncInteractiveState() {
  const hasEpisode = Boolean(state.detail && state.currentEpisodeId);
  const playbackReady = Boolean(state.cache && state.durationNs > 0);
  const currentIndex = state.filteredEpisodes.findIndex((item) => item.id === state.currentEpisodeId);
  els.previousEpisode.disabled = !state.filteredEpisodes.length || currentIndex <= 0;
  els.nextEpisode.disabled = !state.filteredEpisodes.length || currentIndex < 0 || currentIndex >= state.filteredEpisodes.length - 1;
  els.togglePlay.disabled = !playbackReady;
  els.playbackRate.disabled = !playbackReady;
  els.timelineRange.disabled = !hasEpisode;
  els.markIn.disabled = !hasEpisode;
  els.markOut.disabled = !hasEpisode;
  els.loopSelection.disabled = !hasEpisode;
  els.undo.disabled = !hasEpisode;
  els.redo.disabled = !hasEpisode;
  els.decisionGrid.querySelectorAll("button").forEach((button) => { button.disabled = !hasEpisode; });
  els.needsRecheck.disabled = !hasEpisode;
  els.exportResults.disabled = !state.currentTaskId;
}

function renderCameras() {
  els.cameraGrid.querySelectorAll("img[data-object-url]").forEach((image) => URL.revokeObjectURL(image.dataset.objectUrl));
  const cameras = state.cache?.cameras || [];
  els.cameraGrid.className = `camera-grid count-${Math.min(cameras.length, 6)}`;
  if (!cameras.length) {
    els.cameraGrid.innerHTML = '<div class="empty-panel">当前 Episode 无有效相机</div>';
    if (state.selectedCameraId) state.selectedCameraId = null;
    renderTargetContext();
    renderLabels();
    return;
  }
  els.cameraGrid.innerHTML = cameras.map((camera) => `
    <article class="camera-card" data-camera-id="${escapeHtml(camera.stream_id)}" data-camera-topic="${escapeHtml(camera.topic)}" title="单击选择标注目标，双击放大">
      <div class="camera-heading"><div><span class="status-dot"></span><strong>${escapeHtml(camera.display_name)}</strong></div><span class="camera-time">等待帧</span></div>
      <img alt="${escapeHtml(camera.display_name)}" />
    </article>`).join("");
  els.cameraGrid.querySelectorAll(".camera-card").forEach((card) => {
    card.addEventListener("click", (event) => { if (event.detail === 1) selectCamera(card.dataset.cameraId); });
    card.addEventListener("dblclick", () => toggleCameraFullscreen(card.dataset.cameraId));
  });
  syncCameraSelectionUi();
  renderTargetContext();
  renderLabels();
}

function selectCamera(streamId) {
  if (state.selectedCameraId === streamId) selectAnnotationTarget(state.selectedBaseTarget);
  else selectAnnotationTarget("camera", streamId);
}

function toggleCameraFullscreen(streamId) {
  const card = els.cameraGrid.querySelector(`[data-camera-id="${streamId}"]`);
  if (!card) return;
  const enabled = !card.classList.contains("fullscreen");
  els.cameraGrid.querySelectorAll(".camera-card").forEach((item) => item.classList.remove("fullscreen"));
  card.classList.toggle("fullscreen", enabled);
  els.cameraGrid.classList.toggle("has-fullscreen", enabled);
}

function renderMotionAvailability() {
  const available = Boolean(state.cache?.robot_actions?.available || state.cache?.motion?.available);
  els.motionCard.classList.toggle("ready", available);
  els.motionEmpty.hidden = available;
  if (!available) els.motionEmpty.textContent = "当前 Episode 无可用的 G1 动作或 Mocap";
  renderMotionSourceOptions();
  renderJointOptions(state.cache?.motion?.available ? state.cache.motion.joint_names || [] : []);
  renderTargetContext();
  renderLabels();
}

function renderMotionSourceOptions() {
  const sourceNames = {
    policy: "Policy 实际执行姿态（默认）",
    policy_target: "PMG 目标姿态",
    policy_command: "Policy 最终控制目标",
    soma: "SOMA 重定向动作"
  };
  const sources = state.cache?.robot_actions?.sources || [];
  const available = new Set(sources.filter((item) => item.available).map((item) => item.key));
  if (!available.has(state.motionSource)) {
    state.motionSource = ["policy", "policy_target", "policy_command", "soma"].find((key) => available.has(key)) || "policy";
  }
  els.motionSource.innerHTML = ["policy", "policy_target", "policy_command", "soma"].map((key) =>
    `<option value="${key}"${available.has(key) ? "" : " disabled"}>${sourceNames[key]}${available.has(key) ? "" : "（不可用）"}</option>`
  ).join("");
  els.motionSource.value = state.motionSource;
  els.motionSource.disabled = available.size < 2;
}

function renderCoverageTracks() {
  const streams = (state.detail?.streams || []).filter((item) => item.available);
  const priority = { mocap: 0, camera: 1 };
  const visible = [...streams].sort((a, b) => (priority[a.stream_type] ?? 3) - (priority[b.stream_type] ?? 3)).slice(0, 7);
  els.coverageTracks.innerHTML = visible.map((stream) => `
    <div class="coverage-row ${escapeHtml(stream.stream_type)}" title="${escapeHtml(stream.topic)} · ${stream.message_count} 条">
      <span>${escapeHtml(stream.display_name)}</span><span class="coverage-bar"><i></i></span>
    </div>`).join("");
}

async function requestVisualFrames(force = false) {
  if (!state.cache || !state.currentEpisodeId || !state.playbackEpisodeId || state.visualPending) return;
  const now = performance.now();
  if (!force && now - state.lastVisualRequest < 90) return;
  state.lastVisualRequest = now;
  state.visualPending = true;
  const episodeId = state.currentEpisodeId;
  const playbackEpisodeId = state.playbackEpisodeId;
  const timeNs = Math.max(0, Math.min(state.durationNs, Math.round(state.playheadNs)));
  try {
    const cameraRequests = (state.cache.cameras || []).map(async (camera) => {
      const frame = await window.episodeQc.getCameraFrame({
        episodeId: playbackEpisodeId,
        streamId: camera.stream_id,
        timeNs,
      });
      if (episodeId !== state.currentEpisodeId) return;
      const card = els.cameraGrid.querySelector(`[data-camera-id="${camera.stream_id}"]`);
      if (!card) return;
      const image = card.querySelector("img");
      if (image.dataset.frameIndex !== String(frame.frameIndex)) {
        if (image.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
        image.src = frame.dataUrl;
        if (frame.dataUrl.startsWith("blob:")) image.dataset.objectUrl = frame.dataUrl;
        else delete image.dataset.objectUrl;
        image.dataset.frameIndex = String(frame.frameIndex);
      }
      card.classList.add("ready");
      card.querySelector(".camera-time").textContent = `${formatClock(frame.frameOffsetNs)} · ${formatSkew(frame.skewNs)}`;
    });
    const motionRequest = state.cache.motion?.available
      ? window.episodeQc.getMotionFrame({ episodeId: playbackEpisodeId, timeNs }).then((frame) => {
          if (episodeId === state.currentEpisodeId) { state.motionFrame = frame; drawMotion(); }
        })
      : Promise.resolve();
    const requestedSource = state.motionSource;
    const actionSource = state.cache.robot_actions?.sources?.find((item) => item.key === requestedSource && item.available);
    const actionRequest = actionSource
      ? window.episodeQc.getRobotActionFrame({
          episodeId: playbackEpisodeId,
          sourceKey: requestedSource,
          timeNs,
        }).then((frame) => {
          if (episodeId === state.currentEpisodeId && requestedSource === state.motionSource) {
            state.robotActionFrame = frame;
            drawMotion();
          }
        })
      : Promise.resolve();
    await Promise.all([...cameraRequests, motionRequest, actionRequest]);
    if (episodeId === state.currentEpisodeId) {
      setCacheStatus("ready", state.cache.reused ? "播放缓存已复用" : "播放缓存已就绪");
    }
  } catch (error) {
    if (episodeId === state.currentEpisodeId) setCacheStatus("error", `帧读取失败：${error.message || error}`);
  } finally {
    state.visualPending = false;
  }
}

function playbackLoop(now) {
  // requestAnimationFrame's timestamp may move backwards after a renderer
  // lifecycle transition. Never let that transient clock reset rewind the
  // playhead below zero and send an invalid frame request to the main process.
  const elapsedMs = Math.max(0, Math.min(250, now - state.lastTick));
  state.lastTick = now;
  if (state.playing && state.durationNs > 0) {
    state.playheadNs += elapsedMs * 1e6 * state.playbackRate;
    const start = state.selectionStartNs;
    const end = state.selectionEndNs;
    if (els.loopSelection.checked && start !== null && end !== null && end > start && state.playheadNs >= end) {
      state.playheadNs = start;
    } else if (state.playheadNs >= state.durationNs) {
      state.playheadNs = state.durationNs;
      state.playing = false;
      updatePlaybackButton();
      savePlayhead();
    }
    renderClock();
    requestVisualFrames();
  }
  requestAnimationFrame(playbackLoop);
}

async function togglePlayback() {
  if (!state.cache) return;
  if (state.playheadNs >= state.durationNs) state.playheadNs = 0;
  state.playing = !state.playing;
  state.lastTick = performance.now();
  updatePlaybackButton();
  if (state.playing && state.detail?.episode.review_status === "unreviewed") await setReviewStatus("in_progress", false);
  if (!state.playing) savePlayhead();
}

function updatePlaybackButton() {
  els.togglePlay.textContent = state.playing ? "Ⅱ" : "▶";
  els.togglePlay.title = state.playing ? "暂停 (Space)" : "播放 (Space)";
}

function seekTo(timeNs) {
  state.playheadNs = Math.max(0, Math.min(Number(timeNs) || 0, state.durationNs));
  renderClock();
  requestVisualFrames(true);
}

function renderClock() {
  els.currentTime.textContent = formatClock(state.playheadNs);
  els.durationTime.textContent = formatClock(state.durationNs);
  els.timelineRange.value = state.durationNs ? String(Math.round((state.playheadNs / state.durationNs) * 1_000_000)) : "0";
}

function markSelectionStart() {
  state.selectionStartNs = Math.round(state.playheadNs);
  if (state.selectionEndNs !== null && state.selectionEndNs < state.selectionStartNs) state.selectionEndNs = null;
  renderSelection();
}

function markSelectionEnd() {
  if (state.selectionStartNs === null) state.selectionStartNs = 0;
  state.selectionEndNs = Math.max(state.selectionStartNs, Math.round(state.playheadNs));
  renderSelection();
}

function renderSelection() {
  if (state.selectionStartNs === null) els.selectionLabel.textContent = "未选择区间";
  else if (state.selectionEndNs === null) els.selectionLabel.textContent = `${formatClock(state.selectionStartNs)} → 等待终点`;
  else els.selectionLabel.textContent = `${formatClock(state.selectionStartNs)} → ${formatClock(state.selectionEndNs)}`;
}

function renderLabels() {
  const labels = state.labelSchema?.labels || [];
  const groups = new Map((state.labelSchema?.groups || []).map((item) => [item.code, groupDisplayName(item.code, item.name)]));
  const groupCodes = [...new Set(labels.filter((label) => label.enabled !== false).map((label) => label.group).filter(Boolean))];
  const selectedGroup = els.labelGroupFilter.value;
  els.labelGroupFilter.innerHTML = [
    '<option value="all">全部分组</option>',
    ...groupCodes.map((code) => `<option value="${escapeHtml(code)}">${escapeHtml(groups.get(code) || groupDisplayName(code))}</option>`)
  ].join("");
  els.labelGroupFilter.value = groupCodes.includes(selectedGroup) ? selectedGroup : "all";
  const activeGroup = els.labelGroupFilter.value;
  const query = els.labelSearch.value.trim().toLowerCase();
  const enabled = labels.filter((label) => label.enabled !== false);
  const visible = enabled.filter((label) => label.annotation_scopes?.includes(state.scope) && (activeGroup === "all" || label.group === activeGroup) && (!query || `${label.code} ${label.name} ${label.description || ""}`.toLowerCase().includes(query)));
  const currentTarget = currentAnnotationTarget();
  const usable = visible.filter((label) => labelSupportsTarget(label, currentTarget));
  const schemaHeader = state.labelSchema?.schema || {};
  const labelSetName = labelSetDisplayName(schemaHeader.label_set_id, schemaHeader.label_set_name);
  const versionText = schemaHeader.schema_version ? ` · v${schemaHeader.schema_version}` : "";
  els.labelSetMeta.textContent = labelSetName ? `${labelSetName}${versionText}` : "尚未导入标签库";
  els.labelSetMeta.title = els.labelSetMeta.textContent;
  els.labelCount.textContent = `${usable.length} 可用 / ${visible.length}`;
  if (!visible.length) {
    els.labelList.innerHTML = `<div class="empty-panel">${labels.length ? "当前范围没有可用标签" : "请先导入标签库"}</div>`;
    return;
  }
  els.labelList.innerHTML = visible.map((label) => {
    const supported = labelSupportsTarget(label, currentTarget);
    const enabledForEpisode = supported && Boolean(state.detail);
    const targetHint = targetTypesDescription(label.target_types || []);
    const title = supported
      ? `${label.name} · ${groups.get(label.group) || groupDisplayName(label.group)}${label.description ? `\n${label.description}` : ""}`
      : `当前对象“${currentTarget.displayName}”不可用；该标签支持：${targetHint}`;
    return `
    <button class="label-button${enabledForEpisode ? "" : " target-disabled"}" data-label-code="${escapeHtml(label.code)}" style="--label-color:${escapeHtml(label.color || "#8c959f")}" title="${escapeHtml(state.detail ? title : "请先选择 Episode")}" type="button"${enabledForEpisode ? "" : " disabled"}>
      <i class="label-color"></i><span class="label-copy"><strong>${escapeHtml(label.name)}</strong><small>${escapeHtml(supported ? (groups.get(label.group) || label.group) : `仅支持：${targetHint}`)}</small></span>${label.shortcut ? `<kbd>${escapeHtml(label.shortcut)}</kbd>` : ""}
    </button>`;
  }).join("");
}

function renderTargetContext() {
  const active = currentAnnotationTarget();
  const cameras = state.cache?.cameras || [];
  const mocapAvailable = Boolean(state.detail?.episode.mocap_available || state.cache?.motion?.available);
  const joints = state.cache?.motion?.joint_names || [];
  const button = (type, key, label) => `
    <button class="target-option${active.targetType === type && (key === null || active.selectionKey === key) ? " active" : ""}" data-target-type="${type}"${key ? ` data-target-key="${escapeHtml(key)}"` : ""} aria-pressed="${active.targetType === type && (key === null || active.selectionKey === key)}" type="button">${escapeHtml(label)}</button>`;
  els.targetContext.innerHTML = `
    <div class="target-picker-heading"><strong>标注对象</strong><span>当前：${escapeHtml(active.displayName)}</span></div>
    <div class="target-options">
      ${button("global", null, "全局")}
      ${mocapAvailable ? button("mocap", null, "全身动作") : ""}
      ${cameras.map((camera) => button("camera", camera.stream_id, camera.display_name || "相机")).join("")}
      ${joints.length ? `<select class="target-joint-select${active.targetType === "joint" ? " active" : ""}" data-target-joint aria-label="选择标注关节"><option value="">选择关节…</option>${joints.map((joint) => `<option value="${escapeHtml(joint)}"${state.selectedJoint === joint ? " selected" : ""}>${escapeHtml(jointDisplayName(joint))}</option>`).join("")}</select>` : ""}
    </div>`;
}

async function createAnnotation(labelCode) {
  if (!state.detail) return toast("请先选择 Episode", "error");
  const label = state.labelSchema?.labels?.find((item) => item.code === labelCode);
  if (!label) return;
  let start = Math.round(state.playheadNs);
  let end = start;
  if (state.scope === "time_range") {
    if (state.selectionStartNs === null || state.selectionEndNs === null || state.selectionEndNs <= state.selectionStartNs) {
      return toast("请先使用 I / O 设置有效区间", "error");
    }
    start = state.selectionStartNs;
    end = state.selectionEndNs;
  } else if (state.scope === "episode") {
    start = 0;
    end = state.durationNs;
  }
  const target = targetForLabel(label);
  if (!target) {
    const current = currentAnnotationTarget();
    return toast(`“${label.name}”不支持当前对象“${current.displayName}”；请选择：${targetTypesDescription(label.target_types || [])}`, "error", 5500);
  }
  const attributes = collectCustomFields(label, target);
  if (attributes === null) return;
  const payload = {
    episode_id: state.currentEpisodeId, label_code: label.code, scope: state.scope,
    start_offset_ns: start, end_offset_ns: end, target_type: target.targetType, target_key: target.targetKey,
    severity: label.default_severity, action: label.default_action, comment: els.annotationComment.value.trim(),
    attributes, reviewer_name: els.reviewerName.value.trim(), status: "confirmed"
  };
  setSaveState("saving", "保存中…");
  try {
    const saved = await window.episodeQc.saveAnnotation({ payload });
    state.detail.annotations.push(saved);
    state.detail.episode.annotation_count = state.detail.annotations.length;
    updateEpisodeFromDetail();
    renderAnnotations();
    els.annotationComment.value = "";
    setSaveState("saved", "已保存");
  } catch (error) {
    setSaveState("error", "保存失败");
    toast(error.message || String(error), "error", 7000);
  }
}

function collectCustomFields(label, target) {
  const attributes = {};
  for (const field of label.fields || []) {
    let value = null;
    if (field.type === "joint_selector") {
      const joint = state.selectedJoint || (target.targetType === "mocap" ? WHOLE_BODY_JOINT : null);
      if (joint) value = field.multiple ? [joint] : joint;
    }
    else if (field.type === "camera_selector" && target.targetType === "camera") value = field.multiple ? [target.targetKey] : target.targetKey;
    else if (field.type === "stream_selector" && target.targetKey) value = field.multiple ? [target.targetKey] : target.targetKey;
    else if (field.type === "boolean") value = window.confirm(`${field.name || field.code}？`);
    else if (["select", "multi_select"].includes(field.type)) {
      const options = (field.options || []).map((item) => `${item.code}=${item.name}`).join("，");
      const answer = window.prompt(`${field.name || field.code}${options ? `（${options}）` : ""}`, "");
      if (answer !== null && answer.trim()) value = field.type === "multi_select" ? answer.split("|").map((item) => item.trim()).filter(Boolean) : answer.trim();
    } else if (["text", "textarea", "number"].includes(field.type)) {
      const answer = window.prompt(field.name || field.code, "");
      if (answer !== null && answer.trim()) value = field.type === "number" ? Number(answer) : answer;
    }
    if (field.required && (value === null || value === "" || Array.isArray(value) && value.length === 0)) {
      toast(`标签“${label.name}”需要填写：${field.name || field.code}`, "error");
      return null;
    }
    if (value !== null && value !== "") attributes[field.code] = value;
  }
  return attributes;
}

function targetForLabel(label) {
  const target = currentAnnotationTarget();
  return labelSupportsTarget(label, target) ? target : null;
}

function currentAnnotationTarget() {
  return resolveSelectedTarget({
    selectedJoint: state.selectedJoint,
    selectedCameraId: state.selectedCameraId,
    baseTarget: state.selectedBaseTarget,
    cameras: state.cache?.cameras || [],
    jointDisplayName,
  });
}

function selectAnnotationTarget(targetType, targetKey = null) {
  if (targetType === "global" || targetType === "mocap") {
    state.selectedBaseTarget = targetType;
    state.selectedCameraId = null;
    state.selectedJoint = null;
  } else if (targetType === "camera") {
    if (!(state.cache?.cameras || []).some((item) => item.stream_id === targetKey)) return;
    state.selectedCameraId = targetKey;
    state.selectedJoint = null;
  } else if (targetType === "joint") {
    if (!targetKey) return selectAnnotationTarget("mocap");
    state.selectedJoint = targetKey;
    state.selectedCameraId = null;
  } else {
    return;
  }
  syncJointSelectionUi();
  syncCameraSelectionUi();
  renderTargetContext();
  renderLabels();
  drawMotion();
}

function syncCameraSelectionUi() {
  els.cameraGrid.querySelectorAll(".camera-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.cameraId === state.selectedCameraId);
  });
}

function renderAnnotations() {
  const annotations = state.detail?.annotations || [];
  const labels = new Map((state.labelSchema?.labels || []).map((item) => [item.code, item]));
  const severities = new Map((state.labelSchema?.severity_levels || []).map((item) => [item.code, item.name]));
  els.annotationCount.textContent = annotations.length;
  if (!annotations.length) els.annotationList.innerHTML = '<div class="empty-panel">暂无标注</div>';
  else els.annotationList.innerHTML = annotations.map((annotation) => {
    const label = labels.get(annotation.label_code) || { name: annotation.label_code, color: "#8c959f" };
    return `<div class="annotation-item" data-annotation-id="${escapeHtml(annotation.annotation_id)}"><i style="background:${escapeHtml(label.color || "#8c959f")}"></i><span><strong>${escapeHtml(label.name)}</strong><small>${escapeHtml(annotationTargetName(annotation))} · ${escapeHtml(severities.get(annotation.severity) || annotation.severity || "未分级")}</small></span><time>${annotation.scope === "episode" ? "整条" : annotation.scope === "time_point" ? formatClock(annotation.start_offset_ns) : `${formatClock(annotation.start_offset_ns)}–${formatClock(annotation.end_offset_ns)}`}</time></div>`;
  }).join("");
  els.annotationTrack.innerHTML = annotations.map((annotation) => {
    const label = labels.get(annotation.label_code) || {};
    const left = state.durationNs ? (annotation.start_offset_ns / state.durationNs) * 100 : 0;
    const width = annotation.scope === "time_point" ? 0.35 : state.durationNs ? Math.max(.35, ((annotation.end_offset_ns - annotation.start_offset_ns) / state.durationNs) * 100) : .35;
    return `<i class="annotation-block" data-annotation-id="${escapeHtml(annotation.annotation_id)}" title="${escapeHtml(label.name || annotation.label_code)}" style="left:${left}%;width:${width}%;background:${escapeHtml(label.color || "#8c959f")}"></i>`;
  }).join("");
}

function openAnnotationEditor(annotationId) {
  const annotation = state.detail?.annotations?.find((item) => item.annotation_id === annotationId);
  if (!annotation) return;
  els.editId.value = annotationId;
  els.editStart.value = (annotation.start_offset_ns / 1e9).toFixed(3);
  els.editEnd.value = (annotation.end_offset_ns / 1e9).toFixed(3);
  els.editStart.disabled = annotation.scope === "episode";
  els.editEnd.disabled = annotation.scope === "episode";
  fillSelect(els.editSeverity, state.labelSchema?.severity_levels || [], annotation.severity);
  fillSelect(els.editAction, state.labelSchema?.actions || [], annotation.action);
  els.editComment.value = annotation.comment || "";
  seekTo(annotation.start_offset_ns);
  els.annotationEditor.showModal();
}

function fillSelect(element, choices, selected) {
  element.innerHTML = choices.map((item) => `<option value="${escapeHtml(item.code)}" ${item.code === selected ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
}

async function saveAnnotationEdit() {
  const annotation = state.detail?.annotations?.find((item) => item.annotation_id === els.editId.value);
  if (!annotation) return;
  const payload = {
    ...annotation,
    episode_id: annotation.episode_id,
    start_offset_ns: Math.round(Number(els.editStart.value) * 1e9),
    end_offset_ns: Math.round(Number(els.editEnd.value) * 1e9),
    severity: els.editSeverity.value,
    action: els.editAction.value,
    comment: els.editComment.value,
    reviewer_name: els.reviewerName.value.trim()
  };
  setSaveState("saving", "保存中…");
  try {
    const saved = await window.episodeQc.saveAnnotation({ annotationId: annotation.annotation_id, payload });
    state.detail.annotations = state.detail.annotations.map((item) => item.annotation_id === saved.annotation_id ? saved : item);
    renderAnnotations();
    els.annotationEditor.close();
    setSaveState("saved", "已保存");
  } catch (error) {
    setSaveState("error", "保存失败");
    toast(error.message || String(error), "error");
  }
}

async function deleteCurrentAnnotation() {
  const annotationId = els.editId.value;
  if (!annotationId) return;
  setSaveState("saving", "删除中…");
  try {
    await window.episodeQc.deleteAnnotation(annotationId);
    state.detail.annotations = state.detail.annotations.filter((item) => item.annotation_id !== annotationId);
    state.detail.episode.annotation_count = state.detail.annotations.length;
    updateEpisodeFromDetail();
    renderAnnotations();
    els.annotationEditor.close();
    setSaveState("saved", "已保存");
  } catch (error) {
    setSaveState("error", "删除失败");
    toast(error.message || String(error), "error");
  }
}

async function undo() {
  try {
    const result = await window.episodeQc.undo();
    if (!result) return toast("没有可撤销的操作");
    await reloadCurrentEpisode();
    setSaveState("saved", "已撤销");
  } catch (error) { toast(error.message || String(error), "error"); }
}

async function redo() {
  try {
    const result = await window.episodeQc.redo();
    if (!result) return toast("没有可恢复的操作");
    await reloadCurrentEpisode();
    setSaveState("saved", "已恢复");
  } catch (error) { toast(error.message || String(error), "error"); }
}

async function setDecision(decision) {
  if (!state.currentEpisodeId) return;
  setSaveState("saving", "保存结论…");
  try {
    const episode = await window.episodeQc.updateReview({ episodeId: state.currentEpisodeId, status: "completed", decision, reviewer: els.reviewerName.value.trim(), playheadNs: state.playheadNs });
    state.detail.episode = { ...state.detail.episode, ...episode };
    updateEpisodeFromDetail();
    renderDecision();
    setSaveState("saved", "已保存");
  } catch (error) { setSaveState("error", "保存失败"); toast(error.message || String(error), "error"); }
}

async function setReviewStatus(status, showToast = true) {
  if (!state.currentEpisodeId) return;
  try {
    const episode = await window.episodeQc.updateReview({ episodeId: state.currentEpisodeId, status, reviewer: els.reviewerName.value.trim(), playheadNs: state.playheadNs });
    state.detail.episode = { ...state.detail.episode, ...episode };
    updateEpisodeFromDetail();
    renderDecision();
    if (showToast) toast(status === "needs_recheck" ? "已标记为待复核" : "质检状态已保存", "success");
  } catch (error) { toast(error.message || String(error), "error"); }
}

function renderDecision() {
  const episode = state.detail?.episode;
  const decision = episode?.quality_decision || "";
  els.decisionGrid.querySelectorAll("[data-decision]").forEach((button) => {
    button.classList.toggle("active", button.dataset.decision === decision);
    button.disabled = !episode;
  });
  els.decisionCurrent.textContent = decision ? decisionName(decision) : "未选择";
  els.decisionCurrent.classList.toggle("selected", Boolean(decision));
  els.needsRecheck.classList.toggle("active", episode?.review_status === "needs_recheck");
  els.needsRecheck.disabled = !episode;
}

function updateEpisodeFromDetail() {
  const index = state.episodes.findIndex((item) => item.id === state.currentEpisodeId);
  if (index >= 0) state.episodes[index] = { ...state.episodes[index], ...state.detail.episode };
  renderEpisodeList();
  refreshTaskSummaries();
}

async function refreshTaskSummaries() {
  try {
    const payload = await window.episodeQc.getTasks();
    state.tasks = payload.tasks || [];
    state.currentTask = state.tasks.find((item) => item.id === state.currentTaskId) || state.currentTask;
    renderTaskContext();
  } catch { /* Episode 已保存，任务摘要稍后刷新即可 */ }
}

async function savePlayhead() {
  if (!state.currentEpisodeId) return;
  try {
    await window.episodeQc.updateReview({ episodeId: state.currentEpisodeId, playheadNs: Math.round(state.playheadNs), reviewer: els.reviewerName.value.trim() });
  } catch { /* best-effort window close / switch save */ }
}

function moveEpisode(direction) {
  if (!state.filteredEpisodes.length) return;
  let index = state.filteredEpisodes.findIndex((item) => item.id === state.currentEpisodeId);
  if (index < 0) index = direction > 0 ? -1 : 0;
  const next = state.filteredEpisodes[index + direction];
  if (next) openEpisode(next.id);
}

function drawMotion() {
  const frame = state.motionFrame;
  const actionFrame = state.robotActionFrame;
  if ((frame?.positions?.length || actionFrame?.jointPositions?.length) && g1Viewer.status === "loading") {
    els.motionEmpty.hidden = false;
    els.motionEmpty.textContent = "正在载入官方 G1 29DOF 模型…";
  }
  state.projectedJoints = g1Viewer.render(frame, {
    episodeId: state.currentEpisodeId,
    cameraYaw: state.cameraYaw,
    cameraPitch: state.cameraPitch,
    cameraZoom: state.cameraZoom,
    selectedJoint: state.selectedJoint,
    robotAction: actionFrame,
    motionSource: state.motionSource,
  });
  els.jointLabelLayer.replaceChildren();
  if (els.jointLabels.checked) {
    for (const point of state.projectedJoints) {
      const label = document.createElement("button");
      label.type = "button";
      label.dataset.jointName = point.name;
      label.textContent = jointDisplayName(point.name);
      label.title = `选择 ${jointDisplayName(point.name)} (${point.name})`;
      label.classList.toggle("active", point.name === state.selectedJoint);
      label.style.left = `${point.x}px`;
      label.style.top = `${point.y}px`;
      els.jointLabelLayer.append(label);
    }
  }
}

function robotLimbWidth(name) {
  const value = String(name || "").toLowerCase();
  if (value.includes("finger") || value.includes("toe")) return 3.5;
  if (value.includes("hand") || value.includes("foot") || value.includes("neck")) return 5.5;
  if (value.includes("spine") || value.includes("chest") || value.includes("hip")) return 9;
  return 7;
}

function drawRobotLimb(context, point, parent, width, depth) {
  const gradient = context.createLinearGradient(parent.x, parent.y, point.x, point.y);
  const shade = Math.max(0, Math.min(1, .48 + depth * .12));
  gradient.addColorStop(0, shade > .5 ? "#c9d0d3" : "#818b91");
  gradient.addColorStop(.48, "#8d989e");
  gradient.addColorStop(1, "#4e5960");
  context.save();
  context.lineCap = "round";
  context.beginPath(); context.moveTo(parent.x, parent.y); context.lineTo(point.x, point.y);
  context.lineWidth = width + 4; context.strokeStyle = "rgba(5,8,10,.92)"; context.stroke();
  context.beginPath(); context.moveTo(parent.x, parent.y); context.lineTo(point.x, point.y);
  context.lineWidth = width; context.strokeStyle = gradient; context.stroke();
  context.beginPath(); context.moveTo(parent.x, parent.y); context.lineTo(point.x, point.y);
  context.lineWidth = 1; context.strokeStyle = "rgba(238,245,247,.38)"; context.stroke();
  context.restore();
}

function normalizeJointName(name) { return String(name || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }

function robotJointLookup(points, validity) {
  const lookup = new Map();
  points.forEach((point, index) => { if (validity?.[index]) lookup.set(normalizeJointName(point.name), point); });
  return (...names) => names.map((name) => lookup.get(normalizeJointName(name))).find(Boolean);
}

function drawRobotTorso(context, joint) {
  const leftShoulder = joint("LeftShoulder", "LeftArm"), rightShoulder = joint("RightShoulder", "RightArm");
  const leftHip = joint("LeftUpLeg", "LeftHip"), rightHip = joint("RightUpLeg", "RightHip");
  const chest = joint("Chest", "Spine2", "Spine1"), hips = joint("Hips", "Pelvis");
  if (leftShoulder && rightShoulder && leftHip && rightHip) {
    const panel = [leftShoulder, rightShoulder, rightHip, leftHip];
    const top = Math.min(...panel.map((point) => point.y)), bottom = Math.max(...panel.map((point) => point.y));
    const fill = context.createLinearGradient(0, top, 0, bottom || top + 1);
    fill.addColorStop(0, "#d7dcde"); fill.addColorStop(.42, "#7d898f"); fill.addColorStop(1, "#3b454b");
    context.save();
    context.beginPath();
    context.moveTo(leftShoulder.x, leftShoulder.y);
    context.lineTo(rightShoulder.x, rightShoulder.y);
    context.lineTo(rightHip.x, rightHip.y);
    context.lineTo(leftHip.x, leftHip.y);
    context.closePath();
    context.lineJoin = "round"; context.lineWidth = 4; context.strokeStyle = "#090d10"; context.stroke();
    context.fillStyle = fill; context.fill();
    context.globalAlpha = .72;
    context.beginPath();
    context.moveTo(leftShoulder.x * .72 + rightShoulder.x * .28, leftShoulder.y * .72 + rightShoulder.y * .28);
    context.lineTo(rightShoulder.x * .72 + leftShoulder.x * .28, rightShoulder.y * .72 + leftShoulder.y * .28);
    context.lineTo(rightHip.x * .72 + leftHip.x * .28, rightHip.y * .72 + leftHip.y * .28);
    context.lineTo(leftHip.x * .72 + rightHip.x * .28, leftHip.y * .72 + rightHip.y * .28);
    context.closePath(); context.fillStyle = "#354148"; context.fill();
    const mark = chest || {
      x: panel.reduce((sum, point) => sum + point.x, 0) / panel.length,
      y: panel.reduce((sum, point) => sum + point.y, 0) / panel.length,
    };
    context.globalAlpha = 1; context.textAlign = "center"; context.textBaseline = "middle";
    context.font = "800 10px system-ui"; context.fillStyle = "#d7f356"; context.fillText("G1", mark.x, mark.y);
    context.restore();
  }
  if (leftHip && rightHip) {
    drawRobotLimb(context, leftHip, rightHip, 10, 0);
    const middle = hips || { x: (leftHip.x + rightHip.x) / 2, y: (leftHip.y + rightHip.y) / 2 };
    context.save(); context.beginPath(); context.arc(middle.x, middle.y, 5.2, 0, Math.PI * 2);
    context.fillStyle = "#1b2227"; context.fill(); context.lineWidth = 1.5; context.strokeStyle = "#aeb8bc"; context.stroke(); context.restore();
  }
}

function drawRobotHead(context, joint) {
  const head = joint("Head"), neck = joint("Neck", "Neck1");
  if (!head) return;
  const distance = neck ? Math.hypot(head.x - neck.x, head.y - neck.y) : 18;
  const radius = Math.max(9, Math.min(17, distance * .62));
  const shell = context.createLinearGradient(head.x - radius, head.y - radius, head.x + radius, head.y + radius);
  shell.addColorStop(0, "#e2e6e7"); shell.addColorStop(.52, "#8e999e"); shell.addColorStop(1, "#3c474d");
  context.save();
  context.beginPath(); context.ellipse(head.x, head.y, radius, radius * .78, 0, 0, Math.PI * 2);
  context.fillStyle = shell; context.fill(); context.lineWidth = 3; context.strokeStyle = "#080c0f"; context.stroke();
  context.beginPath(); context.ellipse(head.x, head.y - radius * .04, radius * .68, radius * .43, 0, 0, Math.PI * 2);
  context.fillStyle = "#10161a"; context.fill();
  context.beginPath(); context.moveTo(head.x - radius * .35, head.y - radius * .06); context.lineTo(head.x + radius * .35, head.y - radius * .06);
  context.lineWidth = 1.2; context.strokeStyle = "rgba(215,243,86,.72)"; context.stroke();
  context.restore();
}

function drawRobotJoint(context, point, selected) {
  const small = /finger|toe/i.test(point.name);
  const radius = small ? 2.2 : 4.1;
  context.save();
  if (selected) {
    context.beginPath(); context.arc(point.x, point.y, radius + 4, 0, Math.PI * 2);
    context.lineWidth = 2; context.strokeStyle = "#d7f356"; context.stroke();
  }
  context.beginPath(); context.arc(point.x, point.y, radius + 1.5, 0, Math.PI * 2);
  context.fillStyle = "#0b1013"; context.fill();
  context.beginPath(); context.arc(point.x, point.y, radius, 0, Math.PI * 2);
  context.fillStyle = selected ? "#ffffff" : "#778289"; context.fill();
  context.beginPath(); context.arc(point.x - radius * .24, point.y - radius * .27, Math.max(1, radius * .28), 0, Math.PI * 2);
  context.fillStyle = selected ? "#d7f356" : "rgba(235,241,243,.72)"; context.fill();
  context.restore();
}

function drawGroundGrid(context, width, height) {
  const horizon = height * .73;
  const platform = context.createRadialGradient(width / 2, horizon + 8, 0, width / 2, horizon + 8, width * .32);
  platform.addColorStop(0, "rgba(215,243,86,.10)"); platform.addColorStop(1, "rgba(215,243,86,0)");
  context.save(); context.scale(1, .24); context.beginPath(); context.arc(width / 2, (horizon + 8) / .24, width * .32, 0, Math.PI * 2);
  context.fillStyle = platform; context.fill(); context.restore();
  context.strokeStyle = "rgba(113,126,134,.13)";
  context.lineWidth = 1;
  for (let i = -6; i <= 6; i++) {
    context.beginPath(); context.moveTo(width / 2 + i * 17, horizon); context.lineTo(width / 2 + i * 42, height); context.stroke();
  }
  for (let i = 0; i < 6; i++) {
    const y = horizon + (height - horizon) * (i / 6) ** 1.65;
    context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke();
  }
}

function motionPointerDown(event) { state.drag = { x: event.clientX, y: event.clientY, moved: false }; els.motionCanvas.setPointerCapture?.(event.pointerId); }
function motionPointerMove(event) {
  if (!state.drag) return;
  const dx = event.clientX - state.drag.x, dy = event.clientY - state.drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 2) state.drag.moved = true;
  state.cameraYaw += dx * .008; state.cameraPitch = Math.max(-1.1, Math.min(1.1, state.cameraPitch + dy * .006));
  state.drag.x = event.clientX; state.drag.y = event.clientY; drawMotion();
}
function motionPointerUp() { setTimeout(() => { state.drag = null; }, 0); }
function motionWheel(event) { event.preventDefault(); state.cameraZoom = Math.max(.35, Math.min(3, state.cameraZoom * Math.exp(-event.deltaY * .001))); drawMotion(); }
function resetMotionView() { state.cameraYaw = -.15; state.cameraPitch = .12; state.cameraZoom = 1; drawMotion(); }
function renderJointOptions(names) {
  const available = [...new Set((names || []).filter(Boolean))];
  const signature = available.join("|");
  if (els.jointSelector.dataset.signature !== signature) {
    const options = [new Option("全身（默认）", "")];
    for (const name of available) options.push(new Option(`${jointDisplayName(name)} · ${name}`, name));
    els.jointSelector.replaceChildren(...options);
    els.jointSelector.dataset.signature = signature;
  }
  if (state.selectedJoint && !available.includes(state.selectedJoint)) state.selectedJoint = null;
  syncJointSelectionUi();
}
function syncJointSelectionUi() {
  els.jointSelector.value = state.selectedJoint || "";
  els.selectedJoint.hidden = !state.selectedJoint;
  els.selectedJoint.textContent = state.selectedJoint ? jointDisplayName(state.selectedJoint) : "";
}
function selectJoint(name) {
  selectAnnotationTarget(name ? "joint" : (state.detail?.episode.mocap_available ? "mocap" : "global"), name || null);
}
function selectJointAtPointer(event) {
  if (state.drag?.moved || !state.projectedJoints.length) return;
  const rect = els.motionCanvas.getBoundingClientRect();
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  let closest = null, distance = 28;
  for (const point of state.projectedJoints) {
    const value = Math.hypot(point.x - x, point.y - y);
    if (value < distance) { distance = value; closest = point; }
  }
  if (closest) selectJoint(state.selectedJoint === closest.name ? null : closest.name);
}

function handleKeyboard(event) {
  if (event.key === "Escape" && els.toolMenu.open) {
    els.toolMenu.open = false;
    return;
  }
  const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName) || els.annotationEditor.open;
  if (editing && !(event.ctrlKey && event.key.toLowerCase() === "z")) return;
  if (event.ctrlKey && event.key.toLowerCase() === "z") {
    const action = event.shiftKey ? els.redo : els.undo;
    if (!action.disabled) { event.preventDefault(); event.shiftKey ? redo() : undo(); }
    return;
  }
  if (event.altKey && /^[1-6]$/.test(event.key)) {
    const decision = ["pass", "pass_with_labels", "trim", "repair", "recollect", "reject"][Number(event.key) - 1];
    const button = els.decisionGrid.querySelector(`[data-decision="${decision}"]`);
    if (!button?.disabled) { event.preventDefault(); setDecision(decision); }
    return;
  }
  if (event.code === "Space") { if (!els.togglePlay.disabled) { event.preventDefault(); togglePlayback(); } return; }
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    if (!els.timelineRange.disabled) {
      event.preventDefault();
      seekTo(state.playheadNs + (event.key === "ArrowRight" ? 1 : -1) * (event.shiftKey ? 1e9 : 1e9 / 30));
    }
    return;
  }
  const key = event.key.toUpperCase();
  if (key === "I" && !els.markIn.disabled) return markSelectionStart();
  if (key === "O" && !els.markOut.disabled) return markSelectionEnd();
  if (key === "N" && !els.nextEpisode.disabled) return moveEpisode(1);
  if (key === "P" && !els.previousEpisode.disabled) return moveEpisode(-1);
  if (key === "F") {
    const streamId = state.selectedCameraId || state.cache?.cameras?.[0]?.stream_id;
    if (streamId) toggleCameraFullscreen(streamId);
    return;
  }
  const label = state.labelSchema?.labels?.find((item) => item.enabled !== false && item.shortcut?.toUpperCase() === key && item.annotation_scopes?.includes(state.scope));
  if (label) { event.preventDefault(); createAnnotation(label.code); }
}

function beginTimelineSelection(event) {
  if (event.target.closest("[data-annotation-id]") || !state.durationNs) return;
  state.timelineSelecting = true;
  state.timelineAnchorNs = Math.round(timelineTimeFromPointer(event));
  state.selectionStartNs = state.timelineAnchorNs;
  state.selectionEndNs = state.selectionStartNs;
  els.annotationTrack.setPointerCapture?.(event.pointerId);
  renderSelection();
}

function updateTimelineSelection(event) {
  if (!state.timelineSelecting) return;
  const time = Math.round(timelineTimeFromPointer(event));
  state.selectionStartNs = Math.min(state.timelineAnchorNs, time);
  state.selectionEndNs = Math.max(state.timelineAnchorNs, time);
  renderSelection();
}

function endTimelineSelection() {
  if (!state.timelineSelecting) return;
  state.timelineSelecting = false;
  state.timelineAnchorNs = null;
  if (state.selectionEndNs === state.selectionStartNs) state.selectionEndNs = Math.min(state.durationNs, state.selectionStartNs + Math.round(1e9 / 30));
  renderSelection();
}

function timelineTimeFromPointer(event) {
  const rect = els.annotationTrack.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
  return ratio * state.durationNs;
}

function scheduleReviewerSave() {
  clearTimeout(state.reviewerTimer);
  setSaveState("saving", "保存中…");
  state.reviewerTimer = setTimeout(async () => {
    try {
      state.workspace = await window.episodeQc.updateWorkspaceSettings({ reviewer: els.reviewerName.value.trim() });
      setSaveState("saved", "已保存");
    } catch (error) { setSaveState("error", "保存失败"); toast(error.message || String(error), "error"); }
  }, 550);
}

function reviewStatusName(status) {
  return ({
    unreviewed: "未开始",
    in_progress: "质检中",
    completed: "已完成",
    reviewed: "已完成",
    needs_recheck: "待复核"
  })[status] || status || "未开始";
}

function decisionName(decision) {
  return ({
    pass: "通过",
    pass_with_labels: "有条件通过",
    trim: "需裁剪",
    repair: "需修复",
    recollect: "需重采",
    reject: "废弃"
  })[decision] || decision || "";
}

function groupDisplayName(code, configuredName = "") {
  if (configuredName && configuredName !== code) return configuredName;
  return ({
    episode: "整体问题",
    mocap: "Mocap 问题",
    camera: "相机问题",
    teleoperation: "遥操作问题",
    collection: "采集过程问题",
    task_execution: "任务执行",
    clothes_handling: "衣物处理",
    motion_safety: "动作与安全"
  })[code] || configuredName || code || "其他";
}

function labelSetDisplayName(id, configuredName = "") {
  if (id === "washing_machine_task_qc_v1") return "洗衣机任务质检标签";
  return configuredName || id || "";
}

function jointDisplayName(name) {
  return ({
    Hips: "骨盆",
    LeftUpLeg: "左髋",
    LeftLeg: "左膝",
    LeftFoot: "左踝",
    LeftToe: "左脚尖",
    RightUpLeg: "右髋",
    RightLeg: "右膝",
    RightFoot: "右踝",
    RightToe: "右脚尖",
    Spine1: "腰部",
    Spine2: "胸腰",
    Chest: "胸部",
    Neck: "颈部",
    Head: "头部",
    LeftShoulder: "左锁骨",
    LeftArm: "左肩",
    LeftForeArm: "左肘",
    LeftHand: "左腕",
    RightShoulder: "右锁骨",
    RightArm: "右肩",
    RightForeArm: "右肘",
    RightHand: "右腕",
  })[name] || name || "未命名关节";
}

function annotationTargetName(annotation) {
  const target = annotation.target_type;
  if (target === "global") return "全局";
  if (target === "joint") return `关节 · ${jointDisplayName(annotation.target_key)}${annotation.target_key ? ` (${annotation.target_key})` : ""}`;
  if (target === "camera") {
    const camera = state.cache?.cameras?.find((item) => item.topic === annotation.target_key || item.stream_id === annotation.target_key);
    return `相机 · ${camera?.display_name || annotation.target_key || "未指定"}`;
  }
  return ({ mocap: "Mocap", stream: "数据流", retarget: "重定向", robot: "机器人", hand: "灵巧手" })[target] || annotation.target_key || target || "未指定";
}

function setCacheStatus(kind, text) { els.cacheStatus.className = `cache-status ${kind}`; els.cacheStatus.textContent = text; }
function setSaveState(kind, text) { els.saveState.className = `save-state ${kind === "saved" ? "" : kind}`; els.saveState.innerHTML = `<span></span>${escapeHtml(text)}`; }
function setBusyButton(button, busy, text) { button.disabled = busy; button.textContent = text; }
function toast(message, kind = "", timeout = 3500) {
  const item = document.createElement("div"); item.className = `toast ${kind}`; item.textContent = message; els.toastStack.appendChild(item);
  setTimeout(() => item.remove(), timeout);
}
function formatClock(ns) {
  const milliseconds = Math.max(0, Number(ns) || 0) / 1e6;
  const minutes = Math.floor(milliseconds / 60000); const seconds = Math.floor(milliseconds / 1000) % 60; const millis = Math.floor(milliseconds % 1000);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}
function formatDuration(seconds) { const value = Math.max(0, Number(seconds) || 0); return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`; }
function formatBytes(bytes) {
  let value = Math.max(0, Number(bytes) || 0);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}
function taskStatusName(status) {
  return ({
    importing: "正在导入",
    caching: "正在缓存",
    ready: "待质检",
    in_progress: "质检中",
    completed: "已完成",
    submitted: "已提交",
    archived: "已归档",
    failed: "导入失败",
  })[status] || status || "未知状态";
}
function flowJobStatusName(status) {
  return ({
    waiting_data: "等待数据",
    pending: "待领取",
    claimed: "已领取",
    caching: "缓存中",
    cache_ready: "缓存就绪",
    in_progress: "质检中",
    completed: "已完成",
    failed: "异常",
  })[status] || status || "未知状态";
}
function formatSkew(ns) { const ms = Number(ns) / 1e6; return `${ms >= 0 ? "+" : ""}${ms.toFixed(1)} ms`; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]); }

initialize();
