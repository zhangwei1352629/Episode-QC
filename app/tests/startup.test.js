const test = require("node:test");
const assert = require("node:assert/strict");

test("NAS 状态请求卡住时仍能加载本地任务", async () => {
  const { loadInitialTasks } = await import("../renderer/startup.mjs");
  let releaseNasStatus;
  const blockedNasStatus = new Promise((resolve) => { releaseNasStatus = resolve; });

  try {
    const result = await Promise.race([
      loadInitialTasks({
        getTasks: async () => ({ tasks: [{ id: "task-local" }] }),
        refreshNasStatus: () => blockedNasStatus,
      }),
      new Promise((_, reject) => {
        setTimeout(() => reject(new Error("任务加载被 NAS 状态请求阻塞")), 100);
      }),
    ]);

    assert.deepEqual(result, { tasks: [{ id: "task-local" }] });
  } finally {
    releaseNasStatus();
  }
});
