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
test('manual timeline gestures from before AI boundary editing remain available',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const source=fs.readFileSync(path.join(__dirname,'../renderer/renderer.js'),'utf8');
  assert.ok(source.includes('els.annotationTrack.addEventListener("pointerdown", beginTimelineSelection)'));
  assert.ok(source.includes('els.annotationTrack.addEventListener("dblclick"'));
  assert.ok(source.includes('calibration.select(item.dataset.annotationId)'));
  assert.ok(source.includes('state.scope = "time_point"'));
});
test('track zoom retains pointer anchor and clamps to episode boundaries',async()=>{
  const {zoomWindow}=await import('../renderer/interval-track.mjs');
  assert.deepEqual(zoomWindow(0,10e9,5e9,.5,10e9),[2.5e9,7.5e9]);
  assert.deepEqual(zoomWindow(0,10e9,0,.5,10e9),[0,5e9]);
  assert.deepEqual(zoomWindow(5e9,10e9,10e9,2,10e9),[0,10e9]);
  assert.deepEqual(zoomWindow(0,5e7,2e7,.5,5e7),[0,5e7]);
});
test('boundary dragging preserves the exact grab point inside the wide handle',async()=>{
  const {boundaryGrabOffset,timeAtClientX}=await import('../renderer/interval-track.mjs');
  const view=[0,1000],rect={left:100,width:400};
  const grabOffset=boundaryGrabOffset(view,250,210,rect);
  assert.equal(grabOffset,10);
  assert.equal(timeAtClientX(view,250,rect,grabOffset),350);
  assert.equal(timeAtClientX(view,210,rect,grabOffset),250);
});
test('track controller does not add a second timeline or write on pointermove',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const text=fs.readFileSync(path.join(__dirname,'../renderer/interval-track.mjs'),'utf8');
  assert.ok(!text.includes('type="range"'));
  const move=text.slice(text.indexOf("container.addEventListener('pointermove'"),text.indexOf('function cancel()'));
  assert.ok(!move.includes('await save'));
  assert.ok(text.includes("container.addEventListener('pointerup'"));
});
test('gapless AI ranges become one segment track and ignore unrelated annotations',async()=>{
  const {contiguousAiSegments}=await import('../renderer/interval-track.mjs');
  const ai=(id,start,end)=>({annotation_id:id,episode_id:'ep',scope:'time_range',start_offset_ns:start,end_offset_ns:end,attributes:{_incremental_source:{round_kind:'ai'}}});
  const unrelated={annotation_id:'manual',scope:'time_point',start_offset_ns:50,end_offset_ns:50,attributes:{}};
  assert.deepEqual(contiguousAiSegments([ai('b',40,100),unrelated,ai('a',0,40)],100).map(a=>a.annotation_id),['a','b']);
  assert.deepEqual(contiguousAiSegments([ai('a',0,39),ai('b',40,100)],100),[]);
  assert.deepEqual(contiguousAiSegments([ai('a',1,100)],100),[]);
});
test('primary AI phase row can be selected without swallowing overlapping AI quality layers',async()=>{
  const {contiguousAiSegments,isPrimaryAiSegment}=await import('../renderer/interval-track.mjs');
  const ai=(id,start,end,group)=>({annotation_id:id,episode_id:'ep',scope:'time_range',start_offset_ns:start,end_offset_ns:end,group,attributes:{ai_provenance:{run_id:'r'}}});
  const values=[ai('phase-a',0,40,'phase'),ai('quality',10,30,'camera_quality'),ai('phase-b',40,100,'phase')];
  assert.deepEqual(contiguousAiSegments(values.filter(item=>isPrimaryAiSegment(item,{group:item.group})),100).map(item=>item.annotation_id),['phase-a','phase-b']);
  assert.equal(isPrimaryAiSegment(values[1],{group:'camera_quality'}),false);
  assert.equal(isPrimaryAiSegment({...values[0],attributes:{}},{group:'phase'}),false);
});
test('shared AI boundary updates both neighboring ranges without a gap',async()=>{
  const {sharedBoundaryBounds}=await import('../renderer/interval-track.mjs');
  const left={episode_id:'ep',scope:'time_range',start_offset_ns:0,end_offset_ns:40};
  const right={episode_id:'ep',scope:'time_range',start_offset_ns:40,end_offset_ns:100};
  assert.deepEqual(sharedBoundaryBounds(left,right,55,100),{
    left:{start_offset_ns:0,end_offset_ns:55},right:{start_offset_ns:55,end_offset_ns:100},
  });
  assert.throws(()=>sharedBoundaryBounds(left,right,0,100));
  assert.throws(()=>sharedBoundaryBounds(left,{...right,start_offset_ns:41},55,100),/不连续/);
});
test('AI boundary frame nudging stays inside both neighboring segments',async()=>{
  const {adjacentFrameBoundary,nearestValidBoundary}=await import('../renderer/interval-track.mjs');
  const left={start_offset_ns:0,end_offset_ns:40},right={start_offset_ns:40,end_offset_ns:100};
  const frames=[0,20,40,60,80,100];
  assert.equal(adjacentFrameBoundary(frames,40,-1,left,right),20);
  assert.equal(adjacentFrameBoundary(frames,40,1,left,right),60);
  assert.equal(adjacentFrameBoundary(frames,20,-1,left,right),null);
  assert.equal(adjacentFrameBoundary(frames,80,1,left,right),null);
  assert.equal(adjacentFrameBoundary([],40,1,left,right),null);
  assert.equal(nearestValidBoundary(frames,73,left,right),80);
  assert.equal(nearestValidBoundary(frames,96,left,right),80);
  assert.equal(nearestValidBoundary([0,100],50,left,right),null);
});
test('renderer exposes one labeled AI segment row with shared boundary handles',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const renderer=fs.readFileSync(path.join(__dirname,'../renderer/renderer.js'),'utf8');
  const styles=fs.readFileSync(path.join(__dirname,'../renderer/styles.css'),'utf8');
  assert.ok(renderer.includes('function renderAiSegmentTrack('));
  assert.ok(renderer.includes('data-ai-segment-track'));
  assert.ok(renderer.includes('data-boundary-left-id'));
  assert.ok(renderer.includes('moveAiBoundary'));
  assert.ok(renderer.includes('annotation-layer-divider'));
  assert.ok(renderer.includes('附加标注'));
  assert.ok(renderer.includes('动作分段'));
  assert.ok(styles.includes('.ai-segment-boundary'));
  assert.ok(styles.includes('.ai-segment-boundary:active:not(:disabled) { transform:translateX(-50%); }'));
  assert.ok(styles.includes('.ai-segment-name'));
  assert.ok(styles.includes('.ai-boundary-inspector'));
  assert.ok(styles.includes('.boundary-adjacent'));
});
test('boundary inspector supports frame nudging and exact time entry',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const text=fs.readFileSync(path.join(__dirname,'../renderer/interval-track.mjs'),'utf8');
  assert.ok(text.includes('data-boundary-nudge="-1"'));
  assert.ok(text.includes('data-boundary-nudge="1"'));
  assert.ok(text.includes('data-boundary-seconds'));
  assert.ok(text.includes("tick.textContent=position?`${position.exact?'':'≈'}F${position.number}`"));
  assert.ok(text.includes("[data-boundary-frame]').textContent=frameIndex>=0?`F${frameIndex+1}`"));
  assert.ok(text.includes("e.key==='ArrowLeft'||e.key==='ArrowRight'"));
  const move=text.slice(text.indexOf("container.addEventListener('pointermove'"),text.indexOf('function cancel()'));
  assert.ok(move.includes('value=Math.round(raw)'));
  assert.ok(move.includes('seek(value);render();return'));
});
test('clicking inside an AI segment seeks to the pointer instead of its start',()=>{
  const fs=require('node:fs'),path=require('node:path');
  const text=fs.readFileSync(path.join(__dirname,'../renderer/interval-track.mjs'),'utf8');
  const click=text.slice(text.indexOf("container.addEventListener('click'"));
  assert.ok(click.includes("block.closest('[data-ai-segment-track]')"));
  assert.ok(click.includes('seek(time(e,surface))'));
});
