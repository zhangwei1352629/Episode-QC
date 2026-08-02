const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const preloadSource = fs.readFileSync(path.join(__dirname, "..", "preload.js"), "utf-8");

function loadPreload(environment = {}) {
  let exposedApi = null;
  const ipcRenderer = {
    invoke: () => {},
    on: () => {},
    removeListener: () => {},
  };
  const contextBridge = {
    exposeInMainWorld(name, api) {
      assert.equal(name, "episodeQc");
      exposedApi = api;
    },
  };
  const context = vm.createContext({
    process: { env: environment },
    require(moduleName) {
      assert.equal(moduleName, "electron", "sandboxed preload must not require local modules");
      return { contextBridge, ipcRenderer };
    },
  });

  vm.runInContext(preloadSource, context, { filename: "preload.js" });
  return exposedApi;
}

test("sandboxed preload exposes workspace API with image detection disabled", () => {
  const api = loadPreload();
  assert.equal(typeof api.getWorkspaceState, "function");
  assert.equal(typeof api.onEpisodeCacheReady, "function");
  assert.equal(api.scanStaleRegions, undefined);
});

test("sandboxed preload exposes image detection only when explicitly enabled", () => {
  const api = loadPreload({ EPISODE_QC_ENABLE_IMAGE_DETECTION: "1" });
  assert.equal(typeof api.scanStaleRegions, "function");
});
