const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('切换并行读取且旧条目迟到不能覆盖当前条目', async () => {
  const source = fs.readFileSync('app/renderer/renderer.js', 'utf8');
  const code = source.slice(source.indexOf('let nextEpisodeWarmup'), source.indexOf('async function reloadCurrentEpisode'));
  const detail = {}, prepare = {}, calls = [];
  const state = {currentEpisodeId:'old',episodes:[],loadToken:0};
  const context = {state,performance:{now:()=>10},console:{info(){}},clearTimeout(){},setTimeout(){},requestAnimationFrame:f=>f(),calibration:null,
    savePlayhead:()=>new Promise(()=>{}), refreshAI:async()=>{}, requestVisualFrames:async()=>{},
    window:{episodeQc:{cancelEpisodeReads(){},updateWorkspaceSettings:async()=>{},
      getEpisode:id=>{calls.push('detail-'+id);return new Promise(r=>detail[id]=r);},
      prepareEpisode:id=>{calls.push('prepare-'+id);return new Promise(r=>prepare[id]=r);}}}};
  for(const name of ['syncJointSelectionUi','updatePlaybackButton','syncInteractiveState','renderEpisodeList','setCacheStatus','renderEpisodeDetail','renderCameras','renderMotionAvailability','renderClock','renderSelection','renderAnnotations','toast'])context[name]=()=>{};
  vm.createContext(context);vm.runInContext(code,context);
  const first=context.openEpisode('a'),second=context.openEpisode('b');
  assert.deepEqual(calls,['prepare-a','detail-a','prepare-b','detail-b']);
  detail.b({episode:{duration_ns:10}});prepare.b({complete:true});await second;
  detail.a({episode:{duration_ns:99}});prepare.a({complete:true});await first;
  assert.equal(state.currentEpisodeId,'b');assert.equal(state.durationNs,10);
  assert.equal(state.switchTimings.length,1);
});

test('异步保存播放位置捕获原条目且同条目按顺序保存', async()=>{
  const source=fs.readFileSync('app/renderer/renderer.js','utf8');
  const code=source.slice(source.indexOf('const playheadSaves'),source.indexOf('function moveEpisode('));
  const writes=[],releases=[];const state={currentEpisodeId:'a',playheadNs:1};
  const ctx=vm.createContext({state,els:{reviewerName:{value:'tester'}},window:{episodeQc:{updateReview:p=>{writes.push(p);return new Promise(r=>releases.push(r));}}}});
  vm.runInContext(code,ctx);const a=ctx.savePlayhead();state.playheadNs=2;const b=ctx.savePlayhead();state.currentEpisodeId='b';
  await new Promise(setImmediate);assert.equal(writes.length,1);releases[0]();await a;await new Promise(setImmediate);
  assert.equal(writes[1].episodeId,'a');assert.equal(writes[1].playheadNs,2);releases[1]();await b;
});
