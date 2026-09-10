const {test}=require('node:test');
const assert=require('node:assert/strict');
test('track zoom retains pointer anchor and clamps to episode boundaries',async()=>{
  const {zoomWindow}=await import('../renderer/interval-track.mjs');
  assert.deepEqual(zoomWindow(0,10e9,5e9,.5,10e9),[2.5e9,7.5e9]);
  assert.deepEqual(zoomWindow(0,10e9,0,.5,10e9),[0,5e9]);
  assert.deepEqual(zoomWindow(5e9,10e9,10e9,2,10e9),[0,10e9]);
  assert.deepEqual(zoomWindow(0,5e7,2e7,.5,5e7),[0,5e7]);
});
test('track controller does not add a second timeline or write on pointermove',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const text=fs.readFileSync(path.join(__dirname,'../renderer/interval-track.mjs'),'utf8');
  assert.ok(!text.includes('type="range"'));
  const move=text.slice(text.indexOf("container.addEventListener('pointermove'"),text.indexOf('function cancel()'));
  assert.ok(!move.includes('await save'));
  assert.ok(text.includes("container.addEventListener('pointerup'"));
});
