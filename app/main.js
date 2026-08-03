const { app, BrowserWindow, dialog, ipcMain } = require("electron");
const { spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const {
  buildAnnotationIndexArgs,
  buildFrameExportArgs,
  buildUvArgs,
  findUvExecutable,
  isImageAnomalyDetectionEnabled,
  normalizeCandidatePaths
} = require("./services/pythonBackend");
const {
  ACTION_FRAME_ENCODING,
  MOTION_FRAME_ENCODING,
  decodeMotionFrame,
  decodeRobotActionFrame
} = require("./services/playbackBinary");

// Ubuntu's XDG FileChooser portal v3 can immediately cancel Electron folder
// dialogs without user input. Requiring portal v4 makes Electron fall back to
// the native GTK/KDE chooser on those systems. Portal v4+ remains supported.
if (process.platform === "linux") {
  app.commandLine.appendSwitch("xdg-portal-required-version", "4");
}

let mainWindow;
const playbackManifests = new Map();
const playbackPreparationJobs = new Map();
let pendingLabelSchemaPath = null;
let playbackPreparationQueue = Promise.resolve();
let episodeQcWorker = null;
let workerStdoutBuffer = "";
let workerStderrBuffer = "";
let workerRequestId = 0;
const workerRequests = new Map();

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 980,
    minHeight: 680,
    title: "Episode QC",
    backgroundColor: "#f7f7f2",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true
    }
  });

  mainWindow.loadFile(path.join(__dirname, "renderer", "index.html"));
}

app.whenReady().then(() => {
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  stopEpisodeQcWorker();
});

ipcMain.handle("episode:selectMcap", async () => {
  return selectOpenFile({
    title: "Select episode.mcap",
    filters: [{ name: "MCAP", extensions: ["mcap"] }]
  });
});

ipcMain.handle("episode:selectFolder", async () => {
  return selectDirectory("Select episode folder");
});

ipcMain.handle("episode:listTopics", async (_event, mcapPath) => {
  assertMcapPath(mcapPath);
  const { stdout } = await runEpisodeQc(["topics", mcapPath]);
  return stdout
    .trim()
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      const match = line.trim().match(/^(\d+)\s+(.+)$/);
      if (!match) {
        return null;
      }
      return {
        messageCount: Number(match[1]),
        name: match[2]
      };
    })
    .filter(Boolean);
});

ipcMain.handle("episode:indexAnnotationFolder", async (_event, folderPath) => {
  assertAnnotationRootPath(folderPath);
  const { stdout } = await runEpisodeQc(buildAnnotationIndexArgs(folderPath));
  return JSON.parse(stdout);
});

ipcMain.handle("episode:exportAnnotationFrame", async (_event, request) => {
  assertAnnotationFrameRequest(request);
  const outputPath = makeAnnotationFramePath(request);
  const { stdout } = await runEpisodeQc(
    buildFrameExportArgs({
      mcapPath: request.mcapPath,
      topic: request.topic,
      frameIndex: request.frameIndex,
      outputPath
    })
  );
  const result = JSON.parse(stdout);
  result.frame_url = pathToFileURL(result.output_path).href;
  return result;
});

if (isImageAnomalyDetectionEnabled()) {
  ipcMain.handle("episode:scanStaleRegions", async (_event, request) => {
    assertScanTarget(request);

    const isFolder = request.mode === "folder";
    const inputPath = isFolder ? request.folderPath : request.mcapPath;
    const exportDir = makeSnapshotDir(inputPath);
    const args = buildUvArgs({
      command: isFolder ? "scan-folder" : "detect-stale-region",
      mcapPath: request.mcapPath,
      folderPath: request.folderPath,
      topics: request.topics,
      detector: request.detector,
      threshold: request.threshold,
      limit: request.limit,
      historySize: request.historySize,
      minChange: request.minChange,
      maxStaleDelta: request.maxStaleDelta,
      maxPersistenceFrames: request.maxPersistenceFrames,
      jobs: request.jobs,
      resize: request.resize,
      exportDir
    });

    const { stdout } = await runEpisodeQc(args);
    const result = JSON.parse(stdout);
    normalizeCandidatePaths(result, pathToFileURL);
    result.exportDir = exportDir;
    return result;
  });
}

