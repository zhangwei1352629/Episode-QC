import { installAISuggestions } from "./ai-suggestions.mjs";
import { adjacentFrame } from "./annotation-calibration.mjs";
import { contiguousAiSegmentGroup, installIntervalTrack } from "./interval-track.mjs";
import { G1Viewer } from "./g1-viewer.bundle.js";
import { annotationDurationNs, annotationTimeError } from "./annotation-timing.mjs";
import {
  labelSupportsTarget,
  resolveSelectedTarget,
  targetTypesDescription,
} from "./target-selection.mjs";
import {
  flowJobNeedsCacheRecovery,
  flowJobOwnershipLabel,
  flowTaskGroupPresentation,
  groupFlowClaimPoolJobs,
} from "./flow-task-groups.mjs";
import {
  canConfirmEpisode,
  pendingInheritedDecision,
} from "./incremental-review.mjs";
import { loadInitialTasks } from "./startup.mjs";
import {
  beginRangeSelection,
  completeRangeSelection,
  frameGridForCameras,
  framePositionForTime,
  frameRangeForInterval,
  isTimelineDrag,
  singleFrameRange,
  snapTimeToFrame,
} from "./range-selection.mjs";
import {
  createEgoDraft,
  egoDraftForLabel,
  labelUsesEgoSemanticFields,
  previousEpisodeForCurrent,
  reuseSameObjectDraft,
  sameStepNewObjectDraft,
} from "./ego-annotation-draft.mjs";

const $ = (id) => document.getElementById(id);

const els = {
  workspaceName: $("workspace-name"), reviewerName: $("reviewer-name"), importLabels: $("import-labels"),
  headerTaskSummary: $("header-task-summary"), headerLabelVersion: $("header-label-version"),
  headerReviewRound: $("header-review-round"),
  nasStatus: $("nas-status"),
  exportFormat: $("export-format"), exportResults: $("export-results"), addSource: $("add-source"), addEgoSource: $("add-ego-source"), saveState: $("save-state"),
  toggleEpisodes: $("toggle-episodes"), toggleLabels: $("toggle-labels"), toolMenu: $("tool-menu"),
  episodeTotal: $("episode-total"), episodeDone: $("episode-done"), episodeErrors: $("episode-errors"),
  episodeSearch: $("episode-search"), statusFilter: $("status-filter"), episodeList: $("episode-list"),
  currentEpisode: $("current-episode"), episodeMeta: $("episode-meta"), previousEpisode: $("previous-episode"),
  nextEpisode: $("next-episode"), togglePlay: $("toggle-play"), playbackRate: $("playback-rate"), enableStreamPreview: $("enable-stream-preview"),
  currentTime: $("current-time"), durationTime: $("duration-time"), framePosition: $("frame-position"), cacheStatus: $("cache-status"),
  motionCard: $("motion-card"), motionCanvas: $("motion-canvas"), motionEmpty: $("motion-empty"), jointLabelLayer: $("joint-label-layer"),
  motionViewerTitle: $("motion-viewer-title"), motionViewerBadge: $("motion-viewer-badge"), motionHint: $("motion-hint"),
  motionControlsToggle: $("motion-controls-toggle"), motionControlPanel: $("motion-control-panel"),
  motionSource: $("motion-source"), jointSelector: $("joint-selector"), jointLabels: $("joint-labels"), resetView: $("reset-view"), selectedJoint: $("selected-joint"),
  cameraGrid: $("camera-grid"), selectionLabel: $("selection-label"), markIn: $("mark-in"), markOut: $("mark-out"),
  loopSelection: $("loop-selection"), coverageTracks: $("coverage-tracks"), annotationTrack: $("annotation-track"),
  timelineViewControls: $("timeline-view-controls"), timelineRange: $("timeline-range"), timelineEnd: $("timeline-end"), scopeTabs: $("scope-tabs"),
  labelSearch: $("label-search"), labelGroupFilter: $("label-group-filter"), labelCount: $("label-count"),
  labelSetMeta: $("label-set-meta"), labelHelp: $("label-help"), targetContext: $("target-context"), labelList: $("label-list"),
  labelSection: $("label-section"), toggleLabelSection: $("toggle-label-section"),
  labelPagination: $("label-pagination"), labelPagePrevious: $("label-page-previous"),
  labelPageNext: $("label-page-next"), labelPageStatus: $("label-page-status"),
  openLabelEditor: $("open-label-editor"), openAnnotationType: $("open-annotation-type"),
  openLabelName: $("open-label-name"), saveOpenLabel: $("save-open-label"),
  annotationComment: $("annotation-comment"), undo: $("undo"), redo: $("redo"),
  egoAnnotationFields: $("ego-annotation-fields"), egoBodyPart: $("ego-body-part"), egoObjectName: $("ego-object-name"),
  egoObjectColor: $("ego-object-color"), egoSourceName: $("ego-source-name"), egoTargetName: $("ego-target-name"),
  egoExceptionType: $("ego-exception-type"), egoRecoveryAction: $("ego-recovery-action"),
  egoStepDraft: $("ego-step-draft"), egoSelectedStep: $("ego-selected-step"),
  egoSemanticField: $("ego-semantic-field"), egoSemanticDescription: $("ego-semantic-description"),
  egoNewObject: $("ego-new-object"), egoReuseObject: $("ego-reuse-object"), saveEgoAnnotation: $("save-ego-annotation"),
  annotationCount: $("annotation-count"), annotationList: $("annotation-list"), decisionGrid: $("decision-grid"), decisionCurrent: $("decision-current"),
  annotationsSection: $("current-annotations-section"),
  toggleCurrentAnnotations: $("toggle-current-annotations"),
  decisionSection: $("decision-section"), toggleDecisionSection: $("toggle-decision-section"),
  needsRecheck: $("needs-recheck"), toastStack: $("toast-stack"), taskCenterToastStack: $("task-center-toast-stack"), annotationEditor: $("annotation-editor"),
  editId: $("edit-id"), editStart: $("edit-start"), editEnd: $("edit-end"), editSeverity: $("edit-severity"),
  editLabelName: $("edit-label-name"), editFrameRange: $("edit-frame-range"), editTimingHelp: $("edit-timing-help"),
  editStartLabel: $("edit-start-label"), editEndLabel: $("edit-end-label"),
  editAction: $("edit-action"), editComment: $("edit-comment"), editProvenance: $("edit-provenance"), deleteAnnotation: $("delete-annotation"),
  editEgoFields: $("edit-ego-fields"), editEgoStepField: $("edit-ego-step-field"), editEgoStep: $("edit-ego-step"),
  editEgoSemanticField: $("edit-ego-semantic-field"),
  editEgoSemanticDescription: $("edit-ego-semantic-description"), editEgoBodyPart: $("edit-ego-body-part"),
  editEgoObjectName: $("edit-ego-object-name"), editEgoObjectColor: $("edit-ego-object-color"),
  editEgoSourceName: $("edit-ego-source-name"), editEgoTargetName: $("edit-ego-target-name"),
  saveEdit: $("save-edit"), currentTaskName: $("current-task-name"), currentTaskCode: $("current-task-code"),
  currentTaskPath: $("current-task-path"), currentTaskStatus: $("current-task-status"),
  openTaskCenter: $("open-task-center"), rescanTask: $("rescan-task"), taskCenter: $("task-center"),
  closeTaskCenter: $("close-task-center"), taskCenterSummary: $("task-center-summary"),
  taskList: $("task-list"), taskCenterImport: $("task-center-import"), taskCenterImportEgo: $("task-center-import-ego"), submitFlowTask: $("submit-flow-task"),
  confirmCurrentEpisode: $("confirm-current-episode"), reviewSummary: $("review-summary"),
  reviewAddedCount: $("review-added-count"), reviewModifiedCount: $("review-modified-count"),
  reviewRemovedCount: $("review-removed-count"), reviewPreservedCount: $("review-preserved-count"),
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
  streamPreview: null,
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
  timelinePointer: null,
  timelineAnchorNs: null,
  timelineSurface: null,
  reviewerTimer: null,
  platform: { enabled: true, connected: false, jobs: [] },
  platformReviewers: [],
  labelSets: [],
  pendingFlowJobCode: null,
  flowPollTimer: null,
  expandedFlowTaskKeys: new Set(),
  timelineView: "effective",
  selectedEgoStepCode: null,
  previousEgoDetail: null,
  previousEgoDetailEpisodeId: null,
  previousEgoDetailTaskId: null,
  labelPage: 1,
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
const LABEL_PAGE_SIZE = 12;

async function initialize() {
  refreshFlowSessionNotice();
  window.setInterval(refreshFlowSessionNotice, 15000);
  restoreWorkspaceLayout();
  bindEvents();
  syncInteractiveState();
  setSaveState("saving", "打开中…");
  try {
    const taskPayload = await loadInitialTasks({
      getTasks: window.episodeQc.getTasks,
      refreshNasStatus,
    });
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
  window.setInterval(() => { refreshNasStatus(); }, 30000);
}

async function refreshNasStatus() {
  try {
    const health = await window.episodeQc.getHealth();
    const nas = health.nas || {};
    const unavailable = nas.configured && nas.available === false;
    els.nasStatus.hidden = !unavailable;
    els.nasStatus.textContent = unavailable ? nas.message : "";
  } catch {
    // The primary workspace request will surface any server connection failure.
  }
}

async function refreshWorkspace({ preserveEpisode = true } = {}) {
  const previousTaskId = state.currentTaskId;
  const payload = await window.episodeQc.getWorkspaceState(state.currentTaskId);
  state.workspace = payload.workspace;
  state.tasks = payload.tasks || [];
  state.currentTask = payload.selected_task || null;
  state.currentTaskId = state.currentTask?.id || null;
  state.episodes = payload.episodes || [];
  state.labelSchema = payload.label_schema;
  if (state.currentTaskId !== previousTaskId) {
    clearEgoDraft();
    state.labelPage = 1;
  }
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

let calibration, calibrationPreview = null;
function bindEvents() {
  const pauseCalibration = () => { state.playing=false;calibrationPreview=null;updatePlaybackButton(); };
  calibration=installIntervalTrack({container:els.annotationTrack,
    getState:()=>({episodeId:state.currentEpisodeId,annotations:state.detail?.annotations||[],time:state.playheadNs,duration:state.durationNs,limit:annotationDurationNs(state.detail?.episode),grid:selectionFrameGrid(),visualReady:!state.visualPending && state.visualReadyTime===Math.round(state.playheadNs)}),
    seek:seekTo,pause:pauseCalibration,
    preview:(start,end)=>{if(!state.cache||end<=start)return;seekTo(start);calibrationPreview={end};state.playing=true;state.lastTick=performance.now();updatePlaybackButton();},
    edit:openAnnotationEditor,notify:message=>toast(message,'error'),
    save:async(original,bounds)=>{
      if(original.episode_id!==state.currentEpisodeId)throw new Error('Episode 已切换，取消修改');
      const current=state.detail?.annotations?.find(a=>a.annotation_id===original.annotation_id);
      if(!current||current.updated_at!==original.updated_at)throw new Error('标注已变化，请重新选中后校准');
      const episodeId=state.currentEpisodeId;
      const saved=await window.episodeQc.saveAnnotation({annotationId:current.annotation_id,payload:{...current,...bounds,episode_id:episodeId}});
      if(state.currentEpisodeId===episodeId){state.detail.annotations=state.detail.annotations.map(a=>a.annotation_id===saved.annotation_id?saved:a);renderAnnotations();}
      return saved;
    },
    saveBoundary:async(left,right,boundaryOffsetNs)=>{
      if(left.episode_id!==state.currentEpisodeId||right.episode_id!==state.currentEpisodeId)throw new Error('Episode 已切换，取消修改');
      const currentLeft=state.detail?.annotations?.find(a=>a.annotation_id===left.annotation_id);
      const currentRight=state.detail?.annotations?.find(a=>a.annotation_id===right.annotation_id);
      if(!currentLeft||!currentRight||currentLeft.updated_at!==left.updated_at||currentRight.updated_at!==right.updated_at)throw new Error('相邻分段已变化，请刷新后重试');
      const episodeId=state.currentEpisodeId;
      const result=await window.episodeQc.moveAiBoundary({
        episodeId,
        leftAnnotationId:left.annotation_id,
        rightAnnotationId:right.annotation_id,
        boundaryOffsetNs,
        leftUpdatedAt:left.updated_at,
        rightUpdatedAt:right.updated_at,
      });
      if(state.currentEpisodeId===episodeId){
        const saved=new Map(result.annotations.map(a=>[a.annotation_id,a]));
        state.detail.annotations=state.detail.annotations.map(a=>saved.get(a.annotation_id)||a);
        renderAnnotations();
      }
      return result;
    }});
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
  els.addSource.addEventListener("click", () => addSource("robot_teleoperation"));
  els.addEgoSource.addEventListener("click", () => addSource("ego_omniego"));
  els.taskCenterImport.addEventListener("click", () => addSource("robot_teleoperation"));
  els.taskCenterImportEgo.addEventListener("click", () => addSource("ego_omniego"));
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
  els.enableStreamPreview.addEventListener("click", enableStreamPreview);
  els.playbackRate.addEventListener("change", () => { state.playbackRate = Number(els.playbackRate.value); });
  els.timelineRange.addEventListener("input", () => {state.playing=false;updatePlaybackButton();const ratio=Number(els.timelineRange.value)/1_000_000;seekTo(calibration?calibration.atRatio(ratio):ratio*state.durationNs);});
  els.markIn.addEventListener("click", markSelectionStart);
  els.markOut.addEventListener("click", markSelectionEnd);
  els.scopeTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-scope]");
    if (!button) return;
    selectAnnotationScope(button.dataset.scope);
  });
  els.labelSearch.addEventListener("input", () => { state.labelPage = 1; renderLabels(); });
  els.labelGroupFilter.addEventListener("change", () => { state.labelPage = 1; renderLabels(); });
  els.labelPagePrevious.addEventListener("click", () => { state.labelPage -= 1; renderLabels(); });
  els.labelPageNext.addEventListener("click", () => { state.labelPage += 1; renderLabels(); });
  els.saveOpenLabel.addEventListener("click", () => createOpenAnnotation());
  els.saveEgoAnnotation.addEventListener("click", saveSelectedEgoAnnotation);
  els.egoNewObject.addEventListener("click", () => applyEgoDraftShortcut("new_object"));
  els.egoReuseObject.addEventListener("click", () => applyEgoDraftShortcut("same_object"));
  els.editEgoStep.addEventListener("change", renderEditEgoFieldState);
  els.openLabelName.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); createOpenAnnotation(); }
  });
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
    const focus = event.target.closest("[data-focus-label]");
    if (focus) {
      focusLabelAnnotations(focus.dataset.focusLabel);
      return;
    }
    const button = event.target.closest("[data-label-code]");
    if (!button) return;
    if (button.dataset.focusOnly === "true") focusLabelAnnotations(button.dataset.labelCode);
    else createAnnotation(button.dataset.labelCode);
  });
  els.annotationList.addEventListener("click", (event) => {
    const item = event.target.closest("[data-annotation-id]");
    if (item) openAnnotationEditor(item.dataset.annotationId);
  });
  els.toggleCurrentAnnotations.addEventListener("click", () => {
    setCurrentAnnotationsExpanded(!els.annotationsSection.classList.contains("expanded"));
  });
  els.toggleLabelSection.addEventListener("click", () => {
    setSidebarSectionExpanded("labels", !els.labelSection.classList.contains("expanded"));
  });
  els.toggleDecisionSection.addEventListener("click", () => {
    setSidebarSectionExpanded("decision", !els.decisionSection.classList.contains("expanded"));
  });
  els.timelineViewControls.addEventListener("click", (event) => {
    const button = event.target.closest("[data-timeline-view]");
    if (!button) return;
    state.timelineView = button.dataset.timelineView;
    els.timelineViewControls.querySelectorAll("button").forEach((item) => {
      item.classList.toggle("active", item === button);
      item.setAttribute("aria-pressed", String(item === button));
    });
    renderAnnotations();
  });
  // Preserve the original manual-annotation gestures. The interval editor
  // intercepts only an existing annotation handle or an AI shared boundary;
  // dragging empty track space still creates a manual I/O range.
  els.annotationTrack.addEventListener("click", (event) => {
    const item = event.target.closest("[data-annotation-id]");
    if (item) calibration.select(item.dataset.annotationId);
  });
  els.annotationTrack.addEventListener("pointerdown", beginTimelineSelection);
  window.addEventListener("pointermove", updateTimelineSelection);
  window.addEventListener("pointerup", endTimelineSelection);
  window.addEventListener("pointercancel", cancelTimelineSelection);
  els.annotationTrack.addEventListener("dblclick", (event) => {
    if (event.target.closest("[data-annotation-id]")) return;
    seekTo(timelineTimeFromPointer(event));
    state.scope = "time_point";
    els.scopeTabs.querySelectorAll("button").forEach((button) => button.classList.toggle("active", button.dataset.scope === state.scope));
    renderLabels();
  });
  els.undo.addEventListener("click", undo);
  els.redo.addEventListener("click", redo);
  els.confirmCurrentEpisode.addEventListener("click", confirmCurrentEpisode);
  els.decisionGrid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-decision]");
    if (button) setDecision(button.dataset.decision);
  });
  els.needsRecheck.addEventListener("click", () => setReviewStatus("needs_recheck"));
  els.resetView.addEventListener("click", resetMotionView);
  els.motionControlsToggle.addEventListener("click", () => {
    setMotionControlsExpanded(els.motionControlsToggle.getAttribute("aria-expanded") !== "true");
  });
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
  setSidebarSectionExpanded("labels", window.localStorage.getItem("episodeQcLabelSectionExpanded") !== "false", false);
  setCurrentAnnotationsExpanded(window.localStorage.getItem("episodeQcCurrentAnnotationsExpanded") !== "false", false);
  setSidebarSectionExpanded("decision", window.localStorage.getItem("episodeQcDecisionSectionExpanded") !== "false", false);
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

