const { contextBridge, ipcRenderer } = require("electron");

// Sandboxed preload scripts can only require Electron and a small set of
// built-in modules. Keep this flag check local so a disabled optional feature
// cannot prevent the entire renderer API from being exposed.
function isImageAnomalyDetectionEnabled(environment = process.env) {
  const value = String(environment?.EPISODE_QC_ENABLE_IMAGE_DETECTION || "").trim().toLowerCase();
  return ["1", "true", "yes", "on"].includes(value);
}

const episodeQcApi = {
  selectMcap: () => ipcRenderer.invoke("episode:selectMcap"),
  selectFolder: () => ipcRenderer.invoke("episode:selectFolder"),
  listTopics: (mcapPath) => ipcRenderer.invoke("episode:listTopics", mcapPath),
  indexAnnotationFolder: (folderPath) => ipcRenderer.invoke("episode:indexAnnotationFolder", folderPath),
  exportAnnotationFrame: (request) => ipcRenderer.invoke("episode:exportAnnotationFrame", request),
  saveReport: (report) => ipcRenderer.invoke("episode:saveReport", report),
  saveAnnotations: (report) => ipcRenderer.invoke("episode:saveAnnotations", report),
  getWorkspaceState: () => ipcRenderer.invoke("workspace:getState"),
  updateWorkspaceSettings: (request) => ipcRenderer.invoke("workspace:updateSettings", request),
  addSource: () => ipcRenderer.invoke("workspace:addSource"),
  addSourcePath: (rootPath) => ipcRenderer.invoke("workspace:addSourcePath", rootPath),
  getEpisode: (episodeId) => ipcRenderer.invoke("workspace:getEpisode", episodeId),
  prepareEpisode: (episodeId) => ipcRenderer.invoke("workspace:prepareEpisode", episodeId),
  getCameraFrame: (request) => ipcRenderer.invoke("workspace:getCameraFrame", request),
  getMotionFrame: (request) => ipcRenderer.invoke("workspace:getMotionFrame", request),
  getRobotActionFrame: (request) => ipcRenderer.invoke("workspace:getRobotActionFrame", request),
  onEpisodeCacheReady: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on("workspace:episodeCacheReady", listener);
    return () => ipcRenderer.removeListener("workspace:episodeCacheReady", listener);
  },
  importLabelSchema: () => ipcRenderer.invoke("workspace:importLabelSchema"),
  confirmLabelSchema: () => ipcRenderer.invoke("workspace:confirmLabelSchema"),
  saveAnnotation: (request) => ipcRenderer.invoke("workspace:saveAnnotation", request),
  deleteAnnotation: (annotationId) => ipcRenderer.invoke("workspace:deleteAnnotation", annotationId),
  undo: () => ipcRenderer.invoke("workspace:undo"),
  redo: () => ipcRenderer.invoke("workspace:redo"),
  updateReview: (request) => ipcRenderer.invoke("workspace:updateReview", request),
  exportWorkspace: (request) => ipcRenderer.invoke("workspace:export", request)
};

if (isImageAnomalyDetectionEnabled()) {
  episodeQcApi.scanStaleRegions = (request) => ipcRenderer.invoke("episode:scanStaleRegions", request);
}

contextBridge.exposeInMainWorld("episodeQc", episodeQcApi);
