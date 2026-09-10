const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('慢任务列表继续轮询，失败不改写登录状态', async () => {
  const source = fs.readFileSync('app/renderer/renderer.js', 'utf8');
  const code = source.slice(source.indexOf('let platformRefreshInFlight'), source.indexOf('function renderPlatformJobs()'));
  let polling = 0;
  const state = {platform: {connected: true}};
  const context = vm.createContext({state, $: () => null, els: {flowTaskStatus: {}},
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

test('未登录和服务不可达均持续提示，已登录后清除提示', async () => {
  const source = fs.readFileSync('app/renderer/renderer.js', 'utf8');
  const code = source.slice(source.indexOf('async function refreshFlowSessionNotice'), source.indexOf('async function refreshPlatformJobs'));
  const notice = {};
  const context = vm.createContext({$: () => notice, window: {episodeQc: {
    getPlatformStatus: async () => ({enabled: true, logged_in: false})}}});
  vm.runInContext(code, context);
  await context.refreshFlowSessionNotice();
  assert.equal(notice.hidden, false);
  assert.match(notice.textContent, /尚未登录 Flow/);
  context.window.episodeQc.getPlatformStatus = async () => ({enabled: true, logged_in: true});
  await context.refreshFlowSessionNotice();
  assert.equal(notice.hidden, true);
  context.window.episodeQc.getPlatformStatus = async () => { throw Error('offline'); };
  await context.refreshFlowSessionNotice();
  assert.equal(notice.hidden, false);
  assert.match(notice.textContent, /暂时无法确认/);
});