function setCurrentAnnotationsExpanded(expanded, persist = true) {
  els.annotationsSection.classList.toggle("expanded", expanded);
  els.toggleCurrentAnnotations.setAttribute("aria-expanded", String(expanded));
  els.toggleCurrentAnnotations.textContent = expanded ? "收起" : "展开";
  if (persist) window.localStorage.setItem("episodeQcCurrentAnnotationsExpanded", String(expanded));
}

function setSidebarSectionExpanded(section, expanded, persist = true) {
  const isLabels = section === "labels";
  const container = isLabels ? els.labelSection : els.decisionSection;
  const button = isLabels ? els.toggleLabelSection : els.toggleDecisionSection;
  container.classList.toggle("expanded", expanded);
  button.setAttribute("aria-expanded", String(expanded));
  button.textContent = expanded ? "收起" : "展开";
  if (persist) {
    window.localStorage.setItem(
      isLabels ? "episodeQcLabelSectionExpanded" : "episodeQcDecisionSectionExpanded",
      String(expanded),
    );
  }
}

function handleWorkerEvent(payload) {
  if (payload?.type === "ai_cache") {
    const row = state.episodes.find(item => item.id === payload.episodeId);
    if (row) { row.ai_cache_state = payload.state; renderEpisodeList(); }
    if (payload.episodeId === state.currentEpisodeId) refreshAI();
    return;
  }
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
  renderViewerProfile();
  renderHeaderContext();
  els.currentTaskName.textContent = task?.task_name || "尚未导入任务";
  els.currentTaskCode.textContent = task?.task_code || "—";
  const taskPath = task?.local_source_path || task?.source_uri || "点击“导入新任务”选择数据目录";
  els.currentTaskPath.textContent = compactSourcePath(taskPath);
  els.currentTaskPath.title = taskPath;
  els.currentTaskStatus.textContent = task
    ? `${taskStatusName(task.status)} · ${task.completed_count}/${task.expected_episode_count ?? task.episode_count}${task.missing_episode_count ? ` · 待缓存/导入 ${task.missing_episode_count} 条` : ""}`
    : "未加载";
  els.currentTaskStatus.dataset.status = task?.status || "empty";
  els.rescanTask.disabled = !task;
  const flowTask = Boolean(task?.origin === "flow" && task?.flow_job_code);
  els.submitFlowTask.hidden = !flowTask;
  els.submitFlowTask.disabled = !task || !["completed", "submitted"].includes(task.status);
  els.submitFlowTask.textContent = task?.status === "submitted"
    ? "已提交到 Flow"
    : task?.status === "completed"
      ? "提交本轮质检到 Flow"
      : "完成全部 Episode 后提交";
  renderReviewFooter();

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
          <small>${escapeHtml(taskKindName(item.task_kind))} · ${escapeHtml(item.task_code)} · ${escapeHtml(taskStatusName(item.status))}${escapeHtml(issue)}</small>
          <span title="${escapeHtml(path)}">${escapeHtml(compactSourcePath(path))}</span>
        </span>
        <span class="task-list-progress"><strong>${item.completed_count}/${item.expected_episode_count ?? item.episode_count}</strong><span>${formatBytes(item.source_size_bytes)} · 异常 ${item.error_count}${item.missing_episode_count ? ` · 待缓存/导入 ${item.missing_episode_count} 条` : ""}</span></span>
      </button>`;
  }).join("");
}

function renderHeaderContext() {
  const task = state.currentTask;
  const episode = state.detail?.episode
    || state.episodes.find((item) => item.id === state.currentEpisodeId)
    || state.episodes[0]
    || null;
  const schema = state.labelSchema?.schema || {};
  const taskCode = task?.flow_job_code || task?.task_code || "—";
  const taskName = task?.task_name || "尚未选择 QC 任务";
  els.headerTaskSummary.textContent = task ? `${taskCode} · ${taskName}` : taskName;
  els.headerTaskSummary.title = task ? `${taskCode} · ${taskName}` : taskName;
  els.headerLabelVersion.textContent = schema.annotation_mode === "open"
    ? `开放标签 · ${schema.annotation_schema_version || "ego_open_v1"}`
    : schema.schema_version
    ? `标签 V${schema.schema_version}`
    : "标签 —";
  els.headerReviewRound.textContent = `当前 R${episode ? currentReviewRound(episode) : 1}`;
}

let platformRefreshInFlight = false;
async function refreshFlowSessionNotice() {
  const notice = $("flow-session-notice");
  if (!notice || !window.episodeQc.getPlatformStatus) return;
  try {
    const status = await window.episodeQc.getPlatformStatus();
    notice.hidden = status.enabled === false || status.logged_in;
    notice.textContent = status.error
      ? "Flow 登录已失效：请在任务中心重新登录。已缓存的数据和 AI 标注可继续离线质检；领取、续传和提交暂不可用。"
      : "尚未登录 Flow：请在任务中心选择质检员登录。已缓存的数据和 AI 标注可离线使用；未缓存的 AI 结果不会自动出现。";
  } catch {
    notice.hidden = false;
    notice.textContent = "暂时无法确认 QC 服务与登录状态，请检查连接；不要将 AI 加载失败视为没有标注。";
  }
}
async function refreshPlatformJobs({ quiet = false } = {}) {
  if (platformRefreshInFlight) return;
  platformRefreshInFlight = true;
  try {
    const payload = await window.episodeQc.getPlatformJobs();
    state.platform = payload;
    refreshFlowSessionNotice();
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
    if (payload.refreshing) startFlowPolling();
    else if (!stillCaching) stopFlowPolling();
  } catch (error) {
    if (!quiet) toast(error.message || String(error), "error", 6500);
    els.flowTaskStatus.textContent = "任务列表刷新失败，请重试（不代表已退出 Flow）";
  } finally {
    platformRefreshInFlight = false;
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
  if (platform.connection_pending) {
    els.flowTaskStatus.textContent = "正在更新任务列表，请稍候";
    return;
  }
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
  const groups = groupFlowClaimPoolJobs(platform.jobs);
  const activeGroupKeys = new Set(groups.map((group) => group.key));
  state.expandedFlowTaskKeys = new Set(
    [...state.expandedFlowTaskKeys].filter((key) => activeGroupKeys.has(key)),
  );
  const claimableBatchCount = groups.reduce((sum, group) => sum + group.claimableBatchCount, 0);
  const recoverableBatchCount = groups.reduce((sum, group) => sum + group.recoverableBatchCount, 0);
  const blockedBatchCount = groups.reduce(
    (sum, group) => sum + group.batchCount - group.claimableBatchCount - group.recoverableBatchCount,
    0,
  );
  els.flowTaskStatus.textContent = `${platform.reviewer || platform.username} · ${groups.length} 个任务 · 可领取 ${claimableBatchCount} · 可恢复 ${recoverableBatchCount} · 不可领取 ${blockedBatchCount}`;
  if (platform.refreshing) els.flowTaskStatus.textContent += " · 更新中（显示上次结果）";
  if (!groups.length) {
    els.flowTaskList.innerHTML = '<div class="empty-panel">当前没有可领取或待排查的 Flow 质检批次</div>';
    return;
  }
  els.flowTaskList.innerHTML = groups.map((group) => {
    const expanded = state.expandedFlowTaskKeys.has(group.key);
    const presentation = flowTaskGroupPresentation(group);
    return `
      <section class="flow-task-group ${presentation.tone} ${expanded ? "expanded" : ""}">
        <button class="flow-task-group-toggle" type="button" data-flow-task-key="${escapeHtml(group.key)}" aria-expanded="${expanded}">
          <span class="flow-task-group-copy">
            <strong>${escapeHtml(group.taskName)}</strong>
            <small>${escapeHtml(group.taskCode || "未设置任务编号")} · ${escapeHtml(presentation.availabilityLabel)}</small>
            <span>${group.episodeCount} Episode · ${formatBytes(group.sizeBytes)}</span>
          </span>
          <span class="flow-task-group-meta"><strong>${escapeHtml(presentation.badgeLabel)}</strong><small>${escapeHtml(presentation.totalLabel)}</small><span aria-hidden="true">⌄</span></span>
        </button>
        <div class="flow-task-batches" ${expanded ? "" : "hidden"}>
          ${group.jobs.map(renderFlowJobItem).join("")}
        </div>
      </section>`;
  }).join("");
}

function renderFlowJobItem(job) {
  const action = flowJobAction(job);
  const progress = (job.local_caching || job.cache_complete === false || ["claimed", "caching", "cache_ready"].includes(job.status))
    ? ` · ${flowJobProgressLabel(job)}`
    : "";
  const blockedReason = job.claimable === false && !job.local_task_id
    ? ` · ${job.claim_blocked_reason || "当前不可领取"}`
    : "";
  const ownership = flowJobOwnershipLabel(job);
  const ownershipLabel = ownership ? ` · ${ownership}` : "";
  const labelMismatch = job.label_snapshot_mismatch
    ? ` · 标签待同步：本地 ${job.local_label_schema_version || "未知"} / Flow ${job.label_schema_version || "未知"}`
    : "";
  return `
    <div class="flow-task-item">
      <div>
        <strong>${escapeHtml(job.asset_id || job.code)}</strong>
        <small>${escapeHtml(job.code)} · ${escapeHtml(flowJobAssetTypeName(job))} · ${escapeHtml(flowJobStatusName(job.status))}${escapeHtml(progress)}${escapeHtml(blockedReason)}${escapeHtml(ownershipLabel)}${escapeHtml(labelMismatch)}</small>
        <span>${escapeHtml(job.collector || "未知采集员")} · ${job.required_episode_count || job.episodes?.length || 0} Episode · ${formatBytes(job.asset_size_bytes)}</span>
      </div>
      <button type="button" data-flow-job-code="${escapeHtml(job.code)}" data-flow-action="${action.name}" ${action.disabled ? "disabled" : ""}>${escapeHtml(action.label)}</button>
    </div>`;
}

function flowJobProgressLabel(job) {
  const local = job.local_progress || {};
  if (local.phase === "verifying" && Number(local.total_files) > 0) {
    return `校验 ${Number(local.verified_files || 0)}/${Number(local.total_files)} 个文件`;
  }
  const cachedEpisodes = Number(local.cached_episode_count ?? job.cached_episode_count ?? 0);
  const totalEpisodes = Number(local.total_episode_count ?? job.total_episode_count ?? 0);
  const progress = Number(job.cache_progress || local.progress || 0);
  if (totalEpisodes > 0) return `原文件 ${cachedEpisodes}/${totalEpisodes} · 可播放 ${Number(job.playback_ready_count || 0)} · 播放准备中 ${Number(job.playback_preparing_count || 0)} · ${progress}%`;
  return `缓存 ${progress}%`;
}

function flowJobAction(job) {
  if (job.superseded) return { name: "none", label: "旧版本已替代", disabled: true };
  if (flowJobNeedsCacheRecovery(job)) {
    if (job.local_caching) return { name: "none", label: flowJobProgressLabel(job), disabled: true };
    return { name: "claim", label: "恢复缓存", disabled: false };
  }
  if (job.local_task_id && job.cache_complete === false && !job.local_caching) {
    return { name: "claim", label: "继续缓存", disabled: false };
  }
  if (job.local_task_id && job.label_snapshot_mismatch) {
    return { name: "open", label: "同步标签并打开", disabled: false };
  }
  if (job.local_task_id) return { name: "open", label: job.local_task_status === "submitted" ? "已提交" : "打开任务", disabled: false };
  if (job.local_caching) return { name: "none", label: flowJobProgressLabel(job), disabled: true };
  if (["claimed", "caching", "cache_ready"].includes(job.status)) return { name: "claim", label: "继续缓存", disabled: false };
  if (job.status === "pending" && job.claimable === false) {
    return { name: "none", label: job.claim_blocked_reason || "暂不可领取", disabled: true };
  }
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
    state.expandedFlowTaskKeys.clear();
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
      ...state.platformReviewers.map((reviewer) => `<option value="${escapeHtml(reviewer.employee_no)}">${escapeHtml(reviewer.display_name)} · ${escapeHtml(reviewer.employee_no)} · ${escapeHtml(reviewer.team_name || "未分组")}</option>`),
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
    state.expandedFlowTaskKeys.clear();
    state.platform = await window.episodeQc.logoutPlatform();
    renderPlatformJobs();
    toast("已退出 Flow");
  } catch (error) { toast(error.message || String(error), "error", 6500); }
}

async function handleFlowTaskAction(event) {
  const groupToggle = event.target.closest("[data-flow-task-key]");
  if (groupToggle) {
    const key = groupToggle.dataset.flowTaskKey;
    if (state.expandedFlowTaskKeys.has(key)) state.expandedFlowTaskKeys.delete(key);
    else state.expandedFlowTaskKeys.add(key);
    renderPlatformJobs();
    return;
  }
  const button = event.target.closest("[data-flow-job-code]");
  if (!button || button.disabled) return;
  const job = state.platform.jobs.find((item) => item.code === button.dataset.flowJobCode);
  if (!job) return;
  if (button.dataset.flowAction === "open" && job.local_task_id) {
    try {
      if (job.label_snapshot_mismatch || !["completed", "submitted", "archived"].includes(job.local_task_status)) {
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
  const recoveringCache = flowJobNeedsCacheRecovery(job);
  setBusyButton(button, true, recoveringCache ? "恢复中…" : "领取中…");
  try {
    const result = await window.episodeQc.claimPlatformJob(job.code);
    state.pendingFlowJobCode = job.code;
    const message = result.cache_recovery
      ? `已备份质检记录，正在从 NAS 恢复 ${job.code} 的缓存`
      : result.accepted
        ? `已领取 ${job.code}，正在后台完整缓存`
        : `${job.code} 已在本机处理`;
    toast(message, "success", 5500);
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
  const deleteCache = window.confirm("提交成功并核验结果后，是否删除此批次的本地数据缓存？\n确定：成功后删除缓存；取消：提交但保留缓存。\n标注、质检记录和 NAS 数据均保留；提交失败不删除缓存。");
  setBusyButton(els.submitFlowTask, true, "提交中…");
  try {
    const response = await window.episodeQc.submitPlatformJob(task.flow_job_code, { deleteCache });
    const cleanup = response?.cache_cleanup;
    const notice = cleanup?.status === "deleted"
      ? "提交成功，本地数据缓存已删除，标注记录已保留"
      : cleanup?.status === "failed"
        ? `提交成功，但缓存未完整删除：${cleanup.error}。可稍后重试清理`
        : "提交成功，本地缓存已保留";
    toast(notice, cleanup?.status === "failed" ? "error" : "success", 8000);
    await Promise.all([refreshWorkspace(), refreshPlatformJobs({ quiet: true })]).catch((error) => {
      toast(`结果已提交，但页面刷新失败，请手动刷新：${error.message || error}`, "error", 8000);
    });
  } catch (error) {
    toast(`提交请求失败：${error.message || String(error)}。提交失败不会删除缓存；如连接中断，结果及清理状态可能尚未返回，请刷新核对。`, "error", 9000);
  } finally {
    renderTaskContext();
  }
}

async function switchTask(taskId) {
  if (!taskId || taskId === state.currentTaskId) {
    els.taskCenter.close();
    return;
  }
  ++state.loadToken;
  window.episodeQc.cancelEpisodeReads?.();
  state.visualGeneration = (state.visualGeneration || 0) + 1;
  state.visualPending = false;
  setSaveState("saving", "切换任务…");
  try {
    await savePlayhead();
    state.playing = false;
    clearEgoDraft();
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

async function addSource(taskKind = "robot_teleoperation") {
  const ego = taskKind === "ego_omniego";
  const sourceButton = ego ? els.addEgoSource : els.addSource;
  const centerButton = ego ? els.taskCenterImportEgo : els.taskCenterImport;
  setBusyButton(sourceButton, true, "导入中…");
  setBusyButton(centerButton, true, "导入中…");
  try {
    const result = await window.episodeQc.addSource(taskKind);
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
    setBusyButton(sourceButton, false, ego ? "Import Ego" : "Import Episode");
    setBusyButton(centerButton, false, ego ? "Import Ego" : "Import Episode");
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
  els.episodeList.innerHTML = state.filteredEpisodes.map((episode) => {
    const previous = episode.previous_review;
    const previousBadge = previous
      ? `<em class="history-label-badge" title="当前为第 ${currentReviewRound(episode)} 轮质检">R${currentReviewRound(episode)}</em>`
      : "";
    return `
      <button class="episode-item ${episode.id === state.currentEpisodeId ? "active" : ""}" data-episode-id="${escapeHtml(episode.id)}" data-status="${escapeHtml(episode.review_status)}" type="button">
        <span class="review-dot"></span>
        <span class="episode-copy">
          <strong>${escapeHtml(episode.episode_name)}</strong>
          <span title="${escapeHtml(episode.relative_path)}">${escapeHtml(episode.relative_path)} · ${escapeHtml(({ ready: "可播放", partial: "主视角可播放", preparing: "播放准备中", failed: "播放准备失败", stale: "需重新准备播放" })[episode.cache_status] || "待准备播放")}${episode.task_origin === 'flow' ? ` · ${escapeHtml(({ready:'AI 已缓存',failed:'AI 缓存异常',stale:'AI 待核验',pending:'AI 待生成'})[episode.ai_cache_state] || 'AI 待缓存')}` : ''}</span>
          <span class="episode-badges"><em class="status-badge">${escapeHtml(episodeReviewStatusName(episode))}</em>${episode.quality_decision ? `<em class="decision-badge">${escapeHtml(decisionName(episode.quality_decision))}</em>` : ""}<em>${episode.camera_count} CAM</em><em>${episode.mocap_available ? "MOCAP" : "无 MOCAP"}</em><em>${episode.annotation_count} 有效标注</em>${previousBadge}</span>
        </span>
        <time>${formatDuration(episode.duration_sec || 0)}</time>
      </button>`;
  }).join("");
  syncInteractiveState();
}

let renderAI;
function refreshAI() {
  if (!renderAI) renderAI=installAISuggestions({container:els.annotationList,api:window.episodeQc,episodeId:()=>state.currentEpisodeId,seek:seekTo,reload:reloadCurrentEpisode,applyDetail:(eid,detail)=>{if(eid===state.currentEpisodeId){state.detail=detail;renderEpisodeDetail();}},notify:(s)=>toast(s,"error")});
  return renderAI();
}
let nextEpisodeWarmup = null;
function scheduleNextEpisodeWarmup(token) {
  clearTimeout(nextEpisodeWarmup);
  nextEpisodeWarmup = setTimeout(() => {
    if (token !== state.loadToken || state.playing) return;
    const index = state.episodes.findIndex((e) => e.id === state.currentEpisodeId);
    const next = index >= 0 ? state.episodes[index + 1] : null;
    // Warm SQLite pages and immutable schema only. Never display cached
    // annotations: another reviewer or AI import may have updated them.
    if (next) window.episodeQc.getEpisode(next.id, { background: true }).catch(() => {});
  }, 1500);
}
async function openEpisode(episodeId) {
  if (!episodeId || episodeId === state.currentEpisodeId && state.cache) return;
  const token = ++state.loadToken;
  clearTimeout(nextEpisodeWarmup);
  const started = performance.now();
  const timing = { episodeId };
  const stamp = (stage) => { timing[stage] = Math.round(performance.now() - started); };
  window.episodeQc.cancelEpisodeReads?.();
  state.visualGeneration = (state.visualGeneration || 0) + 1;
  state.visualPending = false;
  if (state.currentEpisodeId) void savePlayhead();
  if (token !== state.loadToken) return;
  state.playing = false;
  state.streamPreview = null;
  els.enableStreamPreview.textContent = "流预览";
  state.cache = null;
  state.detail = null;
  state.playbackEpisodeId = null;
  state.currentEpisodeId = episodeId;
  state.visualReadyTime=null;
  calibrationPreview=null;
  calibration?.render();
  state.previousEgoDetail = null;
  state.previousEgoDetailEpisodeId = null;
  state.previousEgoDetailTaskId = null;
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
    // Attach both rejection handlers immediately; a cancelled prepare must
    // never cause an unhandled rejection while detail is still loading.
    const prepared = window.episodeQc.prepareEpisode(episodeId).then(
      (value) => { stamp("playbackReadyMs"); return { value }; }, (error) => ({ error }),
    );
    const detail = await window.episodeQc.getEpisode(episodeId);
    stamp("detailMs");
    if (token !== state.loadToken) return;
    state.detail = detail;
    state.selectedBaseTarget = detail.episode.mocap_available ? "mocap" : "global";
    state.labelSchema = detail.label_schema || state.labelSchema;
    state.durationNs = Number(detail.episode.duration_ns || 0);
    state.playheadNs = Math.min(Number(detail.episode.last_playhead_ns || 0), state.durationNs);
    state.playbackEpisodeId = episodeId;
    renderEpisodeDetail();
    Promise.resolve(refreshAI()).then(() => stamp("aiMs")).catch(() => {});
    setCacheStatus("busy", "首次打开：正在建立只读播放缓存…");
    const outcome = await prepared;
    if (outcome.error) throw outcome.error;
    const cache = outcome.value;
    if (token !== state.loadToken) return;
    state.cache = cache;
    syncInteractiveState();
    renderCameras();
    renderMotionAvailability();
    renderClock();
    renderSelection();
    renderAnnotations();
    const cacheMessage = cache.complete
      ? (cache.reused ? "完整播放缓存已复用" : "完整播放缓存已就绪")
      : "默认相机与 Policy 已就绪 · 其余流后台缓存中…";
    setCacheStatus("ready", `${cacheMessage}${cache.decode_errors?.length ? ` · ${cache.decode_errors.length} 个解析提示` : ""}`);
    await requestVisualFrames(true);
    if (token !== state.loadToken) return;
    stamp("visualRequestsMs");
    requestAnimationFrame(() => { if (token === state.loadToken) stamp("paintOpportunityMs"); });
    state.switchTimings = [...(state.switchTimings || []), timing].slice(-20);
    console.info("Episode switch timing", timing);
    scheduleNextEpisodeWarmup(token);
  } catch (error) {
    if (token !== state.loadToken) return;
    setCacheStatus("error", "载入失败");
    toast(error.message || String(error), "error", 8000);
  }
}

async function reloadCurrentEpisode() {
  if (!state.currentEpisodeId) return;
  const episodeId = state.currentEpisodeId, token = state.loadToken;
  const detail = await window.episodeQc.getEpisode(episodeId);
  if (episodeId !== state.currentEpisodeId || token !== state.loadToken) return;
  state.detail = detail;
  state.labelSchema = detail.label_schema || state.labelSchema;
  renderEpisodeDetail();
}

function renderEpisodeDetail() {
  if (!state.detail) return;
  const episode = state.detail.episode;
  renderViewerProfile();
  renderHeaderContext();
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
  renderHeaderContext();
  els.currentEpisode.textContent = "未选择 Episode";
  els.episodeMeta.textContent = "请选择左侧数据";
  els.cameraGrid.innerHTML = '<div class="empty-panel">当前 Episode 的有效相机会在此动态显示</div>';
  els.motionEmpty.hidden = false;
  els.motionEmpty.textContent = isEgoTask() ? "选择 Episode 后显示人体 Pose 骨架" : "选择 Episode 后显示 G1 29DOF 机器人";
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
  renderReviewFooter();
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
  els.enableStreamPreview.disabled = !playbackReady || Boolean(state.streamPreview);
  els.timelineRange.disabled = !hasEpisode;
  els.markIn.disabled = !hasEpisode || !annotationDurationNs(state.detail?.episode);
  els.markOut.disabled = els.markIn.disabled;
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
  const streamed = new Set((state.streamPreview?.cameras || []).map((item) => item.stream_id));
  els.cameraGrid.innerHTML = cameras.map((camera) => {
    const isStreamed = streamed.has(camera.stream_id);
    const videoUrl = isStreamed ? window.episodeQc.streamPreviewUrl({ episodeId: state.currentEpisodeId, streamId: camera.stream_id }) : "";
    return `
    <article class="camera-card${isStreamed ? " streaming-active" : ""}" data-camera-id="${escapeHtml(camera.stream_id)}" data-camera-topic="${escapeHtml(camera.topic)}" title="单击选择标注目标，双击放大">
      <div class="camera-heading"><div><span class="status-dot"></span><strong>${escapeHtml(camera.display_name)}</strong></div><span class="camera-time">等待帧</span></div>
      ${isStreamed ? `<video muted playsinline preload="metadata" src="${escapeHtml(videoUrl)}"></video>` : ""}
      <img alt="${escapeHtml(camera.display_name)}" />
    </article>`;
  }).join("");
  els.cameraGrid.querySelectorAll(".camera-card").forEach((card) => {
    card.addEventListener("click", (event) => { if (event.detail === 1) selectCamera(card.dataset.cameraId); });
    card.addEventListener("dblclick", () => toggleCameraFullscreen(card.dataset.cameraId));
  });
  syncCameraSelectionUi();
  els.cameraGrid.querySelectorAll("video").forEach((video) => video.addEventListener("loadedmetadata", () => syncStreamVideos()));
  renderTargetContext();
  renderLabels();
}

function nearestCameraFrameIndex(camera, timeNs) {
  const offsets = camera.frame_offsets_ns || [];
  if (!offsets.length) return 0;
  let low = 0, high = offsets.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (Number(offsets[middle]) < timeNs) low = middle + 1;
    else high = middle;
  }
  if (!low) return 0;
  if (low >= offsets.length) return offsets.length - 1;
  return Math.abs(Number(offsets[low]) - timeNs) < Math.abs(Number(offsets[low - 1]) - timeNs) ? low : low - 1;
}

function syncStreamVideos({ play = state.playing, forceSeek = false } = {}) {
  if (!state.streamPreview || !state.cache) return;
  const fpsByStream = new Map((state.streamPreview.cameras || []).map((item) => [item.stream_id, Number(item.fps || 30)]));
  (state.cache.cameras || []).forEach((camera) => {
    const video = els.cameraGrid.querySelector(`[data-camera-id="${camera.stream_id}"] video`);
    if (!video || !video.readyState) return;
    const target = nearestCameraFrameIndex(camera, state.playheadNs) / (fpsByStream.get(camera.stream_id) || 30);
    if (forceSeek || !play || Math.abs(video.currentTime - target) > 0.12) video.currentTime = target;
    video.playbackRate = state.playbackRate;
    if (play) void video.play().catch(() => {});
    else video.pause();
  });
}

async function enableStreamPreview() {
  if (!state.currentEpisodeId || !state.cache) return;
  els.enableStreamPreview.disabled = true;
  els.enableStreamPreview.textContent = "生成中…";
  setCacheStatus("busy", "正在从本机播放缓存生成轻量视频流…");
  try {
    const preview = await window.episodeQc.prepareStreamPreview(state.currentEpisodeId);
    if (state.currentEpisodeId !== state.playbackEpisodeId) return;
    state.streamPreview = preview;
    renderCameras();
    syncStreamVideos({ forceSeek: true });
    setCacheStatus("ready", "轻量视频流已就绪 · 标注仍映射原始帧时间戳");
    els.enableStreamPreview.textContent = "流预览已启用";
  } catch (error) {
    setCacheStatus("error", "流媒体预览生成失败");
    toast(error.message || String(error), "error", 8000);
    els.enableStreamPreview.textContent = "流预览";
  } finally {
    els.enableStreamPreview.disabled = Boolean(state.streamPreview) || !state.cache;
  }
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
  renderViewerProfile();
  const available = Boolean(state.cache?.robot_actions?.available || state.cache?.motion?.available);
  els.motionCard.classList.toggle("ready", available);
  els.motionEmpty.hidden = available;
  if (!available) els.motionEmpty.textContent = isEgoTask()
    ? "当前 Episode 无可用的 Pose 骨架"
    : "当前 Episode 无可用的 G1 动作或 Mocap";
  renderMotionSourceOptions();
  renderJointOptions(state.cache?.motion?.available ? state.cache.motion.joint_names || [] : []);
  renderTargetContext();
  renderLabels();
}

function renderMotionSourceOptions() {
  if (isEgoTask()) {
    els.motionSource.innerHTML = '<option value="pose">SMPL 24 关节骨架</option>';
    els.motionSource.value = "pose";
    els.motionSource.disabled = true;
    return;
  }
  const sourceNames = {
    policy: "实际执行姿态（Policy，默认）",
    policy_target: "目标姿态（PMG）",
    policy_command: "最终控制目标（Policy）",
    soma: "重定向姿态（SOMA）"
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
  const streams = state.detail?.streams || [];
  const cameras = streams.filter((item) => item.stream_type === "camera" && item.available);
  const mocapAvailable = streams.some((item) => item.stream_type === "mocap" && item.available);
  const sourceLabel = `${cameras.length} 路相机${mocapAvailable ? " + Mocap" : " · 无 Mocap"}`;
  const complete = cameras.length > 0 && mocapAvailable;
  const messageCount = streams
    .filter((item) => item.available)
    .reduce((total, item) => total + Number(item.message_count || 0), 0);
  els.coverageTracks.innerHTML = `
    <div class="data-source-sync ${complete ? "complete" : "partial"}" title="${escapeHtml(sourceLabel)} · 合计 ${messageCount} 条消息">
      <span><strong>数据源同步</strong><small>${escapeHtml(sourceLabel)}</small></span>
      <span class="coverage-bar"><i></i></span>
      <em>${complete ? "完整" : "部分可用"}</em>
    </div>`;
}

async function requestVisualFrames(force = false) {
  if (!state.cache || !state.currentEpisodeId || !state.playbackEpisodeId || state.visualPending) return;
  const now = performance.now();
  if (!force && now - state.lastVisualRequest < 90) return;
  state.lastVisualRequest = now;
  state.visualPending = true;
  const generation = state.visualGeneration || 0;
  const episodeId = state.currentEpisodeId;
  const playbackEpisodeId = state.playbackEpisodeId;
  const timeNs = Math.max(0, Math.min(state.durationNs, Math.round(state.playheadNs)));
  try {
    const useStreamPreview = Boolean(state.streamPreview);
    if (useStreamPreview) syncStreamVideos({ forceSeek: force || !state.playing });
    const cameraRequests = (useStreamPreview ? [] : (state.cache.cameras || [])).map(async (camera) => {
      const frame = await window.episodeQc.getCameraFrame({
        episodeId: playbackEpisodeId,
        streamId: camera.stream_id,
        timeNs,
      });
      if (episodeId !== state.currentEpisodeId || generation !== (state.visualGeneration || 0)) {
        if (frame.dataUrl?.startsWith('blob:')) URL.revokeObjectURL(frame.dataUrl);
        return;
      }
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
      const totalFrames = Number(camera.frame_offsets_ns?.length || camera.message_count || 0);
      const frameText = totalFrames ? `F${frame.frameIndex + 1}/${totalFrames}` : `F${frame.frameIndex + 1}`;
      card.querySelector(".camera-time").textContent = `${formatClock(frame.frameOffsetNs)} · ${frameText} · ${formatSkew(frame.skewNs)}`;
    });
    const motionRequest = state.cache.motion?.available
      ? window.episodeQc.getMotionFrame({ episodeId: playbackEpisodeId, timeNs }).then((frame) => {
          if (episodeId === state.currentEpisodeId && generation === (state.visualGeneration || 0)) { state.motionFrame = frame; drawMotion(); }
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
          if (episodeId === state.currentEpisodeId && generation === (state.visualGeneration || 0) && requestedSource === state.motionSource) {
            state.robotActionFrame = frame;
            drawMotion();
          }
        })
      : Promise.resolve();
    await Promise.all([...cameraRequests, motionRequest, actionRequest]);
    if (episodeId === state.currentEpisodeId && generation === (state.visualGeneration || 0)) {
      state.visualReadyTime=timeNs;
      setCacheStatus("ready", state.streamPreview
        ? "轻量视频流回放中 · 标注仍映射原始帧时间戳"
        : (state.cache.reused ? "播放缓存已复用" : "播放缓存已就绪"));
    }
  } catch (error) {
    if (episodeId === state.currentEpisodeId && generation === (state.visualGeneration || 0)) setCacheStatus("error", `帧读取失败：${error.message || error}`);
  } finally {
    if (generation === (state.visualGeneration || 0)) state.visualPending = false;
    if (episodeId === state.currentEpisodeId && generation === (state.visualGeneration || 0)) {
      calibration?.render();
      // A paused drag may have moved again while this request was in flight.
      // Always fetch the final position; otherwise the playhead and image differ.
      if(!state.playing && Math.round(state.playheadNs)!==timeNs) void requestVisualFrames(true);
    }
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
    if(calibrationPreview && state.playheadNs>=calibrationPreview.end){state.playheadNs=calibrationPreview.end;state.playing=false;calibrationPreview=null;updatePlaybackButton();renderClock();requestVisualFrames(true);requestAnimationFrame(playbackLoop);return;}
    const start = state.selectionStartNs;
    const end = state.selectionEndNs;
    if (!calibrationPreview && els.loopSelection.checked && start !== null && end !== null && end > start && state.playheadNs >= end) {
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
  syncStreamVideos({ forceSeek: true });
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
  const grid = selectionFrameGrid();
  const frame = framePositionForTime(state.playheadNs, state.durationNs, grid);
  if (frame) {
    els.framePosition.textContent = `${frame.exact ? "" : "≈"}F${frame.number} / F${frame.total}`;
    els.framePosition.title = `${grid.displayName || "参考相机"} · ${frame.exact ? "按真实帧时间戳定位" : "按平均帧间隔估算"}`;
  } else {
    els.framePosition.textContent = "F-- / F--";
    els.framePosition.title = "播放缓存就绪后显示帧号";
  }
  els.timelineRange.value = state.durationNs ? String(Math.round((state.playheadNs / state.durationNs) * 1_000_000)) : "0";
  calibration?.render();
}

function markSelectionStart() {
  warnAnnotationTail();
  const selection = beginRangeSelection({
    playheadNs: state.playheadNs,
    durationNs: annotationDurationNs(state.detail?.episode),
    grid: selectionFrameGrid(),
  });
  state.selectionStartNs = selection.startNs;
  state.selectionEndNs = selection.endNs;
  renderSelection();
}

function markSelectionEnd() {
  warnAnnotationTail();
  const selection = completeRangeSelection({
    startNs: state.selectionStartNs,
    playheadNs: state.playheadNs,
    durationNs: annotationDurationNs(state.detail?.episode),
    grid: selectionFrameGrid(),
  });
  if (!selection.ok) {
    const message = selection.reason === "missing_start"
      ? "请先按 I 设置区间起点"
      : "区间终点必须晚于起点，请移动播放位置后重试";
    toast(message, "error");
    return;
  }
  state.selectionStartNs = selection.startNs;
  state.selectionEndNs = selection.endNs;
  renderSelection();
}

function selectionFrameGrid() {
  return frameGridForCameras(state.cache?.cameras || [], state.selectedCameraId);
}

function resetRangeSelection() {
  state.selectionStartNs = null;
  state.selectionEndNs = null;
  renderSelection();
}

function renderSelection() {
  const episode = state.detail?.episode;
  const limit = annotationDurationNs(episode);
  const timingHint = episode?.annotation_timing_error ||
    `可标注 0–${(limit / 1e9).toFixed(9).replace(/\.?0+$/, "")} 秒${limit < state.durationNs ? " · 尾部仅播放" : ""}`;
  $("annotation-limit").textContent = !episode ? "" : episode.annotation_timing_error ? "Flow 时长未同步，暂不可标注" : timingHint;
  $("annotation-limit").title = episode ? timingHint : "";
  const grid = selectionFrameGrid();
  if (state.selectionStartNs === null) {
    els.selectionLabel.textContent = "未选择区间";
  } else if (state.selectionEndNs === null) {
    const frame = framePositionForTime(state.selectionStartNs, state.durationNs, grid);
    els.selectionLabel.textContent = `${frame ? `${frame.exact ? "" : "≈"}F${frame.number} · ` : ""}${formatClock(state.selectionStartNs)} → 等待终点`;
  } else {
    const durationText = formatSeconds(state.selectionEndNs - state.selectionStartNs);
    const frameText = formatFrameRange(frameRangeForInterval(state.selectionStartNs, state.selectionEndNs, grid));
    els.selectionLabel.textContent = `${frameText ? `${frameText} · ` : ""}${formatClock(state.selectionStartNs)} → ${formatClock(state.selectionEndNs)} · ${durationText}`;
  }
}

function warnAnnotationTail() {
  if (state.playheadNs > annotationDurationNs(state.detail?.episode)) {
    toast("当前播放位置超出可标注范围，选区已限制到可标注终点", "info");
  }
}

function labelPageItems(labels) {
  const total = labels.length;
  const pages = Math.max(1, Math.ceil(total / LABEL_PAGE_SIZE));
  state.labelPage = Math.max(1, Math.min(pages, state.labelPage));
  const start = (state.labelPage - 1) * LABEL_PAGE_SIZE;
  els.labelPagination.hidden = total <= LABEL_PAGE_SIZE;
  els.labelPagePrevious.disabled = state.labelPage <= 1;
  els.labelPageNext.disabled = state.labelPage >= pages;
  els.labelPageStatus.textContent = `第 ${state.labelPage} / ${pages} 页 · ${start + 1}–${Math.min(start + LABEL_PAGE_SIZE, total)} / ${total}`;
  return labels.slice(start, start + LABEL_PAGE_SIZE);
}

function hideLabelPagination() {
  els.labelPagination.hidden = true;
  state.labelPage = 1;
}

function renderLabels() {
  const labels = state.labelSchema?.labels || [];
  const openMode = state.currentTask?.annotation_mode === "open"
    || state.labelSchema?.schema?.annotation_mode === "open";
  const egoFixedMode = isEgoTask() && !openMode;
  els.openLabelEditor.hidden = !openMode;
  els.labelSearch.parentElement.hidden = openMode;
  els.labelHelp.textContent = openMode
    ? "兼容历史开放任务；当前任务未绑定固定步骤模板，标注仍按原结构保存"
    : egoFixedMode
      ? "先选择固定步骤，再填写人工语义和结构化字段，最后保存本段标注"
      : "简易模板只要求填写标签名称，其余字段均可省略";
  renderEgoDraftControls();
  if (openMode) {
    renderOpenLabels(labels);
    return;
  }
  const annotations = state.detail?.annotations || [];
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
  const annotatedCodes = new Set(annotations.map((annotation) => annotation.label_code));
  const visible = enabled.filter((label) => (label.annotation_scopes?.includes(state.scope) || annotatedCodes.has(label.code)) && (activeGroup === "all" || label.group === activeGroup) && (!query || `${label.code} ${label.name} ${label.description || ""}`.toLowerCase().includes(query)));
  const currentTarget = currentAnnotationTarget();
  const usable = visible.filter((label) => label.annotation_scopes?.includes(state.scope) && labelSupportsTarget(label, currentTarget));
  const schemaHeader = state.labelSchema?.schema || {};
  const labelSetName = labelSetDisplayName(schemaHeader.label_set_id, schemaHeader.label_set_name);
  const versionText = schemaHeader.schema_version ? ` · v${schemaHeader.schema_version}` : "";
  els.labelSetMeta.textContent = labelSetName ? `${labelSetName}${versionText}` : "尚未导入标签库";
  els.labelSetMeta.title = els.labelSetMeta.textContent;
  els.labelCount.textContent = `${usable.length} 可用 / ${visible.length}`;
  if (!visible.length) {
    hideLabelPagination();
    els.labelList.innerHTML = `<div class="empty-panel">${labels.length ? "当前范围没有可用标签" : "请先导入标签库"}</div>`;
    return;
  }
  els.labelList.innerHTML = labelPageItems(visible).map((label) => {
    const supported = labelSupportsTarget(label, currentTarget);
    const scopeSupported = label.annotation_scopes?.includes(state.scope);
    const enabledForEpisode = supported && scopeSupported && Boolean(state.detail);
    const targetHint = targetTypesDescription(label.target_types || []);
    const title = supported
      ? `${label.name} · ${groups.get(label.group) || groupDisplayName(label.group)}${label.description ? `\n${label.description}` : ""}`
      : `当前对象“${currentTarget.displayName}”不可用；该标签支持：${targetHint}`;
    const relatedAnnotations = annotations.filter((annotation) => annotation.label_code === label.code);
    const annotationStatus = labelAnnotationStatus(relatedAnnotations);
    const focusOnly = !enabledForEpisode && relatedAnnotations.length > 0;
    const selectedStep = egoFixedMode && state.selectedEgoStepCode === label.code;
    return `
    <button class="label-button${selectedStep ? " selected-step" : ""}${enabledForEpisode ? "" : focusOnly ? " label-view-only" : " target-disabled"}" data-label-code="${escapeHtml(label.code)}" data-focus-only="${focusOnly}" style="--label-color:${escapeHtml(label.color || "#8c959f")}" title="${escapeHtml(focusOnly ? "当前范围不可新增；点击定位已有标注" : state.detail ? title : "请先选择 Episode")}" aria-pressed="${selectedStep}" type="button"${enabledForEpisode || focusOnly ? "" : " disabled"}>
      <i class="label-color"></i>
      <span class="label-copy"><strong>${escapeHtml(label.name)}</strong><small>${escapeHtml(supported ? (groups.get(label.group) || label.group) : `仅支持：${targetHint}`)}</small></span>
      <span class="label-button-meta">${annotationStatus ? `<span class="label-annotation-status ${annotationStatus.tone}" data-focus-label="${escapeHtml(label.code)}" title="定位该标签的全部有效标注">${escapeHtml(annotationStatus.text)}</span>` : ""}${label.shortcut ? `<kbd>${escapeHtml(label.shortcut)}</kbd>` : ""}</span>
    </button>`;
  }).join("");
}

function renderOpenLabels(labels) {
  const annotations = state.detail?.annotations || [];
  const currentTarget = currentAnnotationTarget();
  const visible = labels.filter((label) => label.enabled !== false);
  els.labelSetMeta.textContent = `开放标签 · ${state.labelSchema?.schema?.annotation_schema_version || "ego_open_v1"}`;
  els.labelSetMeta.title = "无需绑定标签库；保留结构版本和原始标签快照";
  els.labelCount.textContent = `${visible.length} 个建议`;
  if (!visible.length) {
    hideLabelPagination();
    els.labelList.innerHTML = '<div class="empty-panel">直接在上方输入第一个自定义标签</div>';
    return;
  }
  els.labelList.innerHTML = labelPageItems(visible).map((label) => {
    const related = annotations.filter((annotation) => annotation.label_slug === label.code || annotation.label_code === label.code);
    const status = labelAnnotationStatus(related);
    return `<button class="label-button" data-label-code="${escapeHtml(label.code)}" style="--label-color:${escapeHtml(label.color || "#cfef5a")}" title="以建议项创建标注；可先修改上方输入框" type="button"${state.detail ? "" : " disabled"}>
      <i class="label-color"></i>
      <span class="label-copy"><strong>${escapeHtml(label.name)}</strong><small>${escapeHtml(groupDisplayName(label.annotation_type || label.group || "other"))} · ${escapeHtml(currentTarget.displayName)}</small></span>
      <span class="label-button-meta">${status ? `<span class="label-annotation-status ${status.tone}">${escapeHtml(status.text)}</span>` : ""}</span>
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

function isOpenAnnotationMode() {
  return state.currentTask?.annotation_mode === "open"
    || state.labelSchema?.schema?.annotation_mode === "open";
}

function egoFieldElements() {
  return {
    semantic_description: els.egoSemanticDescription,
    body_part: els.egoBodyPart,
    object_name: els.egoObjectName,
    object_color: els.egoObjectColor,
    source_name: els.egoSourceName,
    target_name: els.egoTargetName,
    exception_type: els.egoExceptionType,
    recovery_action: els.egoRecoveryAction,
  };
}

function editEgoFieldElements() {
  return {
    semantic_description: els.editEgoSemanticDescription,
    body_part: els.editEgoBodyPart,
    object_name: els.editEgoObjectName,
    object_color: els.editEgoObjectColor,
    source_name: els.editEgoSourceName,
    target_name: els.editEgoTargetName,
  };
}

function clearEgoDraft() {
  state.selectedEgoStepCode = null;
  state.previousEgoDetail = null;
  state.previousEgoDetailEpisodeId = null;
  state.previousEgoDetailTaskId = null;
  Object.values(egoFieldElements()).forEach((element) => { element.value = ""; });
  renderEgoDraftControls();
}

function writeEgoDraft(draft) {
  const labels = state.labelSchema?.labels || [];
  if (!labels.some((label) => label.code === draft.labelCode)) {
    toast("当前任务模板中不存在要复用的固定步骤", "error");
    return false;
  }
  state.selectedEgoStepCode = draft.labelCode;
  const fields = egoFieldElements();
  Object.entries(draft.values || {}).forEach(([field, value]) => {
    if (fields[field]) fields[field].value = value || "";
  });
  resetRangeSelection();
  renderLabels();
  return true;
}

function renderEgoDraftControls() {
  const fixedMode = isEgoTask() && !isOpenAnnotationMode();
  els.egoStepDraft.hidden = !fixedMode;
  els.egoSemanticField.hidden = !fixedMode;
  if (!fixedMode) return;
  const label = (state.labelSchema?.labels || []).find(
    (item) => item.code === state.selectedEgoStepCode,
  );
  if (!label && state.selectedEgoStepCode) state.selectedEgoStepCode = null;
  els.egoSelectedStep.textContent = label
    ? `${label.code} · ${label.name}`
    : "请从下方选择步骤";
  els.saveEgoAnnotation.disabled = !label || !state.detail;
  els.saveEgoAnnotation.textContent = label ? `保存：${label.name}` : "保存本段标注";
  const previousEpisode = previousEpisodeForCurrent(
    state.episodes,
    state.currentEpisodeId,
  );
  const canReuse = Boolean(
    previousEpisode && label && labelUsesEgoSemanticFields(label),
  );
  els.egoNewObject.disabled = !canReuse;
  els.egoReuseObject.disabled = !canReuse;
  const reuseTitle = previousEpisode
    ? `从上一条数据 ${previousEpisode.episode_name || previousEpisode.relative_path || previousEpisode.id} 的同一步骤继承描述`
    : "当前是第一条数据，没有可继承的上一条 Episode";
  els.egoNewObject.title = reuseTitle;
  els.egoReuseObject.title = reuseTitle;
  const semanticField = label?.fields?.find((field) => field.code === "semantic_description");
  els.egoSemanticField.hidden = Boolean(label) && !semanticField;
  els.egoSemanticDescription.required = Boolean(semanticField?.required);
}

function selectEgoStep(labelCode) {
  const label = (state.labelSchema?.labels || []).find((item) => item.code === labelCode);
  if (!label) return;
  state.selectedEgoStepCode = label.code;
  renderLabels();
  if (labelUsesEgoSemanticFields(label)) {
    els.egoSemanticDescription.focus();
    toast(`已选择固定步骤：${label.name}；填写字段并选择区间后保存`, "success", 3500);
  } else {
    toast(`已选择标签：${label.name}；选择范围后保存`, "success", 3000);
  }
}

function saveSelectedEgoAnnotation() {
  if (!state.selectedEgoStepCode) return toast("请先选择固定步骤", "error");
  return createAnnotation(
    state.selectedEgoStepCode,
    { saveSelectedEgoStep: true },
  );
}

async function previousEpisodeEgoDraft(labelCode) {
  const currentEpisodeId = state.currentEpisodeId;
  const currentTaskId = state.currentTaskId;
  const previousEpisode = previousEpisodeForCurrent(
    state.episodes,
    currentEpisodeId,
  );
  if (!previousEpisode) return { previousEpisode: null, draft: null };
  if (
    state.previousEgoDetailEpisodeId !== previousEpisode.id
    || state.previousEgoDetailTaskId !== currentTaskId
  ) {
    const detail = await window.episodeQc.getEpisode(previousEpisode.id);
    if (
      state.currentEpisodeId !== currentEpisodeId
      || state.currentTaskId !== currentTaskId
    ) return { previousEpisode, draft: null, stale: true };
    state.previousEgoDetail = detail;
    state.previousEgoDetailEpisodeId = previousEpisode.id;
    state.previousEgoDetailTaskId = currentTaskId;
  }
  return {
    previousEpisode,
    draft: egoDraftForLabel(
      state.previousEgoDetail?.annotations || [],
      labelCode,
    ),
  };
}

async function applyEgoDraftShortcut(kind) {
  const selectedLabel = (state.labelSchema?.labels || []).find(
    (label) => label.code === state.selectedEgoStepCode,
  );
  if (!selectedLabel || !labelUsesEgoSemanticFields(selectedLabel)) {
    return toast("请先选择要继承的语义动作步骤", "error");
  }
  let source;
  try {
    source = await previousEpisodeEgoDraft(selectedLabel.code);
  } catch (error) {
    return toast(`读取上一条数据失败：${error.message || error}`, "error", 6500);
  }
  if (source.stale) return;
  if (!source.previousEpisode) {
    return toast("当前是第一条数据，没有可继承的上一条 Episode", "error");
  }
  if (!source.draft) {
    const previousName = source.previousEpisode.episode_name
      || source.previousEpisode.relative_path
      || source.previousEpisode.id;
    return toast(`上一条数据 ${previousName} 没有“${selectedLabel.name}”的标注描述`, "error", 5200);
  }
  const draft = kind === "new_object"
    ? sameStepNewObjectDraft(source.draft)
    : reuseSameObjectDraft(
      source.draft,
      selectedLabel.code,
    );
  if (!writeEgoDraft(draft)) return;
  const previousName = source.previousEpisode.episode_name
    || source.previousEpisode.relative_path
    || source.previousEpisode.id;
  if (kind === "new_object") {
    els.egoObjectName.focus();
    toast(`已从上一条数据 ${previousName} 继承同一步骤描述；请填写新物品并按本段修改`, "success", 4800);
  } else {
    els.egoSemanticDescription.focus();
    toast(`已从上一条数据 ${previousName} 继承同一步骤描述和物品属性；请核对后修改`, "success", 4800);
  }
}

async function createAnnotation(labelCode, { saveSelectedEgoStep = false } = {}) {
  if (!state.detail) return toast("请先选择 Episode", "error");
  const label = state.labelSchema?.labels?.find((item) => item.code === labelCode);
  if (!label) return;
  if (state.currentTask?.annotation_mode === "open" || state.labelSchema?.schema?.annotation_mode === "open") {
    els.openAnnotationType.value = label.annotation_type || label.group || "other";
    els.openLabelName.value = label.name;
    return createOpenAnnotation(label.code);
  }
  if (isEgoTask() && !saveSelectedEgoStep) {
    selectEgoStep(label.code);
    return;
  }
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
    end = annotationDurationNs(state.detail.episode);
  }
  const timingError = annotationTimeError(state.detail.episode, state.scope, start, end);
  if (timingError) return toast(timingError, "error", 7000);
  const target = targetForLabel(label);
  if (!target) {
    const current = currentAnnotationTarget();
    return toast(`“${label.name}”不支持当前对象“${current.displayName}”；请选择：${targetTypesDescription(label.target_types || [])}`, "error", 5500);
  }
  const attributes = collectCustomFields(label, target);
  if (attributes === null) return;
  const egoDraft = isEgoTask() && labelUsesEgoSemanticFields(label)
    ? createEgoDraft(label.code, attributes)
    : null;
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
    if (egoDraft) {
      state.selectedEgoStepCode = label.code;
      els.egoSemanticDescription.value = "";
      els.egoExceptionType.value = "";
      els.egoRecoveryAction.value = "";
    }
    state.detail.episode.annotation_count = state.detail.annotations.length;
    updateEpisodeFromDetail();
    renderAnnotations();
    els.annotationComment.value = "";
    resetRangeSelection();
    setSaveState("saved", "已保存");
  } catch (error) {
    setSaveState("error", "保存失败");
    toast(error.message || String(error), "error", 7000);
  }
}

