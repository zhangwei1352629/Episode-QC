const DEFAULT_FRAME_STEP_NS = Math.round(1_000_000_000 / 30);
const DEFAULT_TIMELINE_DRAG_THRESHOLD_PX = 4;

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function cameraFrameGrid(camera) {
  const frameOffsetsNs = camera?.frame_offsets_ns;
  if (Array.isArray(frameOffsetsNs) && frameOffsetsNs.length) {
    const first = finiteNumber(frameOffsetsNs[0]);
    const last = finiteNumber(frameOffsetsNs[frameOffsetsNs.length - 1]);
    if (first !== null && last !== null && last >= first) {
      const stepNs = frameOffsetsNs.length > 1
        ? Math.max(1, Math.round((last - first) / (frameOffsetsNs.length - 1)))
        : DEFAULT_FRAME_STEP_NS;
      return {
        originNs: Math.round(first),
        stepNs,
        frameCount: frameOffsetsNs.length,
        frameOffsetsNs,
        streamId: camera.stream_id || null,
        displayName: camera.display_name || camera.topic || camera.stream_id || "参考相机",
        exact: true,
      };
    }
  }
  const count = finiteNumber(camera?.message_count);
  const first = finiteNumber(camera?.first_offset_ns);
  const last = finiteNumber(camera?.last_offset_ns);
  if (count === null || count <= 1 || first === null || last === null || last <= first) return null;
  const stepNs = Math.round((last - first) / (count - 1));
  if (stepNs <= 0) return null;
  return {
    originNs: Math.round(first),
    stepNs,
    frameCount: Math.round(count),
    frameOffsetsNs: null,
    streamId: camera.stream_id || null,
    displayName: camera.display_name || camera.topic || camera.stream_id || "参考相机",
    exact: false,
  };
}

export function frameGridForCameras(cameras = [], selectedCameraId = null) {
  const selected = cameras.find((camera) => camera.stream_id === selectedCameraId);
  const candidates = selected ? [selected, ...cameras.filter((camera) => camera !== selected)] : cameras;
  for (const camera of candidates) {
    const grid = cameraFrameGrid(camera);
    if (grid) return grid;
  }
  return {
    originNs: 0,
    stepNs: DEFAULT_FRAME_STEP_NS,
    frameCount: null,
    frameOffsetsNs: null,
    streamId: null,
    displayName: null,
    exact: false,
  };
}

function lowerBound(values, target) {
  let low = 0;
  let high = values.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (Number(values[middle]) < target) low = middle + 1;
    else high = middle;
  }
  return low;
}

function nearestFrameIndex(timeNs, grid) {
  const offsets = grid?.frameOffsetsNs;
  if (Array.isArray(offsets) && offsets.length) {
    const position = lowerBound(offsets, timeNs);
    if (position <= 0) return 0;
    if (position >= offsets.length) return offsets.length - 1;
    const before = Number(offsets[position - 1]);
    const after = Number(offsets[position]);
    return timeNs - before <= after - timeNs ? position - 1 : position;
  }
  const count = Math.max(0, Math.round(finiteNumber(grid?.frameCount) || 0));
  if (!count) return null;
  const origin = finiteNumber(grid?.originNs) || 0;
  const step = Math.max(1, Math.round(finiteNumber(grid?.stepNs) || DEFAULT_FRAME_STEP_NS));
  return Math.max(0, Math.min(count - 1, Math.round((timeNs - origin) / step)));
}

export function framePositionForTime(timeNs, durationNs, grid) {
  const duration = Math.max(0, finiteNumber(durationNs) || 0);
  const time = Math.max(0, Math.min(finiteNumber(timeNs) || 0, duration));
  const index = nearestFrameIndex(time, grid);
  const total = Math.max(0, Math.round(finiteNumber(grid?.frameCount) || 0));
  if (index === null || !total) return null;
  return { index, number: index + 1, total, exact: grid?.exact === true };
}

