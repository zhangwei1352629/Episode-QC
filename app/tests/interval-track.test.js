const {test}=require('node:test');
const assert=require('node:assert/strict');
test('dragging a time point keeps equal bounds and rejects out-of-range edits',async()=>{
  const {draggedBounds}=await import('../renderer/interval-track.mjs');
  const point={scope:'time_point',start_offset_ns:20,end_offset_ns:20};
  assert.deepEqual(draggedBounds(point,'point',70,100),{start_offset_ns:70,end_offset_ns:70});
  assert.deepEqual(draggedBounds(point,'point',0,100),{start_offset_ns:0,end_offset_ns:0});
  assert.deepEqual(draggedBounds(point,'point',100,100),{start_offset_ns:100,end_offset_ns:100});
  assert.throws(()=>draggedBounds(point,'point',101,100));
  assert.throws(()=>draggedBounds(point,'point',-1,100));
  assert.throws(()=>draggedBounds({...point,scope:'episode'},'point',70,100));
  assert.deepEqual(draggedBounds({scope:'time_range',start_offset_ns:20,end_offset_ns:80},'start',40,100),{start_offset_ns:40,end_offset_ns:80});
});
test('I and O keyboard bindings remain available',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const source=fs.readFileSync(path.join(__dirname,'../renderer/renderer.js'),'utf8');
  assert.ok(source.includes('key === "I" && !els.markIn.disabled'));
  assert.ok(source.includes('key === "O" && !els.markOut.disabled'));
});
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