ipcMain.handle("episode:saveReport", async (_event, report) => {
  const defaultPath = path.join(
    path.dirname(report?.mcapPath || app.getPath("documents")),
    "qc_report.json"
  );
  const result = await dialog.showSaveDialog(mainWindow, {
    title: "Save QC report",
    defaultPath,
    filters: [{ name: "JSON", extensions: ["json"] }]
  });

  if (result.canceled || !result.filePath) {
    return null;
  }

  fs.writeFileSync(result.filePath, JSON.stringify(report, null, 2) + "\n", "utf-8");
  return result.filePath;
});

ipcMain.handle("episode:saveAnnotations", async (_event, report) => {
  const defaultPath = path.join(
    report?.rootPath || app.getPath("documents"),
    "frame_annotations.json"
  );
  const result = await dialog.showSaveDialog(mainWindow, {
    title: "Save frame annotations",
    defaultPath,
    filters: [{ name: "JSON", extensions: ["json"] }]
  });

  if (result.canceled || !result.filePath) {
    return null;
  }

  fs.writeFileSync(result.filePath, JSON.stringify(report, null, 2) + "\n", "utf-8");
  return result.filePath;
});

ipcMain.handle("workspace:getState", async () => {
  const paths = workspacePaths();
  await runEpisodeQc(["workspace-init", paths.dbPath]);
  let state = JSON.parse((await runEpisodeQc(["workspace-state", paths.dbPath])).stdout);
  if (!state.label_schema && fs.existsSync(paths.defaultLabelSchema)) {
    await runEpisodeQc(["label-schema-import", paths.dbPath, paths.defaultLabelSchema]);
    state = JSON.parse((await runEpisodeQc(["workspace-state", paths.dbPath])).stdout);
  }
  return state;
});

ipcMain.handle("workspace:updateSettings", async (_event, request) => {
  const paths = workspacePaths();
  const args = ["workspace-settings", paths.dbPath];
  if (typeof request?.name === "string") {
    args.push("--name", request.name);
  }
  if (typeof request?.reviewer === "string") {
    args.push("--reviewer", request.reviewer);
  }
  if (typeof request?.lastEpisodeId === "string") {
    assertEntityId(request.lastEpisodeId, "ep");
    args.push("--last-episode-id", request.lastEpisodeId);
  }
  return JSON.parse((await runEpisodeQc(args)).stdout);
});

ipcMain.handle("workspace:addSource", async () => {
  const rootPath = await selectDirectory("选择包含 Episode 的数据根目录");
  if (!rootPath) {
    return null;
  }
  return scanWorkspaceSource(rootPath);
});

ipcMain.handle("workspace:addSourcePath", async (_event, rootPath) => {
  return scanWorkspaceSource(rootPath);
});

async function scanWorkspaceSource(rootPath) {
  if (typeof rootPath !== "string" || !rootPath.trim()) {
    throw new Error("请选择数据目录或输入 smb:// NAS 地址");
  }
  const paths = workspacePaths();
  const args = ["workspace-scan", paths.dbPath, rootPath.trim()];
  if (fs.existsSync(paths.defaultProfile)) {
    args.push("--profile", paths.defaultProfile);
  }
  return JSON.parse((await runEpisodeQc(args)).stdout);
}

ipcMain.handle("workspace:getEpisode", async (_event, episodeId) => {
  assertEntityId(episodeId, "ep");
  const paths = workspacePaths();
  return JSON.parse((await runEpisodeQc(["workspace-episode", paths.dbPath, episodeId])).stdout);
});

