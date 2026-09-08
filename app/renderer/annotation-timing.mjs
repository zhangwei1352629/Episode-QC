export function annotationDurationNs(episode) {
  if (!episode || episode.annotation_timing_error) return 0;
  const limit = episode.annotation_duration_ns;
  if (limit === null || (limit === undefined && episode.task_origin === "flow")) return 0;
  return Math.max(0, Math.min(Number(episode.duration_ns || 0), Number(limit ?? episode.duration_ns ?? 0)));
}

export function annotationTimeError(episode, scope, start, end) {
  const limit = annotationDurationNs(episode);
  if (!limit) return episode?.annotation_timing_error || "可标注时长未就绪，请重新打开任务同步";
  if (scope === "episode") return "";
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || end > limit || end < start ||
      (scope === "time_range" && end === start) || (scope === "time_point" && start !== end)) {
    return `标注时间越界或区间无效，可标注范围 0–${(limit / 1e9).toFixed(9)} 秒`;
  }
  return "";
}