async function createOpenAnnotation(labelSlug = "") {
  if (!state.detail) return toast("请先选择 Episode", "error");
  const labelName = els.openLabelName.value.trim();
  if (!labelName) return toast("请输入自定义标签名称", "error");
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
    end = annotationDurationNs(state.detail.episode);
  }
  const timingError = annotationTimeError(state.detail.episode, state.scope, start, end);
  if (timingError) return toast(timingError, "error", 7000);
  const target = currentAnnotationTarget();
  const attributes = {};
  const fieldValues = {
    body_part: els.egoBodyPart.value.trim(), object_name: els.egoObjectName.value.trim(),
    object_color: els.egoObjectColor.value.trim(), source_name: els.egoSourceName.value.trim(),
    target_name: els.egoTargetName.value.trim(), exception_type: els.egoExceptionType.value.trim(),
    recovery_action: els.egoRecoveryAction.value.trim(),
  };
  Object.entries(fieldValues).forEach(([key, value]) => { if (value) attributes[key] = value; });
  const annotationType = els.openAnnotationType.value;
  const payload = {
    episode_id: state.currentEpisodeId, annotation_mode: "open",
    annotation_schema_version: state.currentTask?.annotation_schema_version || "ego_open_v1",
    annotation_type: annotationType, label_name: labelName, label_slug: labelSlug,
    scope: state.scope, start_offset_ns: start, end_offset_ns: end,
    target_type: target.targetType, target_key: target.targetKey,
    severity: "normal", action: ["pose_quality", "camera_quality"].includes(annotationType) ? "repair" : "keep",
    comment: els.annotationComment.value.trim(), attributes,
    reviewer_name: els.reviewerName.value.trim(), status: "confirmed",
  };
  setSaveState("saving", "保存中…");
  try {
    const saved = await window.episodeQc.saveAnnotation({ payload });
    state.detail.annotations.push(saved);
    if (!state.labelSchema.labels.some((item) => item.code === saved.label_slug)) {
      state.labelSchema.labels.unshift({
        code: saved.label_slug,
        name: saved.label_name,
        group: saved.annotation_type,
        annotation_type: saved.annotation_type,
        enabled: true,
        color: "#cfef5a",
      });
    }
    state.detail.episode.annotation_count = state.detail.annotations.length;
    updateEpisodeFromDetail();
    renderAnnotations();
    els.openLabelName.value = "";
    els.annotationComment.value = "";
    resetRangeSelection();
    setSaveState("saved", "已保存");
  } catch (error) {
    setSaveState("error", "保存失败");
    toast(error.message || String(error), "error", 7000);
  }
}

