function nonNegativeNumber(value) {
  return Math.max(0, Number(value) || 0);
}

export function flowJobIsClaimable(job) {
  return job?.status === "pending" && job?.claimable !== false && !job?.local_task_id;
}

export function flowJobNeedsCacheRecovery(job) {
  return Boolean(job?.local_task_id)
    && (job?.cache_recovery_available === true || job?.cache_state_missing === true)
    && !["submitted", "archived"].includes(job?.local_task_status)
    && !["completed", "waiting_data"].includes(job?.status);
}

export function flowJobOwnershipLabel(job) {
  const reviewer = String(job?.reviewer_name || "").trim();
  if (!reviewer) return "";
  return job?.lease_expired
    ? `由 ${reviewer} 领取 · 心跳异常，归属仍保留`
    : `由 ${reviewer} 领取`;
}

export function flowTaskGroupKey(job, index = 0) {
  const taskCode = String(job?.task_code || "").trim();
  if (taskCode) return `code:${taskCode}`;
  const taskName = String(job?.task_name || "").trim();
  if (taskName) return `name:${taskName}`;
  return `job:${job?.asset_id || job?.code || index}`;
}

export function groupFlowJobs(jobs = []) {
  const groups = new Map();
  jobs.forEach((job, index) => {
    const key = flowTaskGroupKey(job, index);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        taskCode: job?.task_code || "",
        taskName: job?.task_name || job?.asset_id || job?.code || "未命名采集任务",
        jobs: [],
        batchCount: 0,
        claimableBatchCount: 0,
        recoverableBatchCount: 0,
        episodeCount: 0,
        sizeBytes: 0,
      });
    }
    const group = groups.get(key);
    group.jobs.push(job);
    group.batchCount += 1;
    if (flowJobIsClaimable(job)) group.claimableBatchCount += 1;
    if (flowJobNeedsCacheRecovery(job)) group.recoverableBatchCount += 1;
    group.episodeCount += nonNegativeNumber(job?.required_episode_count ?? job?.episodes?.length);
    group.sizeBytes += nonNegativeNumber(job?.asset_size_bytes);
  });
  return [...groups.values()];
}

export function groupClaimableFlowJobs(jobs = []) {
  return groupFlowJobs(jobs.filter(flowJobIsClaimable));
}

export function flowJobIsVisibleInClaimPool(job) {
  return flowJobNeedsCacheRecovery(job) || (!job?.local_task_id && job?.status !== "completed");
}

export function groupFlowClaimPoolJobs(jobs = []) {
  return groupFlowJobs(jobs.filter(flowJobIsVisibleInClaimPool));
}

export function flowTaskGroupPresentation(group) {
  const batchCount = nonNegativeNumber(group?.batchCount);
  const claimableBatchCount = Math.min(batchCount, nonNegativeNumber(group?.claimableBatchCount));
  const recoverableBatchCount = Math.min(
    batchCount - claimableBatchCount,
    nonNegativeNumber(group?.recoverableBatchCount),
  );
  const blockedBatchCount = batchCount - claimableBatchCount - recoverableBatchCount;
  const availabilityLabel = [
    claimableBatchCount ? `可领取 ${claimableBatchCount}` : "",
    recoverableBatchCount ? `可恢复 ${recoverableBatchCount}` : "",
    blockedBatchCount ? `不可领取 ${blockedBatchCount}` : "",
  ].filter(Boolean).join(" · ");
  return {
    tone: claimableBatchCount ? "claimable" : recoverableBatchCount ? "recoverable" : "blocked",
    availabilityLabel,
    badgeLabel: claimableBatchCount
      ? `可领取 ${claimableBatchCount}`
      : recoverableBatchCount
        ? `可恢复 ${recoverableBatchCount}`
        : `不可领取 ${blockedBatchCount}`,
    totalLabel: `共 ${batchCount} 批次`,
  };
}