ipcMain.handle("workspace:prepareEpisode", async (_event, episodeId) => {
  assertEntityId(episodeId, "ep");
  const paths = workspacePaths();
  const result = JSON.parse(
    (await runEpisodeQc(["workspace-prepare", paths.dbPath, episodeId, paths.cacheRoot, "--mode", "priority"])).stdout
  );
  playbackManifests.set(episodeId, loadPlaybackManifest(result.manifest_path, paths.cacheRoot));
  if (!result.complete) {
    queueFullEpisodeCache(paths, episodeId);
  }
  return result;
});

function queueFullEpisodeCache(paths, episodeId) {
  if (playbackPreparationJobs.has(episodeId)) return;
  const job = playbackPreparationQueue.then(async () => {
    const result = JSON.parse(
      (await runEpisodeQcOneShot([
        "workspace-prepare",
        paths.dbPath,
        episodeId,
        paths.cacheRoot,
        "--mode",
        "full"
      ])).stdout
    );
    playbackManifests.set(episodeId, loadPlaybackManifest(result.manifest_path, paths.cacheRoot));
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("workspace:episodeCacheReady", { episodeId, cache: result });
    }
  });
  playbackPreparationJobs.set(episodeId, job);
  playbackPreparationQueue = job.catch(() => {});
  job.catch((error) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("workspace:episodeCacheReady", {
        episodeId,
        error: error.message || String(error)
      });
    }
  }).finally(() => {
    playbackPreparationJobs.delete(episodeId);
  });
}

ipcMain.handle("workspace:getCameraFrame", async (_event, request) => {
  const manifest = playbackManifestFor(request, "streamId");
  const camera = manifest.value.cameras.find((item) => item.stream_id === request.streamId);
  if (!camera || !Array.isArray(camera.index) || camera.index.length === 0) {
    throw new Error(`相机缓存不存在: ${request.streamId}`);
  }
  const entry = nearestIndexEntry(camera.index, request.timeNs);
  const framePath = resolveCacheFile(manifest.path, camera.frames_file, manifest.cacheRoot);
  const bytes = readFileSlice(framePath, entry[1], entry[2]);
  return {
    dataUrl: `data:image/jpeg;base64,${bytes.toString("base64")}`,
    frameOffsetNs: entry[0],
    skewNs: entry[0] - Number(request.timeNs),
    frameIndex: entry[3],
    endOfStream: entry[3] === camera.index.length - 1
  };
});

ipcMain.handle("workspace:getMotionFrame", async (_event, request) => {
  const manifest = playbackManifestFor(request);
  const motion = manifest.value.motion;
  if (!motion?.available || !Array.isArray(motion.index) || motion.index.length === 0) {
    return null;
  }
  const entry = nearestIndexEntry(motion.index, request.timeNs);
  const framePath = resolveCacheFile(manifest.path, motion.frames_file, manifest.cacheRoot);
  const bytes = readFileSlice(framePath, entry[1], entry[2]);
  const frame = motion.frame_encoding === MOTION_FRAME_ENCODING
    ? decodeMotionFrame(bytes, motion.joint_names.length)
    : JSON.parse(bytes.toString("utf-8"));
  return {
    ...frame,
    frameOffsetNs: entry[0],
    skewNs: entry[0] - Number(request.timeNs),
    frameIndex: entry[3],
    jointNames: motion.joint_names,
    parentIndices: motion.parent_indices,
    coordinateFrame: motion.coordinate_frame,
    units: motion.units
  };
});

