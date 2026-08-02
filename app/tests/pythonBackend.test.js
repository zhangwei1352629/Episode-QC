const test = require("node:test");
const assert = require("node:assert/strict");

const {
  buildAnnotationIndexArgs,
  buildFrameExportArgs,
  buildUvArgs,
  isImageAnomalyDetectionEnabled,
  normalizeCandidatePaths
} = require("../services/pythonBackend");

test("image anomaly detection is disabled unless explicitly enabled", () => {
  assert.equal(isImageAnomalyDetectionEnabled({}), false);
  assert.equal(isImageAnomalyDetectionEnabled({ EPISODE_QC_ENABLE_IMAGE_DETECTION: "0" }), false);
  assert.equal(isImageAnomalyDetectionEnabled({ EPISODE_QC_ENABLE_IMAGE_DETECTION: "false" }), false);
  assert.equal(isImageAnomalyDetectionEnabled({ EPISODE_QC_ENABLE_IMAGE_DETECTION: "1" }), true);
  assert.equal(isImageAnomalyDetectionEnabled({ EPISODE_QC_ENABLE_IMAGE_DETECTION: "TRUE" }), true);
});

test("buildUvArgs creates stale-region detect command with repeated topics", () => {
  const args = buildUvArgs({
    command: "detect-stale-region",
    mcapPath: "/data/episode.mcap",
    topics: ["/camera/a/image/jpeg", "/camera/b/image/jpeg"],
    detector: "localized-corruption",
    threshold: 0.82,
    limit: 100,
    historySize: 4,
    minChange: 0.09,
    maxStaleDelta: 0.04,
    maxPersistenceFrames: 10,
    gapWindow: 9,
    resize: "120x80",
    exportDir: "/tmp/snaps"
  });

  assert.deepEqual(args, [
    "detect-stale-region",
    "/data/episode.mcap",
    "--detector",
    "localized-corruption",
    "--threshold",
    "0.82",
    "--history-size",
    "4",
    "--min-change",
    "0.09",
    "--max-stale-delta",
    "0.04",
    "--max-persistence-frames",
    "10",
    "--gap-window",
    "9",
    "--resize",
    "120x80",
    "--json",
    "-",
    "--topic",
    "/camera/a/image/jpeg",
    "--topic",
    "/camera/b/image/jpeg",
    "--limit",
    "100",
    "--export-dir",
    "/tmp/snaps"
  ]);
});

test("buildUvArgs creates folder scan command with jobs", () => {
  const args = buildUvArgs({
    command: "scan-folder",
    folderPath: "/data/episodes",
    topics: ["/camera/ego_head/image/jpeg"],
    jobs: 3
  });

  assert.deepEqual(args, [
    "scan-folder",
    "/data/episodes",
    "--detector",
    "camera-tearing",
    "--threshold",
    "0.72",
    "--history-size",
    "3",
    "--min-change",
    "0.08",
    "--max-stale-delta",
    "0.035",
    "--max-persistence-frames",
    "12",
    "--gap-window",
    "12",
    "--resize",
    "160x90",
    "--json",
    "-",
    "--topic",
    "/camera/ego_head/image/jpeg",
    "--jobs",
    "3"
  ]);
});

test("buildAnnotationIndexArgs creates annotation folder index command", () => {
  assert.deepEqual(buildAnnotationIndexArgs("/data/episodes"), ["index-folder", "/data/episodes", "--json", "-"]);
});

test("buildFrameExportArgs creates single-frame export command", () => {
  assert.deepEqual(
    buildFrameExportArgs({
      mcapPath: "/data/episode.mcap",
      topic: "/camera/ego_head/image/jpeg",
      frameIndex: 42,
      outputPath: "/tmp/frame.jpg"
    }),
    [
      "export-frame",
      "/data/episode.mcap",
      "--topic",
      "/camera/ego_head/image/jpeg",
      "--frame",
      "42",
      "--output",
      "/tmp/frame.jpg",
      "--json",
      "-"
    ]
  );
});

test("normalizeCandidatePaths adds file urls for snapshots", () => {
  const result = {
    candidates: [{ snapshot_path: "/tmp/frame.jpg" }, { snapshot_path: null }],
    events: [{ snapshot_path: "/tmp/event.jpg" }],
    files: [{ result: { candidates: [{ snapshot_path: "/tmp/nested.jpg" }], events: [{ snapshot_path: "/tmp/nested-event.jpg" }] } }]
  };

  normalizeCandidatePaths(result, (value) => ({ href: `file://${value}` }));

  assert.equal(result.candidates[0].snapshot_url, "file:///tmp/frame.jpg");
  assert.equal(result.candidates[1].snapshot_url, null);
  assert.equal(result.events[0].snapshot_url, "file:///tmp/event.jpg");
  assert.equal(result.files[0].result.candidates[0].snapshot_url, "file:///tmp/nested.jpg");
  assert.equal(result.files[0].result.events[0].snapshot_url, "file:///tmp/nested-event.jpg");
});