function collectCustomFields(label, target) {
  const attributes = {};
  const egoInputs = {
    semantic_description: els.egoSemanticDescription,
    body_part: els.egoBodyPart,
    object_name: els.egoObjectName,
    object_color: els.egoObjectColor,
    source_name: els.egoSourceName,
    target_name: els.egoTargetName,
    exception_type: els.egoExceptionType,
    recovery_action: els.egoRecoveryAction,
  };
  for (const field of label.fields || []) {
    let value = null;
    const egoInput = isEgoTask() ? egoInputs[field.code] : null;
    if (egoInput) value = egoInput.value.trim();
    else if (field.type === "joint_selector") {
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
  state.labelPage = 1;
  syncJointSelectionUi();
  syncCameraSelectionUi();
  renderTargetContext();
  renderClock();
  renderSelection();
  renderAnnotations();
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
    const label = labels.get(annotation.label_code) || {
      name: annotation.label_name || annotation.label_code,
      color: annotation.annotation_mode === "open" ? "#cfef5a" : "#8c959f",
    };
    const round = annotationRoundMeta(annotation);
    const badgeTitle = round.inherited
      ? `从历史质检结果继承，可修改或删除；首次 R${round.originRound}，最近 R${round.lastRound}`
      : `本轮 R${round.lastRound} 新增`;
    const semanticDescription = String(annotation.attributes?.semantic_description || "").trim();
    const details = [
      semanticDescription,
      annotationTargetName(annotation),
      severities.get(annotation.severity) || annotation.severity || "未分级",
    ].filter(Boolean).join(" · ");
    const fullTiming = annotationTiming(annotation);
    return `<div class="annotation-item ${round.tone}" data-annotation-id="${escapeHtml(annotation.annotation_id)}"><i style="background:${escapeHtml(label.color || "#8c959f")}"></i><span class="annotation-copy"><span class="annotation-title-row"><strong>${escapeHtml(label.name)}</strong><em class="inherited-annotation-badge" title="${escapeHtml(badgeTitle)}">${escapeHtml(round.badge)}</em></span>${details ? `<small title="${escapeHtml(details)}">${escapeHtml(details)}</small>` : ""}<time title="${escapeHtml(fullTiming)}">${escapeHtml(annotationCardTiming(annotation))}</time></span></div>`;
  }).join("");
  renderAnnotationLanes(annotations, labels);
  const summary = summarizeAnnotationChanges(
    annotations,
    state.detail?.deleted_annotation_lineages || [],
  );
  syncCurrentEpisodeIncrementalSummary(summary);
  renderReviewFooter();
  renderEpisodeList();
  renderLabels();
}

