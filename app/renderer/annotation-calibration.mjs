// Only real timestamps may be advertised as frame-accurate.
export function nearestFrame(offsets,time){
  if(!Array.isArray(offsets)||!offsets.length)return time;
  let lo=0,hi=offsets.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(offsets[mid]<time)lo=mid+1;else hi=mid;}
  if(lo===0)return offsets[0];if(lo===offsets.length)return offsets.at(-1);
  return time-offsets[lo-1]<=offsets[lo]-time?offsets[lo-1]:offsets[lo];
}
export function adjacentFrame(offsets, time, direction) {
  if (!Array.isArray(offsets) || !offsets.length) return null;
  if (direction > 0) return offsets.find(t => t > time) ?? offsets.at(-1);
  for (let i=offsets.length-1;i>=0;i--) if(offsets[i]<time) return offsets[i];
  return offsets[0];
}

export function calibrationBounds(scope, start, end, limit) {
  if(scope === 'time_point') end=start;
  if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end>limit||end<start||(scope==='time_range'&&end===start)) throw new Error('标注边界无效或超出可标注时长');
  return {start_offset_ns:Math.round(start),end_offset_ns:Math.round(end)};
}

export function installCalibration({container, getState, seek, pause, preview, save, edit, notify}) {
  const panel=document.createElement('section');panel.className='annotation-calibration';panel.hidden=true;container.before(panel);
  panel.innerHTML=`<strong>标注边界校准</strong><p class="calibration-info"></p>
    <div class="calibration-actions">
    <button data-do="start">跳到起点</button><button data-do="end">跳到终点</button>
    <button data-do="prev">上一帧</button><button data-do="next">下一帧</button>
    <button data-do="setStart">设为起点</button><button data-do="setEnd">设为终点</button>
    <button data-do="previewStart">预览起点 ±1秒</button><button data-do="previewEnd">预览终点 ±1秒</button>
    <button data-do="preview">播放区间</button><button data-do="stop">暂停</button>
    <button data-do="save">保存边界</button><button data-do="edit">编辑详情</button><button data-do="cancel">取消校准</button></div>
    <label>局部时间轴（拖动只定位，不修改标注）<select aria-label="时间轴放大范围"><option value="1">前后 1 秒</option><option value="0.25">前后 0.25 秒</option><option value="3">前后 3 秒</option></select><input class="calibration-range" aria-label="局部时间定位" type="range" min="0" max="1000000" step="1"></label><small class="calibration-clock"></small>`;
  let selected=null, draft=null, original=null, saving=false;
  const range=panel.querySelector('input'),zoom=panel.querySelector('select');
  const bounds=()=>{const {duration}=getState(),pad=Number(zoom.value)*1e9;return [Math.max(0,draft.start_offset_ns-pad),Math.min(duration,draft.end_offset_ns+pad)];};
  function render(){
    const s=getState(),a=s.annotations.find(a=>a.annotation_id===selected);
    if(!a||original?.episode_id!==s.episodeId){panel.hidden=true;return;}
    panel.hidden=false;const offsets=s.grid?.exact?s.grid.frameOffsetsNs:null;
    const frame=t=>offsets?`F${offsets.indexOf(nearestFrame(offsets,t))+1}`:'帧号待真实时间戳';
    panel.querySelector('.calibration-info').textContent=`${a.label_name||a.label_code} · ${ (draft.start_offset_ns/1e9).toFixed(6)}s (${frame(draft.start_offset_ns)}) → ${(draft.end_offset_ns/1e9).toFixed(6)}s (${frame(draft.end_offset_ns)}) · 持续 ${((draft.end_offset_ns-draft.start_offset_ns)/1e9).toFixed(6)}s · ${s.grid?.displayName||'请选择参考相机'}`;
    const [lo,hi]=bounds();range.value=String(Math.max(0,Math.min(1000000,(s.time-lo)/Math.max(1,hi-lo)*1000000)));
    panel.querySelector('.calibration-clock').textContent=`${(lo/1e9).toFixed(3)}–${(hi/1e9).toFixed(3)}s · 当前 ${(s.time/1e9).toFixed(6)}s · ${frame(s.time)} · 保存前仅为草稿`;
    for(const b of panel.querySelectorAll('button')) b.disabled=saving || (['prev','next'].includes(b.dataset.do)&&!offsets) || (['setStart','setEnd','save'].includes(b.dataset.do)&&a.scope==='episode') || (['setStart','setEnd'].includes(b.dataset.do)&&s.visualReady===false);
    if(s.visualReady===false)panel.querySelector('.calibration-clock').textContent+=' · 正在读取当前画面，暂不能设置边界';
    container.querySelectorAll('[data-annotation-id]').forEach(el=>el.classList.toggle('calibration-selected',el.dataset.annotationId===selected));
  }
  range.oninput=()=>{pause();const [lo,hi]=bounds();seek(lo+Number(range.value)/1e6*(hi-lo));render();};zoom.onchange=render;
  panel.onclick=async e=>{
    const action=e.target.closest('[data-do]')?.dataset.do;if(!action||saving)return;
    const s=getState();if(s.episodeId!==original?.episode_id)return;
    try{
      if(action==='cancel'){pause();selected=null;panel.hidden=true;container.querySelectorAll('.calibration-selected').forEach(el=>el.classList.remove('calibration-selected'));return;}
      if(action==='stop')pause();
      if(action==='edit'){pause();edit(selected);return;}
      if(action==='start'||action==='end'){pause();seek(draft[action==='start'?'start_offset_ns':'end_offset_ns']);}
      if(action==='prev'||action==='next'){pause();const t=adjacentFrame(s.grid?.exact?s.grid.frameOffsetsNs:null,s.time,action==='next'?1:-1);if(t!==null)seek(t);}
      if(action==='setStart'||action==='setEnd'){
        if(s.visualReady===false)throw new Error('请等待当前画面加载完成再设置边界');
        pause();const frameTime=Math.round(nearestFrame(s.grid?.exact?s.grid.frameOffsetsNs:null,s.time));
        const candidate={...draft,[action==='setStart'?'start_offset_ns':'end_offset_ns']:frameTime};
        if(original.scope==='time_point')candidate.start_offset_ns=candidate.end_offset_ns=frameTime;
        draft=calibrationBounds(original.scope,candidate.start_offset_ns,candidate.end_offset_ns,s.limit);
      }
      if(action.startsWith('preview')){
        const t=action==='previewStart'?draft.start_offset_ns:draft.end_offset_ns;
        preview(action==='preview'?draft.start_offset_ns:Math.max(0,t-1e9),action==='preview'?draft.end_offset_ns:Math.min(s.duration,t+1e9));
      }
      if(action==='save'){
        pause();saving=true;render();const eid=s.episodeId,id=selected;
        const updated=await save(original,calibrationBounds(original.scope,draft.start_offset_ns,draft.end_offset_ns,s.limit));
        if(getState().episodeId===eid&&selected===id)original=structuredClone(updated);
      }
    }catch(err){notify(err.message);}finally{saving=false;render();}
  };
  return {render,select(id){const s=getState(),a=s.annotations.find(a=>a.annotation_id===id);if(!a)return;pause();selected=id;original=structuredClone(a);original.episode_id=s.episodeId;draft={start_offset_ns:a.start_offset_ns,end_offset_ns:a.end_offset_ns};seek(a.start_offset_ns);render();}};
}
