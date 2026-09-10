const {test}=require('node:test');
const assert=require('node:assert/strict');
test('frame stepping uses irregular real timestamps and clamps at ends',async()=>{
  const {adjacentFrame,nearestFrame}=await import('../renderer/annotation-calibration.mjs');
  const frames=[0,21,59,61,107];
  assert.equal(adjacentFrame(frames,21,1),59);
  assert.equal(adjacentFrame(frames,58,-1),21);
  assert.equal(adjacentFrame(frames,107,1),107);
  assert.equal(adjacentFrame(frames,0,-1),0);
  assert.equal(adjacentFrame(null,21,1),null);
  assert.equal(nearestFrame(frames,58),59);
  assert.equal(nearestFrame(frames,40),21);
});
test('boundary edits reject reversed ranges and retain point semantics',async()=>{
  const {calibrationBounds}=await import('../renderer/annotation-calibration.mjs');
  assert.deepEqual(calibrationBounds('time_point',25,50,100),{start_offset_ns:25,end_offset_ns:25});
  assert.throws(()=>calibrationBounds('time_range',60,50,100));
  assert.throws(()=>calibrationBounds('time_range',0,101,100));
  assert.throws(()=>calibrationBounds('time_range',20,20,100));
});