ipcMain.handle("workspace:getRobotActionFrame", async (_event, request) => {
  const manifest = playbackManifestFor(request);
  if (!/^(policy|policy_target|soma)$/.test(request?.sourceKey || "")) {
    throw new Error("机器人动作源无效");
  }
  const robotActions = manifest.value.robot_actions;
  const source = robotActions?.sources?.find((item) => item.key === request.sourceKey);
  if (!source?.available || !Array.isArray(source.index) || source.index.length === 0) {
    return null;
  }
  const entry = nearestIndexEntry(source.index, request.timeNs);
  const framePath = resolveCacheFile(manifest.path, source.frames_file, manifest.cacheRoot);
  const bytes = readFileSlice(framePath, entry[1], entry[2]);
  const frame = source.frame_encoding === ACTION_FRAME_ENCODING
    ? decodeRobotActionFrame(bytes, request.sourceKey)
    : JSON.parse(bytes.toString("utf-8"));
  return {
    ...frame,
    jointPositions: frame.joint_positions,
    rootPosition: frame.root_position,
    rootQuaternionWxyz: frame.root_quaternion_wxyz,
    frameOffsetNs: entry[0],
    skewNs: entry[0] - Number(request.timeNs),
    frameIndex: entry[3],
    endOfStream: entry[3] === source.index.length - 1,
    jointNames: robotActions.joint_names
  };
});

ipcMain.handle("workspace:importLabelSchema", async () => {
  const schemaPath = await selectOpenFile({
    title: "导入标签库",
    filters: [{ name: "标签库", extensions: ["yaml", "yml", "json", "csv"] }]
  });
  if (!schemaPath) {
    pendingLabelSchemaPath = null;
    return null;
  }
  const paths = workspacePaths();
  const preview = JSON.parse((await runEpisodeQc(["label-schema-preview", paths.dbPath, schemaPath])).stdout);
  if (!preview.valid) {
    pendingLabelSchemaPath = null;
    return { imported: false, preview };
  }
  pendingLabelSchemaPath = schemaPath;
  return { imported: false, readyToConfirm: true, preview };
});

ipcMain.handle("workspace:confirmLabelSchema", async () => {
  if (!pendingLabelSchemaPath) {
    throw new Error("没有待确认的标签库导入");
  }
  const paths = workspacePaths();
  const schemaPath = pendingLabelSchemaPath;
  pendingLabelSchemaPath = null;
  return JSON.parse((await runEpisodeQc(["label-schema-import", paths.dbPath, schemaPath])).stdout);
});

ipcMain.handle("workspace:saveAnnotation", async (_event, request) => {
  const paths = workspacePaths();
  const args = ["annotation-save", paths.dbPath, "--payload", JSON.stringify(request.payload), "--session-id", "desktop"];
  if (request.annotationId) {
    assertEntityId(request.annotationId, "ann");
    args.push("--annotation-id", request.annotationId);
  }
  return JSON.parse((await runEpisodeQc(args)).stdout);
});

ipcMain.handle("workspace:deleteAnnotation", async (_event, annotationId) => {
  assertEntityId(annotationId, "ann");
  const paths = workspacePaths();
  return JSON.parse((await runEpisodeQc(["annotation-delete", paths.dbPath, annotationId, "--session-id", "desktop"])).stdout);
});

ipcMain.handle("workspace:undo", async () => {
  const paths = workspacePaths();
  return JSON.parse((await runEpisodeQc(["annotation-undo", paths.dbPath, "--session-id", "desktop"])).stdout);
});

ipcMain.handle("workspace:redo", async () => {
  const paths = workspacePaths();
  return JSON.parse((await runEpisodeQc(["annotation-redo", paths.dbPath, "--session-id", "desktop"])).stdout);
});

ipcMain.handle("workspace:updateReview", async (_event, request) => {
  assertEntityId(request?.episodeId, "ep");
  const paths = workspacePaths();
  const args = ["episode-review", paths.dbPath, request.episodeId];
  if (request.status) args.push("--status", request.status);
  if (request.decision) args.push("--decision", request.decision);
  if (typeof request.reviewer === "string") args.push("--reviewer", request.reviewer);
  if (Number.isFinite(request.playheadNs)) args.push("--playhead-ns", String(Math.round(request.playheadNs)));
  return JSON.parse((await runEpisodeQc(args)).stdout);
});

