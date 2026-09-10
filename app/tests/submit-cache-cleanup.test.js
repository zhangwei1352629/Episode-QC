const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('提交弹窗选择保留或删除缓存，并区分清理失败', async () => {
  const source = fs.readFileSync('app/renderer/renderer.js', 'utf8');
  const code = source.slice(source.indexOf('async function submitCurrentFlowTask()'), source.indexOf('async function switchTask('));
  for (const deleteCache of [false, true]) {
    const calls = [], notices = [];
    const choices = [true, deleteCache];
    const context = vm.createContext({
      state: { currentTask: { flow_job_code: 'QCJ-1', status: 'completed' } },
      els: {}, setBusyButton() {}, renderTaskContext() {},
      refreshWorkspace: async () => {}, refreshPlatformJobs: async () => {},
      toast: (message) => notices.push(message),
      window: { confirm: () => choices.shift(), episodeQc: { submitPlatformJob: async (job, options) => {
        calls.push(options.deleteCache);
        return deleteCache ? { cache_cleanup: { status: 'failed', error: 'file busy' } } : {};
      } } },
    });
    await vm.runInContext(code + '\nsubmitCurrentFlowTask()', context);
    assert.deepEqual(calls, [deleteCache]);
    assert.match(notices[0], deleteCache ? /提交成功，但缓存未完整删除/ : /提交成功，本地缓存已保留/);
  }
});