function renderAnnotationLanes(annotations, labels) {
  const visible = annotations.filter((annotation) => {
    const round = annotationRoundMeta(annotation);
    if (state.timelineView === "changes") return round.tone === "added" || round.tone === "modified";
    if (state.timelineView === "history") return round.inherited;
    return true;
  });
  const aiSegments = state.timelineView === "effective"
    ? contiguousAiSegmentGroup(visible, labels, annotationDurationNs(state.detail?.episode))
    : [];
  const aiIds = new Set(aiSegments.map((annotation) => annotation.annotation_id));
  const grouped = new Map();
  visible.filter((annotation) => !aiIds.has(annotation.annotation_id)).forEach((annotation) => {
    if (!grouped.has(annotation.label_code)) grouped.set(annotation.label_code, []);
    grouped.get(annotation.label_code).push(annotation);
  });
  if (!grouped.size && !aiSegments.length) {
    const emptyText = state.timelineView === "changes" ? "本条暂时没有新增或修改" : "暂无有效标注";
    els.annotationTrack.innerHTML = `<div class="timeline-empty annotation-lane-surface">${emptyText} · 可在此拖拽选择区间</div>`;
    return;
  }
  const aiTrack = aiSegments.length ? renderAiSegmentTrack(aiSegments, labels) : "";
  const overlayHeading = aiSegments.length && grouped.size
    ? '<div class="annotation-layer-divider"><span>附加标注</span><small>可重叠</small></div>'
    : "";
  els.annotationTrack.innerHTML = aiTrack + overlayHeading + [...grouped.entries()].map(([labelCode, items]) => {
    const first = items[0] || {};
    const label = labels.get(labelCode) || {
      name: first.label_name || labelCode,
      color: first.annotation_mode === "open" ? "#cfef5a" : "#8c959f",
    };
    const blocks = items.map((annotation) => {
      const round = annotationRoundMeta(annotation);
      const startNs = annotation.scope === "episode" ? 0 : Math.max(0, Number(annotation.start_offset_ns) || 0);
      const endNs = Math.max(startNs, Number(annotation.end_offset_ns) || 0);
      const left = state.durationNs ? Math.max(0, Math.min(100, (startNs / state.durationNs) * 100)) : 0;
      const width = state.durationNs ? Math.max(.7, Math.min(100 - left, ((endNs - startNs) / state.durationNs) * 100)) : .7;
      const pointClass = annotation.scope === "time_point" ? " annotation-point" : "";
      return `<button type="button" class="annotation-block ${round.tone}${pointClass}" data-annotation-id="${escapeHtml(annotation.annotation_id)}" aria-label="${escapeHtml(label.name)}，${escapeHtml(annotationTiming(annotation))}，${escapeHtml(round.badge)}" title="${escapeHtml(label.name)} · ${escapeHtml(annotationTiming(annotation))} · ${escapeHtml(round.badge)}" style="--annotation-left:${left}%;--annotation-width:${width}%;--annotation-color:${escapeHtml(label.color || "#8c959f")}"><span>${escapeHtml(round.shortBadge)}</span></button>`;
    }).join("");
    return `<div class="effective-annotation-lane" data-label-lane="${escapeHtml(labelCode)}"><div class="annotation-lane-label" title="${escapeHtml(label.name)}"><i style="background:${escapeHtml(label.color || "#8c959f")}"></i><span>${escapeHtml(label.name)}</span></div><div class="annotation-lane-surface">${blocks}</div></div>`;
  }).join("");
  calibration?.render();
}

