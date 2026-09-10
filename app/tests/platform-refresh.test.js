const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('慢任务列表继续轮询，失败不改写登录状态', async () => {
  const source = fs.readFileSync('app/renderer/renderer.js', 'utf8');
  const code = source.slice(source.indexOf('let platformRefreshInFlight'), source.indexOf('function renderPlatformJobs()'));
  let polling = 0;
  const state = {platform: {connected: true}};
  const context = vm.createContext({state, els: {flowTaskStatus: {}},
    window: {episodeQc: {getPlatformJobs: async () => ({connected: true, refreshing: true, jobs: []})}},
    renderPlatformJobs() {}, startFlowPolling() { polling++; },
    stopFlowPolling() { throw Error('must continue polling'); }, toast() {}});
  vm.runInContext(code, context);
  await context.refreshPlatformJobs();
  assert.equal(polling, 1);
  context.window.episodeQc.getPlatformJobs = async () => { throw Error('timeout'); };
  await context.refreshPlatformJobs({quiet: true});
  assert.equal(state.platform.connected, true);
  assert.match(context.els.flowTaskStatus.textContent, /任务列表刷新失败/);
});
