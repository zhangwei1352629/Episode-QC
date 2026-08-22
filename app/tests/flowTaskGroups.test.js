const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

async function loadHelpers() {
  return import(pathToFileURL(path.resolve(__dirname, "../renderer/flow-task-groups.mjs")));
}

test("Flow 批次按采集任务分组并汇总可领取数量", async () => {
  const { groupFlowJobs } = await loadHelpers();
  const groups = groupFlowJobs([
    { code: "QCJ-1", task_code: "TASK-1", task_name: "开烤箱", status: "pending", claimable: true, required_episode_count: 5, asset_size_bytes: 100 },
    { code: "QCJ-2", task_code: "TASK-1", task_name: "开烤箱", status: "pending", claimable: false, required_episode_count: 3, asset_size_bytes: 200 },
    { code: "QCJ-3", task_code: "TASK-2", task_name: "倒垃圾", status: "claimed", claimable: true, required_episode_count: 2, asset_size_bytes: 300 },
  ]);

  assert.equal(groups.length, 2);
  assert.deepEqual(
    { name: groups[0].taskName, batches: groups[0].batchCount, claimable: groups[0].claimableBatchCount, episodes: groups[0].episodeCount, bytes: groups[0].sizeBytes },
    { name: "开烤箱", batches: 2, claimable: 1, episodes: 8, bytes: 300 },
  );
  assert.equal(groups[1].claimableBatchCount, 0);
});

test("缺少任务编号时按任务名称分组", async () => {
  const { groupFlowJobs } = await loadHelpers();
  const groups = groupFlowJobs([
    { code: "QCJ-1", task_name: "同一任务", status: "pending" },
    { code: "QCJ-2", task_name: "同一任务", status: "pending" },
  ]);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].batchCount, 2);
});

test("可领取任务分组排除已领取、本机任务和被阻塞批次", async () => {
  const { groupClaimableFlowJobs } = await loadHelpers();
  const groups = groupClaimableFlowJobs([
    { code: "QCJ-1", task_code: "TASK-1", task_name: "开烤箱", status: "pending", claimable: true },
    { code: "QCJ-2", task_code: "TASK-1", task_name: "开烤箱", status: "pending", claimable: true, local_task_id: "local-2" },
    { code: "QCJ-3", task_code: "TASK-1", task_name: "开烤箱", status: "pending", claimable: false },
    { code: "QCJ-4", task_code: "TASK-2", task_name: "倒垃圾", status: "claimed", claimable: true },
  ]);

  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].jobs.map((job) => job.code), ["QCJ-1"]);
});

test("待领取任务池保留不可领取批次并排除正常本机任务和已完成任务", async () => {
  const { groupFlowClaimPoolJobs } = await loadHelpers();
  const groups = groupFlowClaimPoolJobs([
    { code: "QCJ-1", task_code: "TASK-1", task_name: "开烤箱", status: "pending", claimable: true },
    { code: "QCJ-2", task_code: "TASK-1", task_name: "开烤箱", status: "pending", claimable: false, claim_blocked_reason: "任务未绑定可用标签库" },
    { code: "QCJ-3", task_code: "TASK-1", task_name: "开烤箱", status: "pending", local_task_id: "local-3", local_task_status: "submitted", cache_complete: true },
    { code: "QCJ-4", task_code: "TASK-2", task_name: "倒垃圾", status: "completed" },
  ]);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].batchCount, 2);
  assert.equal(groups[0].claimableBatchCount, 1);
  assert.deepEqual(groups[0].jobs.map((job) => job.code), ["QCJ-1", "QCJ-2"]);
});

test("缓存目录和状态文件丢失的本机任务重新出现在任务池", async () => {
  const helpers = await loadHelpers();
  const recoveryJob = {
    code: "QCJ-RECOVER",
    task_code: "TASK-RECOVER",
    task_name: "洗衣机六点位",
    status: "pending",
    claimable: true,
    local_task_id: "local-recover",
    local_task_status: "completed",
    cache_state_missing: true,
  };

  assert.equal(helpers.flowJobNeedsCacheRecovery(recoveryJob), true);
  assert.equal(helpers.flowJobIsClaimable(recoveryJob), false);
  const groups = helpers.groupFlowClaimPoolJobs([recoveryJob]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].recoverableBatchCount, 1);
  assert.deepEqual(helpers.flowTaskGroupPresentation(groups[0]), {
    tone: "recoverable",
    availabilityLabel: "可恢复 1",
    badgeLabel: "可恢复 1",
    totalLabel: "共 1 批次",
  });
  assert.equal(
    helpers.flowJobNeedsCacheRecovery({ ...recoveryJob, local_task_status: "submitted" }),
    false,
  );
});

test("领取心跳过期只显示异常，不把任务伪装成待领取", async () => {
  const { flowJobIsClaimable, flowJobOwnershipLabel } = await loadHelpers();
  const job = {
    code: "QCJ-OWNED",
    status: "in_progress",
    reviewer_name: "常鑫",
    lease_expired: true,
    claimable: false,
  };

  assert.equal(flowJobIsClaimable(job), false);
  assert.equal(flowJobOwnershipLabel(job), "由 常鑫 领取 · 心跳异常，归属仍保留");
});

test("任务分组展示模型突出可领取数量", async () => {
  const helpers = await loadHelpers();
  assert.equal(typeof helpers.flowTaskGroupPresentation, "function");

  assert.deepEqual(
    helpers.flowTaskGroupPresentation({ batchCount: 3, claimableBatchCount: 1 }),
    {
      tone: "claimable",
      availabilityLabel: "可领取 1 · 不可领取 2",
      badgeLabel: "可领取 1",
      totalLabel: "共 3 批次",
    },
  );
  assert.deepEqual(
    helpers.flowTaskGroupPresentation({ batchCount: 5, claimableBatchCount: 0 }),
    {
      tone: "blocked",
      availabilityLabel: "不可领取 5",
      badgeLabel: "不可领取 5",
      totalLabel: "共 5 批次",
    },
  );
});