ipcMain.handle("workspace:export", async (_event, request) => {
  let outputParent = request?.outputParent || process.env.EPISODE_QC_EXPORT_ROOT;
  if (outputParent) {
    assertFolderPath(outputParent);
  } else {
    outputParent = await selectDirectory("选择导出目录");
    if (!outputParent) {
      return null;
    }
  }
  const paths = workspacePaths();
  const args = ["workspace-export", paths.dbPath, outputParent];
  for (const episodeId of request?.episodeIds || []) {
    assertEntityId(episodeId, "ep");
    args.push("--episode-id", episodeId);
  }
  if (request?.completedOnly) args.push("--completed-only");
  return JSON.parse((await runEpisodeQc(args)).stdout);
});

function assertMcapPath(mcapPath) {
  if (typeof mcapPath !== "string" || !mcapPath.endsWith(".mcap")) {
    throw new Error("Please select an .mcap file.");
  }
  if (!fs.existsSync(mcapPath)) {
    throw new Error(`MCAP file does not exist: ${mcapPath}`);
  }
}

function assertFolderPath(folderPath) {
  if (typeof folderPath !== "string") {
    throw new Error("Please select a folder.");
  }
  if (!fs.existsSync(folderPath) || !fs.statSync(folderPath).isDirectory()) {
    throw new Error(`Folder does not exist: ${folderPath}`);
  }
}

function assertAnnotationRootPath(rootPath) {
  if (typeof rootPath !== "string") {
    throw new Error("Please select a folder or .mcap file.");
  }
  if (!fs.existsSync(rootPath)) {
    throw new Error(`Path does not exist: ${rootPath}`);
  }
  const stat = fs.statSync(rootPath);
  if (!stat.isDirectory() && !(stat.isFile() && rootPath.endsWith(".mcap"))) {
    throw new Error("Please select a folder or .mcap file.");
  }
}

function assertScanTarget(request) {
  if (request?.mode === "folder") {
    assertFolderPath(request.folderPath);
  } else {
    assertMcapPath(request?.mcapPath);
  }
}

function assertAnnotationFrameRequest(request) {
  assertMcapPath(request?.mcapPath);
  if (typeof request.topic !== "string" || request.topic.length === 0) {
    throw new Error("Please select an image topic.");
  }
  if (!Number.isInteger(request.frameIndex) || request.frameIndex < 0) {
    throw new Error("Frame index must be a non-negative integer.");
  }
}

function makeSnapshotDir(inputPath) {
  const isMcap = path.extname(inputPath) === ".mcap";
  const episodeName = isMcap ? path.basename(path.dirname(inputPath)) : path.basename(inputPath);
  const runId = new Date().toISOString().replace(/[:.]/g, "-");
  return path.join(app.getPath("userData"), "candidate-frames", episodeName, runId);
}

function makeAnnotationFramePath({ mcapPath, topic, frameIndex }) {
  const episodeName = safePathPart(path.basename(path.dirname(mcapPath)));
  const topicKey = crypto.createHash("sha1").update(`${mcapPath}\0${topic}`).digest("hex").slice(0, 16);
  const frameName = `frame_${String(frameIndex).padStart(6, "0")}.jpg`;
  return path.join(app.getPath("userData"), "annotation-frames", episodeName, topicKey, frameName);
}

function safePathPart(value) {
  return String(value).replace(/[^a-zA-Z0-9._-]+/g, "_") || "item";
}

async function selectDirectory(title) {
  if (canUseZenity()) {
    return runZenity(["--file-selection", "--directory", `--title=${title}`]);
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    title,
    properties: ["openDirectory", "createDirectory"]
  });
  return result.canceled || result.filePaths.length === 0 ? null : result.filePaths[0];
}

