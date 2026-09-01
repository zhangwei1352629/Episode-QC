const test = require("node:test");
const assert = require("node:assert/strict");

test("参考相机时间戳生成稳定的帧网格", async () => {
  const { frameGridForCameras } = await import("../renderer/range-selection.mjs");
  const cameras = [
    {
      stream_id: "cam-default",
      message_count: 616,
      first_offset_ns: 0,
      last_offset_ns: 20_500_000_000,
    },
    {
      stream_id: "cam-selected",
      message_count: 301,
      first_offset_ns: 0,
      last_offset_ns: 10_000_000_000,
    },
  ];

  assert.deepEqual(frameGridForCameras(cameras, "cam-selected"), {
    originNs: 0,
    stepNs: 33_333_333,
  });
});

test("重新设置起点会清除旧终点并吸附到视频帧", async () => {
  const { beginRangeSelection } = await import("../renderer/range-selection.mjs");

  assert.deepEqual(
    beginRangeSelection({
      playheadNs: 3_475_488_000,
      durationNs: 20_500_000_000,
      grid: { originNs: 0, stepNs: 33_333_333 },
    }),
    { startNs: 3_466_666_632, endNs: null },
  );
});

test("没有起点或终点不晚于起点时不会静默生成错误区间", async () => {
  const { completeRangeSelection } = await import("../renderer/range-selection.mjs");
  const common = {
    durationNs: 20_500_000_000,
    grid: { originNs: 0, stepNs: 33_333_333 },
  };

  assert.deepEqual(
    completeRangeSelection({ ...common, startNs: null, playheadNs: 3_000_000_000 }),
    { ok: false, reason: "missing_start", startNs: null, endNs: null },
  );
  assert.deepEqual(
    completeRangeSelection({ ...common, startNs: 5_000_000_000, playheadNs: 4_000_000_000 }),
    { ok: false, reason: "end_not_after_start", startNs: 5_000_000_000, endNs: null },
  );
});

test("单帧区间在时间轴结尾仍保持有效", async () => {
  const { singleFrameRange } = await import("../renderer/range-selection.mjs");

  assert.deepEqual(
    singleFrameRange({
      timeNs: 20_500_000_000,
      durationNs: 20_500_000_000,
      grid: { originNs: 0, stepNs: 33_333_333 },
    }),
    { startNs: 20_466_666_667, endNs: 20_500_000_000 },
  );
});

test("时间轴只有超过拖拽阈值才进入选区手势", async () => {
  const { isTimelineDrag } = await import("../renderer/range-selection.mjs");

  assert.equal(isTimelineDrag(100, 103.9), false);
  assert.equal(isTimelineDrag(100, 104), true);
  assert.equal(isTimelineDrag(100, 95), true);
  assert.equal(isTimelineDrag(undefined, 110), false);
});
