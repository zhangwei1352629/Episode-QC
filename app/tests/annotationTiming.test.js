const test = require("node:test");
const assert = require("node:assert/strict");

test("Flow 标注范围独立于完整播放时长，边界精确到纳秒", async () => {
  const { annotationDurationNs, annotationTimeError } = await import("../renderer/annotation-timing.mjs");
  const episode = { duration_ns: 38866183529, annotation_duration_ns: 38661000000, task_origin: "flow" };
  assert.equal(annotationDurationNs(episode), 38661000000);
  assert.equal(episode.duration_ns, 38866183529);
  assert.equal(annotationTimeError(episode, "time_range", 1, 38661000000), "");
  assert.match(annotationTimeError(episode, "time_range", 1, 38661000001), /越界/);
  assert.match(annotationTimeError(episode, "time_point", 38797504476, 38797504476), /越界/);
  assert.match(annotationTimeError(episode, "time_range", 2, 1), /无效/);
  assert.match(annotationTimeError(episode, "time_range", NaN, 1), /无效/);
});

test("I/O、拖动与末帧选择均使用可标注上限", async () => {
  const { beginRangeSelection, completeRangeSelection, singleFrameRange, snapTimeToFrame } = await import("../renderer/range-selection.mjs");
  const durationNs = 38661000000;
  assert.ok(beginRangeSelection({ playheadNs: 38866183529, durationNs }).startNs <= durationNs);
  assert.ok(completeRangeSelection({ startNs: 1, playheadNs: 38866183529, durationNs }).endNs <= durationNs);
  assert.ok(singleFrameRange({ timeNs: durationNs, durationNs }).endNs <= durationNs);
  assert.ok(snapTimeToFrame(38866183529, durationNs) <= durationNs);
});

test("Flow 时长缺失禁止标注，本地任务保持原有时长", async () => {
  const { annotationDurationNs, annotationTimeError } = await import("../renderer/annotation-timing.mjs");
  assert.equal(annotationDurationNs({ duration_ns: 123 }), 123);
  assert.equal(annotationDurationNs({ duration_ns: 123, task_origin: "flow" }), 0);
  assert.match(annotationTimeError({ annotation_timing_error: "Flow 时长缺失" }, "episode", 0, 1), /Flow 时长缺失/);
});