async function selectOpenFile({ title, filters = [] }) {
  if (canUseZenity()) {
    const args = ["--file-selection", `--title=${title}`];
    for (const filter of filters) {
      const patterns = (filter.extensions || []).map((extension) => `*.${extension}`).join(" ");
      if (patterns) args.push(`--file-filter=${filter.name} | ${patterns}`);
    }
    return runZenity(args);
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    title,
    properties: ["openFile"],
    filters
  });
  return result.canceled || result.filePaths.length === 0 ? null : result.filePaths[0];
}

function canUseZenity() {
  return process.platform === "linux" && fs.existsSync("/usr/bin/zenity");
}

function runZenity(args) {
  return new Promise((resolve, reject) => {
    const child = spawn("/usr/bin/zenity", args, {
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve(stdout.trim() || null);
      } else if (code === 1 || code === 5) {
        resolve(null);
      } else {
        reject(new Error(stderr.trim() || `目录选择器退出，状态码 ${code}`));
      }
    });
  });
}

function workspacePaths() {
  const configuredRoot = process.env.EPISODE_QC_WORKSPACE_ROOT;
  const root = configuredRoot ? path.resolve(configuredRoot) : path.join(app.getPath("userData"), "workspaces", "default");
  return {
    dbPath: path.join(root, "workspace.db"),
    cacheRoot: path.join(root, "cache"),
    defaultProfile: path.join(app.getAppPath(), "mocap_qc_v1_design_bundle", "data_profile_v1.example.yaml"),
    defaultLabelSchema: path.join(app.getAppPath(), "mocap_qc_v1_design_bundle", "label_schema_v1.example.yaml")
  };
}

function assertEntityId(value, prefix) {
  if (typeof value !== "string" || !new RegExp(`^${prefix}_[a-f0-9]{24,32}$`).test(value)) {
    throw new Error(`无效的 ${prefix} ID`);
  }
}

function loadPlaybackManifest(manifestPath, cacheRoot) {
  const resolvedRoot = path.resolve(cacheRoot);
  const resolved = path.resolve(manifestPath);
  if (!resolved.startsWith(`${resolvedRoot}${path.sep}`) || path.basename(resolved) !== "stream_index.json") {
    throw new Error("播放缓存路径超出工作区");
  }
  return { value: JSON.parse(fs.readFileSync(resolved, "utf-8")), path: resolved, cacheRoot: resolvedRoot };
}

function playbackManifestFor(request, streamKey = null) {
  assertEntityId(request?.episodeId, "ep");
  if (!Number.isFinite(request?.timeNs) || request.timeNs < 0) {
    throw new Error("播放时间必须为非负数");
  }
  if (streamKey) {
    assertEntityId(request?.[streamKey], "str");
  }
  const manifest = playbackManifests.get(request.episodeId);
  if (!manifest) {
    throw new Error("请先准备 Episode 播放缓存");
  }
  return manifest;
}

function nearestIndexEntry(entries, timeNs) {
  const target = Number(timeNs);
  let low = 0;
  let high = entries.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (entries[middle][0] < target) low = middle + 1;
    else high = middle;
  }
  if (low === 0) return entries[0];
  if (low >= entries.length) return entries[entries.length - 1];
  return target - entries[low - 1][0] <= entries[low][0] - target ? entries[low - 1] : entries[low];
}

function resolveCacheFile(manifestPath, relativePath, cacheRoot) {
  const resolved = path.resolve(path.dirname(manifestPath), relativePath);
  if (!resolved.startsWith(`${cacheRoot}${path.sep}`)) {
    throw new Error("播放帧路径超出工作区缓存");
  }
  return resolved;
}

function readFileSlice(filePath, offset, length) {
  if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(length) || length <= 0) {
    throw new Error("播放帧索引无效");
  }
  const descriptor = fs.openSync(filePath, "r");
  try {
    const bytes = Buffer.allocUnsafe(length);
    const read = fs.readSync(descriptor, bytes, 0, length, offset);
    if (read !== length) throw new Error("播放帧缓存读取不完整");
    return bytes;
  } finally {
    fs.closeSync(descriptor);
  }
}

