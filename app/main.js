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
  normalizeCandidatePaths
} = require("./services/pythonBackend");

let mainWindow;

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
      sandbox: false
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

ipcMain.handle("episode:selectMcap", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Select episode.mcap",
    properties: ["openFile"],
    filters: [{ name: "MCAP", extensions: ["mcap"] }]
  });

  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }

  return result.filePaths[0];
});

ipcMain.handle("episode:selectFolder", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "Select episode folder",
    properties: ["openDirectory"]
  });

  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }

  return result.filePaths[0];
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

function runEpisodeQc(args) {
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