export function frameRangeForInterval(startNs, endNs, grid) {
  const start = Math.max(0, finiteNumber(startNs) || 0);
  const end = Math.max(start, finiteNumber(endNs) || 0);
  const total = Math.max(0, Math.round(finiteNumber(grid?.frameCount) || 0));
  if (!total || end <= start) return null;

  const offsets = grid?.frameOffsetsNs;
  let firstIndex;
  let lastIndex;
  if (Array.isArray(offsets) && offsets.length) {
    firstIndex = lowerBound(offsets, start);
    lastIndex = lowerBound(offsets, end) - 1;
  } else {
    const origin = finiteNumber(grid?.originNs) || 0;
    const step = Math.max(1, Math.round(finiteNumber(grid?.stepNs) || DEFAULT_FRAME_STEP_NS));
    firstIndex = Math.ceil((start - origin) / step);
    lastIndex = Math.ceil((end - origin) / step) - 1;
  }
  if (firstIndex >= total || lastIndex < 0 || lastIndex < firstIndex) {
    return { startNumber: null, endNumber: null, count: 0, total, exact: grid?.exact === true };
  }
  firstIndex = Math.max(0, firstIndex);
  lastIndex = Math.min(total - 1, lastIndex);
  return {
    startNumber: firstIndex + 1,
    endNumber: lastIndex + 1,
    count: lastIndex - firstIndex + 1,
    total,
    exact: grid?.exact === true,
  };
}

export function snapTimeToFrame(timeNs, durationNs, grid) {
  const duration = Math.max(0, finiteNumber(durationNs) || 0);
  const time = Math.max(0, Math.min(finiteNumber(timeNs) || 0, duration));
  const offsets = grid?.frameOffsetsNs;
  if (Array.isArray(offsets) && offsets.length) {
    const index = nearestFrameIndex(time, grid);
    return Math.round(Math.max(0, Math.min(Number(offsets[index]), duration)));
  }
  const origin = finiteNumber(grid?.originNs) || 0;
  const step = Math.max(1, Math.round(finiteNumber(grid?.stepNs) || DEFAULT_FRAME_STEP_NS));
  const snapped = origin + Math.round((time - origin) / step) * step;
  return Math.round(Math.max(0, Math.min(snapped, duration)));
}

export function beginRangeSelection({ playheadNs, durationNs, grid }) {
  return {
    startNs: snapTimeToFrame(playheadNs, durationNs, grid),
    endNs: null,
  };
}

export function completeRangeSelection({ startNs, playheadNs, durationNs, grid }) {
  if (startNs === null || startNs === undefined) {
    return { ok: false, reason: "missing_start", startNs: null, endNs: null };
  }
  const start = Math.round(Number(startNs));
  const end = snapTimeToFrame(playheadNs, durationNs, grid);
  if (end <= start) {
    return { ok: false, reason: "end_not_after_start", startNs: start, endNs: null };
  }
  return { ok: true, reason: null, startNs: start, endNs: end };
}

export function singleFrameRange({ timeNs, durationNs, grid }) {
  const duration = Math.max(0, Math.round(finiteNumber(durationNs) || 0));
  const step = Math.max(1, Math.round(finiteNumber(grid?.stepNs) || DEFAULT_FRAME_STEP_NS));
  if (!duration) return { startNs: 0, endNs: 0 };
  const offsets = grid?.frameOffsetsNs;
  if (Array.isArray(offsets) && offsets.length) {
    const index = nearestFrameIndex(Math.max(0, Math.min(finiteNumber(timeNs) || 0, duration)), grid);
    const start = Math.max(0, Math.min(Number(offsets[index]), duration));
    const end = index + 1 < offsets.length ? Math.min(Number(offsets[index + 1]), duration) : duration;
    if (end > start) return { startNs: Math.round(start), endNs: Math.round(end) };
    if (index > 0) return { startNs: Math.round(Math.max(0, Number(offsets[index - 1]))), endNs: duration };
    return { startNs: 0, endNs: duration };
  }
  if ((finiteNumber(timeNs) || 0) >= duration) {
    return { startNs: Math.max(0, duration - step), endNs: duration };
  }
  const start = snapTimeToFrame(timeNs, duration, grid);
  const end = Math.min(duration, start + step);
  if (end > start) return { startNs: start, endNs: end };
  return { startNs: Math.max(0, duration - step), endNs: duration };
}

export function isTimelineDrag(startClientX, currentClientX, thresholdPx = DEFAULT_TIMELINE_DRAG_THRESHOLD_PX) {
  const start = finiteNumber(startClientX);
  const current = finiteNumber(currentClientX);
  if (start === null || current === null) return false;
  const threshold = Math.max(0, finiteNumber(thresholdPx) ?? DEFAULT_TIMELINE_DRAG_THRESHOLD_PX);
  return Math.abs(current - start) >= threshold;
}