function renderAiSegmentTrack(segments, labels) {
  const blocks = segments.map((annotation) => {
    const label = labels.get(annotation.label_code) || { name: annotation.label_name || annotation.label_code, color: "#8c959f" };
    const startNs = Number(annotation.start_offset_ns) || 0;
    const endNs = Number(annotation.end_offset_ns) || startNs;
    const left = state.durationNs ? (startNs / state.durationNs) * 100 : 0;
    const width = state.durationNs ? ((endNs - startNs) / state.durationNs) * 100 : 0;
    const grid = frameGridForCameras(state.cache?.cameras || [], annotation.target_type === "camera" ? annotation.target_key : state.selectedCameraId);
    const frameText = formatFrameRange(frameRangeForInterval(startNs, endNs, grid));
    return `<button type="button" class="annotation-block ai-segment-block" data-annotation-id="${escapeHtml(annotation.annotation_id)}" data-display-start-ns="${startNs}" data-display-end-ns="${endNs}" aria-label="${escapeHtml(label.name)}，${escapeHtml(annotationTiming(annotation))}；单击定位，双击编辑" title="${escapeHtml(label.name)} · ${escapeHtml(annotationTiming(annotation))} · 单击定位，双击编辑" style="--annotation-left:${left}%;--annotation-width:${width}%;--annotation-color:${escapeHtml(label.color || "#8c959f")}"><span class="ai-segment-name"><b>${escapeHtml(label.name)}</b><small>${escapeHtml(frameText || `${formatClock(startNs)}–${formatClock(endNs)}`)}</small></span></button>`;
  }).join("");
  const handles = segments.slice(1).map((right, index) => {
    const left = segments[index];
    const position = state.durationNs ? (Number(right.start_offset_ns) / state.durationNs) * 100 : 0;
    const leftName = labels.get(left.label_code)?.name || left.label_name || left.label_code;
    const rightName = labels.get(right.label_code)?.name || right.label_name || right.label_code;
    return `<button type="button" class="ai-segment-boundary" data-boundary-left-id="${escapeHtml(left.annotation_id)}" data-boundary-right-id="${escapeHtml(right.annotation_id)}" data-boundary-offset-ns="${Number(right.start_offset_ns)}" aria-label="拖动 ${escapeHtml(leftName)} 与 ${escapeHtml(rightName)} 的分界点" title="拖动分界点：${escapeHtml(leftName)} ↔ ${escapeHtml(rightName)}" style="--boundary-left:${position}%"></button>`;
  }).join("");
  const first = segments[0], last = segments.at(-1);
  const firstName = labels.get(first.label_code)?.name || first.label_name || first.label_code;
  const lastName = labels.get(last.label_code)?.name || last.label_name || last.label_code;
  const outerHandles = [
    `<button type="button" class="ai-segment-boundary ai-segment-outer-boundary is-start" data-outer-boundary-edge="start" data-outer-boundary-annotation-id="${escapeHtml(first.annotation_id)}" aria-label="拖动首段起点：${escapeHtml(firstName)}" title="拖动首段起点（自动吸附到帧）" style="--boundary-left:${state.durationNs ? (Number(first.start_offset_ns) / state.durationNs) * 100 : 0}%"></button>`,
    `<button type="button" class="ai-segment-boundary ai-segment-outer-boundary is-end" data-outer-boundary-edge="end" data-outer-boundary-annotation-id="${escapeHtml(last.annotation_id)}" aria-label="拖动末段终点：${escapeHtml(lastName)}" title="拖动末段终点（自动吸附到帧）" style="--boundary-left:${state.durationNs ? (Number(last.end_offset_ns) / state.durationNs) * 100 : 0}%"></button>`,
  ].join("");
  return `<div class="effective-annotation-lane ai-segment-track" data-ai-segment-track><div class="annotation-lane-label" title="AI 整段互斥动作标签"><i></i><span>动作标签</span></div><div class="annotation-lane-surface">${blocks}${handles}${outerHandles}</div></div>`;
}

