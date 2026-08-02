const fs = require("node:fs");
const path = require("node:path");

function isImageAnomalyDetectionEnabled(environment = process.env) {
  const value = String(environment?.EPISODE_QC_ENABLE_IMAGE_DETECTION || "").trim().toLowerCase();
  return ["1", "true", "yes", "on"].includes(value);
}

function buildUvArgs({
  command,
  mcapPath,
  folderPath,
  topics = [],
  detector = "camera-tearing",
  threshold = 0.72,
  limit,
  historySize = 3,
  minChange = 0.08,
  maxStaleDelta = 0.035,
  maxPersistenceFrames = 12,
  gapWindow = 12,
  jobs,
  resize = "160x90",
  exportDir
}) {
  if (command !== "detect-stale-region" && command !== "scan-folder") {
    throw new Error(`Unsupported command: ${command}`);
  }

  const inputPath = command === "scan-folder" ? folderPath : mcapPath;
  const args = [
    command,
    inputPath,
    "--detector",
    detector,
    "--threshold",
    String(threshold),
    "--history-size",
    String(historySize),
    "--min-change",
    String(minChange),
    "--max-stale-delta",
    String(maxStaleDelta),
    "--max-persistence-frames",
    String(maxPersistenceFrames),
    "--gap-window",
    String(gapWindow),
    "--resize",
    resize,
    "--json",
    "-"
  ];

  for (const topic of topics || []) {
    args.push("--topic", topic);
  }

  if (Number.isInteger(limit) && limit > 0) {
    args.push("--limit", String(limit));
  }

  if (command === "scan-folder" && Number.isInteger(jobs) && jobs > 0) {
    args.push("--jobs", String(jobs));
  }

  if (exportDir) {
    args.push("--export-dir", exportDir);
  }

  return args;
}

function buildAnnotationIndexArgs(folderPath) {
  return ["index-folder", folderPath, "--json", "-"];
}

function buildFrameExportArgs({ mcapPath, topic, frameIndex, outputPath }) {
  return [
    "export-frame",
    mcapPath,
    "--topic",
    topic,
    "--frame",
    String(frameIndex),
    "--output",
    outputPath,
    "--json",
    "-"
  ];
}

function findUvExecutable() {
  const candidates = [
    process.env.UV_BIN,
    path.join(process.env.HOME || "", ".local", "bin", "uv"),
    "uv"
  ].filter(Boolean);

  for (const candidate of candidates) {
    if (candidate === "uv") {
      return candidate;
    }
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }

  return "uv";
}

function normalizeCandidatePaths(result, toFileUrl) {
  const candidates = collectCandidates(result);
  if (candidates.length === 0) {
    return result;
  }

  for (const candidate of candidates) {
    if (candidate.snapshot_path) {
      candidate.snapshot_url = toFileUrl(candidate.snapshot_path).href;
    } else {
      candidate.snapshot_url = null;
    }
  }

  return result;
}

function collectCandidates(result) {
  const candidates = [];
  if (Array.isArray(result?.candidates)) {
    candidates.push(...result.candidates);
  }
  if (Array.isArray(result?.events)) {
    candidates.push(...result.events);
  }
  for (const fileResult of result?.files || []) {
    const nested = fileResult?.result?.candidates;
    if (Array.isArray(nested)) {
      candidates.push(...nested);
    }
    const nestedEvents = fileResult?.result?.events;
    if (Array.isArray(nestedEvents)) {
      candidates.push(...nestedEvents);
    }
  }
  return candidates;
}

module.exports = {
  buildAnnotationIndexArgs,
  buildFrameExportArgs,
  buildUvArgs,
  isImageAnomalyDetectionEnabled,
  findUvExecutable,
  normalizeCandidatePaths
};
