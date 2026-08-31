const DEFAULT_FRAME_STEP_NS = Math.round(1_000_000_000 / 30);

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function cameraFrameGrid(camera) {
  const count = finiteNumber(camera?.message_count);
  const first = finiteNumber(camera?.first_offset_ns);
  const last = finiteNumber(camera?.last_offset_ns);
  if (count === null || count <= 1 || first === null || last === null || last <= first) return null;
  const stepNs = Math.round((last - first) / (count - 1));
  if (stepNs <= 0) return null;
  return { originNs: Math.round(first), stepNs };
}

export function frameGridForCameras(cameras = [], selectedCameraId = null) {
  const selected = cameras.find((camera) => camera.stream_id === selectedCameraId);
  const candidates = selected ? [selected, ...cameras.filter((camera) => camera !== selected)] : cameras;
  for (const camera of candidates) {
    const grid = cameraFrameGrid(camera);
    if (grid) return grid;
  }
  return { originNs: 0, stepNs: DEFAULT_FRAME_STEP_NS };
}

export function snapTimeToFrame(timeNs, durationNs, grid) {
  const duration = Math.max(0, finiteNumber(durationNs) || 0);
  const time = Math.max(0, Math.min(finiteNumber(timeNs) || 0, duration));
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
  if ((finiteNumber(timeNs) || 0) >= duration) {
    return { startNs: Math.max(0, duration - step), endNs: duration };
  }
  const start = snapTimeToFrame(timeNs, duration, grid);
  const end = Math.min(duration, start + step);
  if (end > start) return { startNs: start, endNs: end };
  return { startNs: Math.max(0, duration - step), endNs: duration };
}