function annotationTiming(annotation) {
  const grid = frameGridForCameras(
    state.cache?.cameras || [],
    annotation.target_type === "camera" ? annotation.target_key : state.selectedCameraId,
  );
  if (annotation.scope === "episode") {
    const end = Number(annotation.end_offset_ns || 0);
    const frameText = formatFrameRange(frameRangeForInterval(0, end, grid));
    return `整条${frameText ? ` · ${frameText}` : ""} · ${formatSeconds(end)}`;
  }
  if (annotation.scope === "time_point") {
    const frame = framePositionForTime(annotation.start_offset_ns, state.durationNs, grid);
    return `${frame ? `${frame.exact ? "" : "≈"}F${frame.number} · ` : ""}${formatClock(annotation.start_offset_ns)}`;
  }
  const frameText = formatFrameRange(frameRangeForInterval(annotation.start_offset_ns, annotation.end_offset_ns, grid));
  return `${frameText ? `${frameText} · ` : ""}${formatClock(annotation.start_offset_ns)}–${formatClock(annotation.end_offset_ns)} · ${formatSeconds(Number(annotation.end_offset_ns) - Number(annotation.start_offset_ns))}`;
}

function annotationCardTiming(annotation) {
  const grid = frameGridForCameras(
    state.cache?.cameras || [],
    annotation.target_type === "camera" ? annotation.target_key : state.selectedCameraId,
  );
  if (annotation.scope === "episode") {
    const end = Number(annotation.end_offset_ns || 0);
    const frameText = formatFrameRange(frameRangeForInterval(0, end, grid));
    return ["整条", frameText, formatSeconds(end)].filter(Boolean).join(" · ");
  }
  if (annotation.scope === "time_point") {
    const frame = framePositionForTime(annotation.start_offset_ns, state.durationNs, grid);
    return frame ? `${frame.exact ? "" : "≈"}F${frame.number}` : formatClock(annotation.start_offset_ns);
  }
  const frameText = formatFrameRange(frameRangeForInterval(annotation.start_offset_ns, annotation.end_offset_ns, grid));
  const duration = formatSeconds(Number(annotation.end_offset_ns) - Number(annotation.start_offset_ns));
  return [frameText, duration].filter(Boolean).join(" · ");
}

function currentReviewRound(episode = state.detail?.episode) {
  const historyCount = Number(episode?.review_history_count || 0);
  return Math.max(1, historyCount + 1);
}

function annotationRoundMeta(annotation) {
  const source = annotation.attributes?._incremental_source;
  const currentRound = currentReviewRound();
  const inherited = Boolean(source);
  const sourceRound = Math.max(1, Number(source?.round_number || source?.origin_round_number || 1));
  const originRound = Math.max(1, Number(source?.origin_round_number || sourceRound));
  const modified = inherited && Boolean(annotation.created_at && annotation.updated_at && annotation.created_at !== annotation.updated_at);
  if (!inherited) {
    return { inherited: false, modified: false, originRound: currentRound, lastRound: currentRound, tone: "added", badge: `本轮新增 · R${currentRound}`, shortBadge: `R${currentRound}` };
  }
  if (modified) {
    return { inherited: true, modified: true, originRound, lastRound: currentRound, tone: "modified", badge: `R${sourceRound}→R${currentRound} 已修改`, shortBadge: `R${sourceRound}→R${currentRound}` };
  }
  const ai = source?.round_kind === 'ai';
  return { inherited: true, modified: false, originRound, lastRound: sourceRound, tone: "inherited", badge: `${ai ? 'AI 预标注 · 待人工复检' : '已标注'} · R${sourceRound}`, shortBadge: `${ai ? 'AI ' : ''}R${sourceRound}` };
}

function labelAnnotationStatus(annotations) {
  if (!annotations.length) return null;
  const metas = annotations.map(annotationRoundMeta);
  const currentRound = currentReviewRound();
  if (metas.some((item) => item.modified)) return { text: `${annotations.length}处 · R${currentRound} 已修改`, tone: "modified" };
  if (metas.some((item) => !item.inherited)) return { text: `${annotations.length}处 · 本轮新增`, tone: "added" };
  const rounds = [...new Set(metas.map((item) => item.lastRound))].sort((a, b) => a - b);
  return { text: `${annotations.length}处 · ${rounds.map((round) => `R${round}`).join("/")}`, tone: "inherited" };
}

function summarizeAnnotationChanges(annotations, deletedLineages) {
  const summary = { added: 0, modified: 0, removed: new Set(deletedLineages).size, preserved: 0 };
  annotations.forEach((annotation) => {
    const meta = annotationRoundMeta(annotation);
    if (!meta.inherited) summary.added += 1;
    else if (meta.modified) summary.modified += 1;
    else summary.preserved += 1;
  });
  return summary;
}

function syncCurrentEpisodeIncrementalSummary(summary) {
  const episode = state.episodes.find((item) => item.id === state.currentEpisodeId);
  if (!episode) return;
  episode.incremental_added_count = summary.added;
  episode.incremental_modified_count = summary.modified;
  episode.incremental_removed_count = summary.removed;
  episode.incremental_preserved_count = summary.preserved;
}

function focusLabelAnnotations(labelCode) {
  const lane = els.annotationTrack.querySelector(`[data-label-lane="${CSS.escape(labelCode)}"]`);
  if (!lane) return;
  lane.classList.remove("focused");
  lane.scrollIntoView({ block: "nearest", behavior: "smooth" });
  requestAnimationFrame(() => lane.classList.add("focused"));
  window.setTimeout(() => lane.classList.remove("focused"), 1200);
}

function selectAnnotationScope(scope) {
  if (!["time_range", "time_point", "episode"].includes(scope)) return;
  state.scope = scope;
  state.labelPage = 1;
  els.scopeTabs.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.scope === scope);
  });
  renderLabels();
}

function currentAiSegmentDisplay(annotationId) {
  if (state.timelineView !== "effective") return null;
  const labels = new Map((state.labelSchema?.labels || []).map((item) => [item.code, item]));
  return contiguousAiSegmentGroup(
    state.detail?.annotations || [],
    labels,
    annotationDurationNs(state.detail?.episode),
  ).find((annotation) => annotation.annotation_id === annotationId) || null;
}

function openAnnotationEditor(annotationId) {
  const annotation = state.detail?.annotations?.find((item) => item.annotation_id === annotationId);
  if (!annotation) return;
  selectAnnotationScope(annotation.scope);
  if (annotation.target_type === "camera") {
    const camera = (state.cache?.cameras || []).find(
      (item) => item.stream_id === annotation.target_key || item.topic === annotation.target_key,
    );
    if (camera) selectAnnotationTarget("camera", camera.stream_id);
  }
  const displayAnnotation = currentAiSegmentDisplay(annotationId) || annotation;
  const timingLocked = displayAnnotation !== annotation;
  const annotationLabel = (state.labelSchema?.labels || []).find(
    (label) => label.code === annotation.label_code,
  );
  els.editId.value = annotationId;
  els.editStart.value = (displayAnnotation.start_offset_ns / 1e9).toFixed(6);
  els.editEnd.value = (displayAnnotation.end_offset_ns / 1e9).toFixed(6);
  for (const input of [els.editStart, els.editEnd]) {
    input.max = String(annotationDurationNs(state.detail?.episode) / 1e9);
    input.step = "any";
  }
  els.annotationEditor.dataset.timingLocked = String(timingLocked);
  els.editStart.disabled = annotation.scope === "episode" || timingLocked;
  els.editEnd.disabled = annotation.scope === "episode" || timingLocked;
  els.editStartLabel.textContent = timingLocked ? "起点（由时间轴控制）" : "起点（秒，精调）";
  els.editEndLabel.textContent = timingLocked ? "终点（由时间轴控制）" : "终点（秒，精调）";
  els.editLabelName.textContent = annotationLabel?.name || annotation.label_name || annotation.label_code || "未命名标注";
  els.editFrameRange.textContent = annotationTiming(displayAnnotation);
  els.editTimingHelp.textContent = timingLocked
    ? "动作分段时间请直接拖动时间轴上的白色分界线；系统会同步更新左右两段，避免重叠或空帧。"
    : annotation.scope === "episode"
      ? "整条标注覆盖完整 Episode，时间范围无需修改。"
      : "帧号优先显示；秒数仅用于精确核对，也可以直接在时间轴拖动修改。";
  els.annotationEditor.classList.toggle("timing-locked", timingLocked);
  fillSelect(els.editSeverity, state.labelSchema?.severity_levels || [], annotation.severity);
  fillSelect(els.editAction, state.labelSchema?.actions || [], annotation.action);
  els.editComment.value = annotation.comment || "";
  const ego = isEgoTask();
  const openMode = annotation.annotation_mode === "open" || isOpenAnnotationMode();
  const semanticAnnotation = ego && !openMode && labelUsesEgoSemanticFields(annotationLabel);
  els.editEgoFields.hidden = !semanticAnnotation;
  els.editEgoStepField.hidden = !semanticAnnotation;
  els.editEgoSemanticField.hidden = !semanticAnnotation;
  if (semanticAnnotation) {
    const labels = (state.labelSchema?.labels || []).filter(
      (label) => label.enabled !== false && labelUsesEgoSemanticFields(label),
    );
    els.editEgoStep.innerHTML = labels.map((label) => (
      `<option value="${escapeHtml(label.code)}"${label.code === annotation.label_code ? " selected" : ""}>${escapeHtml(label.name)}</option>`
    )).join("");
    const fields = editEgoFieldElements();
    Object.entries(fields).forEach(([field, element]) => {
      element.value = String(annotation.attributes?.[field] || "");
    });
    renderEditEgoFieldState();
  }
  const round = annotationRoundMeta(annotation);
  els.editProvenance.hidden = false;
  els.editProvenance.textContent = round.inherited
    ? `首次标注 R${round.originRound} · 最近修改 R${round.lastRound} · 保存后记为 R${currentReviewRound()} 变更`
    : `本轮 R${round.lastRound} 新增标注`;
  seekTo(annotation.start_offset_ns);
  els.annotationEditor.showModal();
}

function renderEditEgoFieldState() {
  if (els.editEgoFields.hidden || isOpenAnnotationMode()) return;
  const label = (state.labelSchema?.labels || []).find(
    (item) => item.code === els.editEgoStep.value,
  );
  const semanticField = label?.fields?.find((field) => field.code === "semantic_description");
  els.editEgoSemanticField.hidden = !semanticField;
  els.editEgoSemanticDescription.required = Boolean(semanticField?.required);
}

