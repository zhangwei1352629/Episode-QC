function nonNegativeNumber(value) {
  return Math.max(0, Number(value) || 0);
}

export function flowJobIsClaimable(job) {
  return job?.status === "pending" && job?.claimable !== false && !job?.local_task_id;
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
        episodeCount: 0,
        sizeBytes: 0,
      });
    }
    const group = groups.get(key);
    group.jobs.push(job);
    group.batchCount += 1;
    if (flowJobIsClaimable(job)) group.claimableBatchCount += 1;
    group.episodeCount += nonNegativeNumber(job?.required_episode_count ?? job?.episodes?.length);
    group.sizeBytes += nonNegativeNumber(job?.asset_size_bytes);
  });
  return [...groups.values()];
}

export function groupClaimableFlowJobs(jobs = []) {
  return groupFlowJobs(jobs.filter(flowJobIsClaimable));
}

export function flowJobIsVisibleInClaimPool(job) {
  return !job?.local_task_id && job?.status !== "completed";
}

export function groupFlowClaimPoolJobs(jobs = []) {
  return groupFlowJobs(jobs.filter(flowJobIsVisibleInClaimPool));
}

export function flowTaskGroupPresentation(group) {
  const batchCount = nonNegativeNumber(group?.batchCount);
  const claimableBatchCount = Math.min(batchCount, nonNegativeNumber(group?.claimableBatchCount));
  const blockedBatchCount = batchCount - claimableBatchCount;
  const availabilityLabel = [
    claimableBatchCount ? `可领取 ${claimableBatchCount}` : "",
    blockedBatchCount ? `不可领取 ${blockedBatchCount}` : "",
  ].filter(Boolean).join(" · ");
  return {
    tone: claimableBatchCount ? "claimable" : "blocked",
    availabilityLabel,
    badgeLabel: claimableBatchCount ? `可领取 ${claimableBatchCount}` : `不可领取 ${blockedBatchCount}`,
    totalLabel: `共 ${batchCount} 批次`,
  };
}
