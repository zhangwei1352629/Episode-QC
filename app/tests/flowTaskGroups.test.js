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

test("待领取任务池保留不可领取批次并排除本机和已完成任务", async () => {
  const { groupFlowClaimPoolJobs } = await loadHelpers();
  const groups = groupFlowClaimPoolJobs([
    { code: "QCJ-1", task_code: "TASK-1", task_name: "开烤箱", status: "pending", claimable: true },
    { code: "QCJ-2", task_code: "TASK-1", task_name: "开烤箱", status: "pending", claimable: false, claim_blocked_reason: "任务未绑定可用标签库" },
    { code: "QCJ-3", task_code: "TASK-1", task_name: "开烤箱", status: "pending", local_task_id: "local-3" },
    { code: "QCJ-4", task_code: "TASK-2", task_name: "倒垃圾", status: "completed" },
  ]);

  assert.equal(groups.length, 1);
  assert.equal(groups[0].batchCount, 2);
  assert.equal(groups[0].claimableBatchCount, 1);
  assert.deepEqual(groups[0].jobs.map((job) => job.code), ["QCJ-1", "QCJ-2"]);
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