function fillSelect(element, choices, selected) {
  element.innerHTML = choices.map((item) => `<option value="${escapeHtml(item.code)}" ${item.code === selected ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("");
}

async function saveAnnotationEdit() {
  const annotation = state.detail?.annotations?.find((item) => item.annotation_id === els.editId.value);
  if (!annotation) return;
  let labelCode = annotation.label_code;
  let attributes = { ...(annotation.attributes || {}) };
  if (isEgoTask() && !els.editEgoFields.hidden) {
    if (annotation.annotation_mode !== "open" && !isOpenAnnotationMode()) {
      labelCode = els.editEgoStep.value;
    }
    const label = (state.labelSchema?.labels || []).find((item) => item.code === labelCode);
    const editableFields = editEgoFieldElements();
    Object.entries(editableFields).forEach(([field, element]) => {
      const value = element.value.trim();
      if (value) attributes[field] = value;
      else delete attributes[field];
    });
    for (const field of label?.fields || []) {
      if (field.required && !attributes[field.code]) {
        toast(`标签“${label.name}”需要填写：${field.name || field.code}`, "error");
        return;
      }
    }
  }
  const payload = {
    ...annotation,
    episode_id: annotation.episode_id,
    label_code: labelCode,
    start_offset_ns: els.annotationEditor.dataset.timingLocked === "true"
      ? annotation.start_offset_ns
      : Math.round(Number(els.editStart.value) * 1e9),
    end_offset_ns: els.annotationEditor.dataset.timingLocked === "true"
      ? annotation.end_offset_ns
      : Math.round(Number(els.editEnd.value) * 1e9),
    severity: els.editSeverity.value,
    action: els.editAction.value,
    comment: els.editComment.value,
    attributes,
    reviewer_name: els.reviewerName.value.trim()
  };
  setSaveState("saving", "保存中…");
  try {
    const timingError = annotationTimeError(state.detail.episode, payload.scope, payload.start_offset_ns, payload.end_offset_ns);
    if (timingError) throw new Error(timingError);
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
  const annotation = state.detail?.annotations?.find((item) => item.annotation_id === annotationId);
  setSaveState("saving", "删除中…");
  try {
    await window.episodeQc.deleteAnnotation(annotationId);
    state.detail.annotations = state.detail.annotations.filter((item) => item.annotation_id !== annotationId);
    const lineage = annotation?.attributes?._incremental_lineage_id;
    const deletedLineages = state.detail.deleted_annotation_lineages ||= [];
    if (lineage && !deletedLineages.includes(lineage)) {
      deletedLineages.push(lineage);
    }
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
  if (!state.currentEpisodeId) return false;
  setSaveState("saving", "保存结论…");
  try {
    const episode = await window.episodeQc.updateReview({ episodeId: state.currentEpisodeId, status: "completed", decision, reviewer: els.reviewerName.value.trim(), playheadNs: state.playheadNs });
    state.detail.episode = { ...state.detail.episode, ...episode };
    updateEpisodeFromDetail();
    renderDecision();
    setSaveState("saved", "已保存");
    return true;
  } catch (error) {
    setSaveState("error", "保存失败");
    toast(error.message || String(error), "error");
    return false;
  }
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
  const inheritedDecision = pendingInheritedDecision(episode);
  const displayedDecision = decision || inheritedDecision;
  els.decisionGrid.querySelectorAll("[data-decision]").forEach((button) => {
    button.classList.toggle("active", button.dataset.decision === displayedDecision);
    button.disabled = !episode;
  });
  els.decisionCurrent.textContent = decision
    ? decisionName(decision)
    : inheritedDecision
      ? `继承：${decisionName(inheritedDecision)}（待确认）`
      : "未选择";
  els.decisionCurrent.classList.toggle("selected", Boolean(displayedDecision));
  els.decisionCurrent.classList.toggle("inherited", Boolean(inheritedDecision));
  els.needsRecheck.classList.toggle("active", episode?.review_status === "needs_recheck");
  els.needsRecheck.disabled = !episode;
  renderReviewFooter();
}

function renderReviewFooter() {
  const totals = state.episodes.reduce((summary, episode) => {
    summary.added += Number(episode.incremental_added_count || 0);
    summary.modified += Number(episode.incremental_modified_count || 0);
    summary.removed += Number(episode.incremental_removed_count || 0);
    summary.preserved += Number(episode.incremental_preserved_count || 0);
    return summary;
  }, { added: 0, modified: 0, removed: 0, preserved: 0 });
  els.reviewAddedCount.textContent = String(totals.added);
  els.reviewModifiedCount.textContent = String(totals.modified);
  els.reviewRemovedCount.textContent = String(totals.removed);
  els.reviewPreservedCount.textContent = String(totals.preserved);
  const episode = state.detail?.episode;
  const status = episode?.review_status;
  els.confirmCurrentEpisode.disabled = !canConfirmEpisode(episode);
  els.confirmCurrentEpisode.textContent = status === "needs_recheck" ? "保存待复核并继续" : "确认本条并继续";
}

async function confirmCurrentEpisode() {
  const episode = state.detail?.episode;
  if (!episode) return;
  if (!["completed", "reviewed", "needs_recheck"].includes(episode.review_status)) {
    const inheritedDecision = pendingInheritedDecision(episode);
    if (!inheritedDecision) {
      toast("请先选择 Episode 结论或标记为待复核", "error");
      return;
    }
    const saved = await setDecision(inheritedDecision);
    if (!saved) return;
  }
  const currentIndex = state.filteredEpisodes.findIndex((item) => item.id === state.currentEpisodeId);
  const next = state.filteredEpisodes[currentIndex + 1];
  if (next) openEpisode(next.id);
  else toast("当前筛选范围内的 Episode 已全部确认", "success");
}

function updateEpisodeFromDetail() {
  const index = state.episodes.findIndex((item) => item.id === state.currentEpisodeId);
  if (index >= 0) state.episodes[index] = { ...state.episodes[index], ...state.detail.episode };
  renderEpisodeList();
  refreshTaskSummaries();
}

function episodeReviewStatusName(episode) {
  if (episode.review_status === "needs_recheck") return "待复核";
  const hasPrevious = Boolean(episode.previous_review);
  const changed = Number(episode.incremental_added_count || 0)
    + Number(episode.incremental_modified_count || 0)
    + Number(episode.incremental_removed_count || 0) > 0;
  if (["completed", "reviewed"].includes(episode.review_status) && hasPrevious) return "本轮确认完成";
  if (changed) return "本轮已修改";
  if (episode.review_status === "unreviewed" && hasPrevious) return "已继承待确认";
  return reviewStatusName(episode.review_status);
}

async function refreshTaskSummaries() {
  try {
    const payload = await window.episodeQc.getTasks();
    state.tasks = payload.tasks || [];
    state.currentTask = state.tasks.find((item) => item.id === state.currentTaskId) || state.currentTask;
    renderTaskContext();
  } catch { /* Episode 已保存，任务摘要稍后刷新即可 */ }
}

const playheadSaves = new Map();
async function savePlayhead() {
  if (!state.currentEpisodeId) return;
  const payload = { episodeId: state.currentEpisodeId, playheadNs: Math.round(state.playheadNs), reviewer: els.reviewerName.value.trim() };
  const previous = playheadSaves.get(payload.episodeId) || Promise.resolve();
  const pending = previous.then(() => window.episodeQc.updateReview(payload)).catch(() => {});
  playheadSaves.set(payload.episodeId, pending);
  await pending;
  if (playheadSaves.get(payload.episodeId) === pending) playheadSaves.delete(payload.episodeId);
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
  if (!isEgoTask() && (frame?.positions?.length || actionFrame?.jointPositions?.length) && g1Viewer.status === "loading") {
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
    viewerProfile: isEgoTask() ? "ego_omniego" : "robot_g1",
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
      const grid = selectionFrameGrid();
      if(!event.shiftKey && grid.exact){state.playing=false;calibrationPreview=null;updatePlaybackButton();seekTo(adjacentFrame(grid.frameOffsetsNs,state.playheadNs,event.key==='ArrowRight'?1:-1));return;}
      const stepNs = event.shiftKey ? 1e9 : grid.stepNs;
      seekTo(snapTimeToFrame(
        state.playheadNs + (event.key === "ArrowRight" ? 1 : -1) * stepNs,
        state.durationNs,
        grid,
      ));
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
  if (event.button !== 0 || event.target.closest("[data-annotation-id]") || !state.durationNs) return;
  const surface = event.target.closest(".annotation-lane-surface");
  if (!surface) return;
  const anchorNs = snapTimeToFrame(
    timelineTimeFromPointer(event, surface),
    state.durationNs,
    selectionFrameGrid(),
  );
  state.timelinePointer = {
    pointerId: event.pointerId,
    startX: event.clientX,
    anchorNs: Math.min(anchorNs, annotationDurationNs(state.detail?.episode)),
    playbackAnchorNs: anchorNs,
    surface,
    previousStartNs: state.selectionStartNs,
    previousEndNs: state.selectionEndNs,
  };
  state.timelineSelecting = false;
  state.timelineSurface = null;
  state.timelineAnchorNs = null;
}

function updateTimelineSelection(event) {
  const pointer = state.timelinePointer;
  if (!pointer || event.pointerId !== pointer.pointerId) return;
  if (!state.timelineSelecting) {
    if (!isTimelineDrag(pointer.startX, event.clientX)) return;
    state.timelineSelecting = true;
    state.timelineSurface = pointer.surface;
    state.timelineAnchorNs = pointer.anchorNs;
    state.selectionStartNs = pointer.anchorNs;
    state.selectionEndNs = pointer.anchorNs;
    els.annotationTrack.setPointerCapture?.(event.pointerId);
  }
  const time = snapTimeToFrame(
    timelineTimeFromPointer(event, state.timelineSurface),
    annotationDurationNs(state.detail?.episode),
    selectionFrameGrid(),
  );
  state.selectionStartNs = Math.min(state.timelineAnchorNs, time);
  state.selectionEndNs = Math.max(state.timelineAnchorNs, time);
  renderSelection();
}

function endTimelineSelection(event) {
  const pointer = state.timelinePointer;
  if (!pointer || event.pointerId !== pointer.pointerId) return;
  const wasSelecting = state.timelineSelecting;
  if (wasSelecting) updateTimelineSelection(event);
  state.timelinePointer = null;
  state.timelineSelecting = false;
  state.timelineAnchorNs = null;
  state.timelineSurface = null;
  if (!wasSelecting) {
    seekTo(pointer.playbackAnchorNs);
    return;
  }
  if (state.selectionEndNs === state.selectionStartNs) {
    const selection = singleFrameRange({
      timeNs: state.selectionStartNs,
      durationNs: annotationDurationNs(state.detail?.episode),
      grid: selectionFrameGrid(),
    });
    state.selectionStartNs = selection.startNs;
    state.selectionEndNs = selection.endNs;
  }
  renderSelection();
}

function cancelTimelineSelection(event) {
  const pointer = state.timelinePointer;
  if (!pointer || event.pointerId !== pointer.pointerId) return;
  if (state.timelineSelecting) {
    state.selectionStartNs = pointer.previousStartNs;
    state.selectionEndNs = pointer.previousEndNs;
    renderSelection();
  }
  state.timelinePointer = null;
  state.timelineSelecting = false;
  state.timelineAnchorNs = null;
  state.timelineSurface = null;
}

function timelineTimeFromPointer(event, surface = null) {
  const target = surface || event.target.closest?.(".annotation-lane-surface") || els.annotationTrack.querySelector(".annotation-lane-surface");
  const rect = target?.getBoundingClientRect() || els.annotationTrack.getBoundingClientRect();
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
    motion_safety: "动作与安全",
    action: "动作",
    pose_quality: "Pose 质量",
    camera_quality: "相机质量",
    exception: "意外与恢复",
    object_state: "物品状态",
    other: "其他"
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
    pelvis: "骨盆",
    left_hip: "左髋",
    right_hip: "右髋",
    spine1: "腰部",
    left_knee: "左膝",
    right_knee: "右膝",
    spine2: "胸腰",
    left_ankle: "左踝",
    right_ankle: "右踝",
    spine3: "胸部",
    left_foot: "左脚",
    right_foot: "右脚",
    neck: "颈部",
    left_collar: "左锁骨",
    right_collar: "右锁骨",
    head: "头部",
    left_shoulder: "左肩",
    right_shoulder: "右肩",
    left_elbow: "左肘",
    right_elbow: "右肘",
    left_wrist: "左腕",
    right_wrist: "右腕",
    left_hand: "左手",
    right_hand: "右手",
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
  const item = document.createElement("div"); item.className = `toast ${kind}`; item.textContent = message;
  const stack = els.taskCenter?.open && els.taskCenterToastStack ? els.taskCenterToastStack : els.toastStack;
  stack.appendChild(item);
  setTimeout(() => item.remove(), timeout);
}
function formatClock(ns) {
  const milliseconds = Math.max(0, Number(ns) || 0) / 1e6;
  const minutes = Math.floor(milliseconds / 60000); const seconds = Math.floor(milliseconds / 1000) % 60; const millis = Math.floor(milliseconds % 1000);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}
function formatSeconds(ns) { return `${(Math.max(0, Number(ns) || 0) / 1e9).toFixed(3)}s`; }
function formatFrameRange(range) {
  if (!range) return "";
  if (!range.count) return `${range.exact ? "" : "约"}0帧`;
  const prefix = range.exact ? "" : "≈";
  const frames = range.startNumber === range.endNumber ? `F${range.startNumber}` : `F${range.startNumber}–F${range.endNumber}`;
  return `${prefix}${frames}（${range.count}帧）`;
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
function taskKindName(taskKind) {
  return taskKind === "ego_omniego" ? "Ego" : "Episode";
}
function flowJobAssetTypeName(job) {
  if (job?.viewer_profile === "ego_omniego" || job?.asset_type === "egocentric") return "Ego";
  if (job?.viewer_profile === "mocap" || job?.asset_type === "mocap") return "动作捕捉";
  return "Robot Teleoperation";
}
function isEgoTask() {
  return (state.detail?.episode?.task_kind || state.currentTask?.task_kind) === "ego_omniego";
}
function setMotionControlsExpanded(expanded) {
  els.motionControlPanel.hidden = !expanded;
  els.motionControlsToggle.setAttribute("aria-expanded", String(expanded));
  els.motionControlsToggle.textContent = expanded ? "收起设置 ▴" : "视图设置 ▾";
  els.motionControlsToggle.title = expanded ? "收起动作源和标注范围" : "展开动作源和标注范围";
}
function renderViewerProfile() {
  const ego = isEgoTask();
  const labelSection = els.egoAnnotationFields.closest(".label-section");
  const egoModeChanged = labelSection?.classList.contains("ego-mode") !== ego;
  labelSection?.classList.toggle("ego-mode", ego);
  if (labelSection && egoModeChanged) labelSection.scrollTop = 0;
  els.motionViewerTitle.textContent = ego ? "人体 Pose 骨架" : "宇树 G1 29DOF";
  els.motionViewerBadge.textContent = ego ? "SMPL 24" : "URDF";
  els.motionHint.textContent = ego
    ? "读取 /dohc/skeleton · 24 关节全身 Pose · 下拉或点击名称选关节 · 拖动旋转 · 滚轮缩放"
    : "G1 关节角直接驱动 · 可切换实际执行/目标/重定向姿态 · 下拉或点击名称选关节 · 拖动旋转 · 滚轮缩放";
  els.motionSource.closest("label")?.classList.toggle("ego-pose-source", ego);
  els.motionCard.classList.toggle("ego-profile", ego);
  els.egoAnnotationFields.hidden = !ego;
  els.motionSource.title = ego ? "人体 Pose 来源" : "选择 G1 动作源";
  els.motionSource.setAttribute("aria-label", els.motionSource.title);
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
function compactSourcePath(value) {
  const original = String(value || "");
  if (!original || !/[\\/]/.test(original)) return original;
  const normalized = original.replace(/\\/g, "/").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return normalized;
  return `…/${parts.slice(-2).join("/")}`;
}

function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]); }

initialize();
