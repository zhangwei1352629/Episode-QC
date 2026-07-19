const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("episodeQc", {
  selectMcap: () => ipcRenderer.invoke("episode:selectMcap"),
  selectFolder: () => ipcRenderer.invoke("episode:selectFolder"),
  listTopics: (mcapPath) => ipcRenderer.invoke("episode:listTopics", mcapPath),
  indexAnnotationFolder: (folderPath) => ipcRenderer.invoke("episode:indexAnnotationFolder", folderPath),
  exportAnnotationFrame: (request) => ipcRenderer.invoke("episode:exportAnnotationFrame", request),
  scanStaleRegions: (request) => ipcRenderer.invoke("episode:scanStaleRegions", request),
  saveReport: (report) => ipcRenderer.invoke("episode:saveReport", report),
  saveAnnotations: (report) => ipcRenderer.invoke("episode:saveAnnotations", report)
});