function runEpisodeQc(args) {
  return new Promise((resolve, reject) => {
    const worker = ensureEpisodeQcWorker();
    const id = ++workerRequestId;
    workerRequests.set(id, { resolve, reject });
    worker.stdin.write(`${JSON.stringify({ id, args })}\n`, (error) => {
      if (!error) return;
      workerRequests.delete(id);
      reject(error);
    });
  });
}

function ensureEpisodeQcWorker() {
  if (episodeQcWorker && !episodeQcWorker.killed) {
    return episodeQcWorker;
  }

  const uv = findUvExecutable();
  const child = spawn(uv, ["run", "episode-qc", "worker"], {
    cwd: app.getAppPath(),
    env: {
      ...process.env,
      UV_CACHE_DIR: path.join(app.getAppPath(), ".uv-cache")
    },
    stdio: ["pipe", "pipe", "pipe"]
  });
  episodeQcWorker = child;
  workerStdoutBuffer = "";
  workerStderrBuffer = "";
  child.stdout.setEncoding("utf-8");
  child.stderr.setEncoding("utf-8");
  child.stdout.on("data", handleWorkerStdout);
  child.stderr.on("data", (chunk) => {
    workerStderrBuffer = `${workerStderrBuffer}${chunk}`.slice(-16_384);
  });
  child.on("error", (error) => failEpisodeQcWorker(child, error));
  child.on("close", (code) => {
    failEpisodeQcWorker(
      child,
      new Error(workerStderrBuffer.trim() || `episode-qc worker exited with code ${code}`)
    );
  });
  return child;
}

function handleWorkerStdout(chunk) {
  workerStdoutBuffer += chunk;
  while (true) {
    const newline = workerStdoutBuffer.indexOf("\n");
    if (newline < 0) return;
    const line = workerStdoutBuffer.slice(0, newline);
    workerStdoutBuffer = workerStdoutBuffer.slice(newline + 1);
    if (!line.trim()) continue;
    let response;
    try {
      response = JSON.parse(line);
    } catch (error) {
      failEpisodeQcWorker(episodeQcWorker, new Error(`Python worker 返回了无效 JSON: ${error.message}`));
      return;
    }
    const pending = workerRequests.get(response.id);
    if (!pending) continue;
    workerRequests.delete(response.id);
    if (response.ok) {
      pending.resolve({ stdout: response.stdout || "", stderr: response.stderr || "" });
    } else {
      pending.reject(new Error((response.stderr || response.error || "Python worker 命令失败").trim()));
    }
  }
}

function failEpisodeQcWorker(child, error) {
  if (episodeQcWorker !== child) return;
  episodeQcWorker = null;
  workerStdoutBuffer = "";
  if (!child.killed) child.kill("SIGTERM");
  for (const pending of workerRequests.values()) {
    pending.reject(error);
  }
  workerRequests.clear();
}

function stopEpisodeQcWorker() {
  const child = episodeQcWorker;
  episodeQcWorker = null;
  if (!child) return;
  child.stdin.end();
  if (!child.killed) child.kill("SIGTERM");
}

function runEpisodeQcOneShot(args) {
  return new Promise((resolve, reject) => {
    const uv = findUvExecutable();
    const child = spawn(uv, ["run", "episode-qc", ...args], {
      cwd: app.getAppPath(),
      env: {
        ...process.env,
        UV_CACHE_DIR: path.join(app.getAppPath(), ".uv-cache")
      },
      stdio: ["ignore", "pipe", "pipe"]
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve({ stdout, stderr });
      } else {
        reject(new Error(stderr.trim() || `episode-qc exited with code ${code}`));
      }
    });
  });
}
