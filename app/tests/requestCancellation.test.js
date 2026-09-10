const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function setup(fetch) {
  const timers = new Map(); let id = 0;
  const window = { location: { search: '' }, sessionStorage: {getItem:()=>'',setItem(){}}, history:{} };
  const context = vm.createContext({window, fetch, AbortController, Response, URLSearchParams, URL,
    setTimeout:(fn)=>{timers.set(++id,fn);return id;}, clearTimeout:i=>timers.delete(i)});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../renderer/web-api.js'),'utf8').replace(/^import .*\n/,''),context);
  return {api:window.episodeQc,timers};
}

test('navigation cancels old reads but never annotation/review writes', async()=>{
  const calls=[];
  const {api}=setup((url,options)=>new Promise((resolve,reject)=>{
    calls.push({url,options,resolve});options.signal.addEventListener('abort',()=>reject(new Error('abort')));
  }));
  const read=api.getEpisode('ep_old');
  const write=api.updateReview({episodeId:'ep_old',playheadNs:0});
  api.cancelEpisodeReads();
  await assert.rejects(read,/取消或超时/);
  assert.equal(calls[1].options.signal.aborted,false);
  calls[1].resolve(new Response('{}'));await write;
  const next=api.getEpisode('ep_new');calls[2].resolve(new Response('{}'));await next;
});

test('timeout also covers stalled binary response bodies',async()=>{
  const {api,timers}=setup(async(url,options)=>({ok:true,status:200,headers:new Headers(),
    arrayBuffer:()=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('abort'))))}));
  const read=api.getCameraFrame({episodeId:'ep_x',streamId:'s',timeNs:0});
  await new Promise(setImmediate);
  for(const fire of timers.values())fire();
  await assert.rejects(read,/取消或超时/);
  assert.equal(timers.size,0);
});

test('completion of an old frame request cannot unlock the current generation',async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../renderer/renderer.js'),'utf8');
  const fn=source.slice(source.indexOf('async function requestVisualFrames('),source.indexOf('\nfunction playbackLoop('));
  const pending=[];
  const state={cache:{cameras:[{stream_id:'s'}]},currentEpisodeId:'e',playbackEpisodeId:'e',visualPending:false,visualGeneration:1,durationNs:100,playheadNs:0};
  const context=vm.createContext({state,performance:{now:()=>1000},URL:{revokeObjectURL(){}},
    window:{episodeQc:{getCameraFrame:()=>new Promise(resolve=>pending.push(resolve))}},
    els:{cameraGrid:{querySelector:()=>null}},calibration:null,setCacheStatus(){}});
  vm.runInContext(fn,context);
  const first=context.requestVisualFrames(true);
  state.visualGeneration=2;state.visualPending=false;
  const second=context.requestVisualFrames(true);
  pending[0]({dataUrl:'blob:old'});await first;
  assert.equal(state.visualPending,true);
  pending[1]({dataUrl:'blob:new'});await second;
  assert.equal(state.visualPending,false);
});

test('paused drag requests its final frame after an in-flight request',async()=>{
  const source=fs.readFileSync(path.join(__dirname,'../renderer/renderer.js'),'utf8');
  const fn=source.slice(source.indexOf('async function requestVisualFrames('),source.indexOf('\nfunction playbackLoop('));
  const pending=[], times=[];
  const state={cache:{cameras:[{stream_id:'s'}]},currentEpisodeId:'e',playbackEpisodeId:'e',visualPending:false,visualGeneration:1,durationNs:100,playheadNs:0,playing:false};
  const context=vm.createContext({state,performance:{now:()=>1000},URL:{revokeObjectURL(){}},calibration:null,
    window:{episodeQc:{getCameraFrame:({timeNs})=>{times.push(timeNs);return new Promise(resolve=>pending.push(resolve));}}},els:{cameraGrid:{querySelector:()=>null}},setCacheStatus(){}});
  vm.runInContext(fn,context);
  const first=context.requestVisualFrames(true);state.playheadNs=80;
  await context.requestVisualFrames(true);
  pending[0]({});await first;
  assert.deepEqual(times,[0,80]);assert.equal(state.visualPending,true);
  pending[1]({});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(state.visualReadyTime,80);assert.equal(state.visualPending,false);
});
