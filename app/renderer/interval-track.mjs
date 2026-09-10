import {nearestFrame, calibrationBounds} from './annotation-calibration.mjs';

export function zoomWindow(start,end,anchor,factor,duration){
  const width=Math.min(duration,Math.max(Math.min(duration,1e8),(end-start)*factor));
  const ratio=(anchor-start)/Math.max(1,end-start);
  const left=Math.max(0,Math.min(duration-width,anchor-ratio*width));
  return [left,left+width];
}

export function draggedBounds(annotation,edge,value,limit){
  if(annotation.scope==='time_point')return calibrationBounds('time_point',value,value,limit);
  if(annotation.scope!=='time_range')throw new Error('整条标签不能拖动修改时间');
  return calibrationBounds('time_range',edge==='start'?value:annotation.start_offset_ns,edge==='end'?value:annotation.end_offset_ns,limit);
}

export function installIntervalTrack({container,getState,seek,pause,save,notify}){
  const toolbar=document.createElement('div');toolbar.className='interval-track-tools';
  toolbar.innerHTML='<button type="button" data-view="in">放大 +</button><button type="button" data-view="out">缩小 −</button><button type="button" data-view="left">← 平移</button><button type="button" data-view="right">平移 →</button><button type="button" data-view="all">全段</button><span role="status"></span>';
  container.before(toolbar);
  let episode=null,duration=0,view=[0,1],selected=null,drag=null,saving=false,suppressClick=false;
  const status=toolbar.querySelector('span');
  function time(event,surface){const rect=surface.getBoundingClientRect();return view[0]+Math.max(0,Math.min(1,(event.clientX-rect.left)/Math.max(1,rect.width)))*(view[1]-view[0]);}
  function reset(){episode=getState().episodeId;duration=getState().duration;view=[0,Math.max(1,duration)];selected=null;drag=null;}
  function render(){
    const s=getState();if(episode!==s.episodeId||duration!==s.duration)reset();
    toolbar.hidden=!s.episodeId||!s.duration;
    // Reuse the original scrubber and its ticks with the same visible window.
    const scrubber=container.parentElement.querySelector('#timeline-range');
    if(scrubber)scrubber.value=String(Math.max(0,Math.min(1,(s.time-view[0])/(view[1]-view[0])))*1e6);
    const ticks=container.parentElement.querySelectorAll('.timeline-ticks span');
    ticks.forEach((tick,i)=>{tick.textContent=`${((view[0]+(view[1]-view[0])*i/Math.max(1,ticks.length-1))/1e9).toFixed(3)}s`;});
    const annotations=new Map(s.annotations.map(a=>[a.annotation_id,a]));
    for(const block of container.querySelectorAll('[data-annotation-id]')){
      const a=annotations.get(block.dataset.annotationId);if(!a)continue;
      const value=drag?.id===a.annotation_id?drag.draft:a;
      const start=value.start_offset_ns,end=value.end_offset_ns,width=view[1]-view[0];
      block.hidden=end<view[0]||start>view[1];
      block.style.setProperty('--annotation-left',`${100*(Math.max(start,view[0])-view[0])/width}%`);
      block.style.setProperty('--annotation-width',`${100*Math.max(0,Math.min(end,view[1])-Math.max(start,view[0]))/width}%`);
      block.classList.toggle('interval-selected',a.annotation_id===selected);
      block.classList.toggle('draggable-time-point',a.scope==='time_point');
      const editable=a.annotation_id===selected&&a.scope==='time_range';
      for(const edge of ['start','end']){
        let handle=block.querySelector(`[data-edge="${edge}"]`);
        if(!handle&&editable){handle=document.createElement('span');handle.dataset.edge=edge;handle.className='interval-handle';handle.setAttribute('aria-label',edge==='start'?'拖动起点':'拖动终点');block.append(handle);}
        if(handle)handle.hidden=!editable||saving||(edge==='start'?start<view[0]:end>view[1]);
      }
    }
    for(const surface of container.querySelectorAll('.annotation-lane-surface')){
      let head=surface.querySelector('.interval-playhead');if(!head){head=document.createElement('i');head.className='interval-playhead';surface.append(head);}
      head.hidden=s.time<view[0]||s.time>view[1];head.style.left=`${100*(s.time-view[0])/(view[1]-view[0])}%`;
    }
    const a=drag?.draft||annotations.get(selected);
    const offsets=s.grid?.exact?s.grid.frameOffsetsNs:null;
    const frame=t=>offsets?` F${offsets.indexOf(nearestFrame(offsets,t))+1}`:'';
    const detail=a?.scope==='time_point'?` · 时间点 ${(a.start_offset_ns/1e9).toFixed(6)}s${frame(a.start_offset_ns)} · 拖动标记修改，松开保存，Esc取消`:a?` · ${(a.start_offset_ns/1e9).toFixed(6)}s${frame(a.start_offset_ns)} → ${(a.end_offset_ns/1e9).toFixed(6)}s${frame(a.end_offset_ns)} · 持续 ${((a.end_offset_ns-a.start_offset_ns)/1e9).toFixed(6)}s · 拖动两端修改，松开保存，Esc取消`:' · 点击标注选中';
    status.textContent=saving?'正在保存标注时间…':`${(view[0]/1e9).toFixed(3)}–${(view[1]/1e9).toFixed(3)}s · 滚轮缩放，Shift+滚轮平移`+detail+` · ${s.grid?.displayName||'时间定位'} · I/O 设置区间`;
  }
  function changeView(action,anchor=getState().time){
    if(drag||!getState().duration)return;const s=getState(),width=view[1]-view[0];
    if(action==='all')view=[0,Math.max(1,s.duration)];
    else if(action==='left'||action==='right'){const left=Math.max(0,Math.min(s.duration-width,view[0]+(action==='left'?-1:1)*width*.25));view=[left,left+width];}
    else view=zoomWindow(...view,Math.max(view[0],Math.min(view[1],anchor)),action==='in'?.5:2,s.duration);
    render();
  }
  toolbar.onclick=e=>{const action=e.target.closest('[data-view]')?.dataset.view;if(action)changeView(action);};
  container.addEventListener('wheel',e=>{if(!getState().episodeId)return;e.preventDefault();const surface=e.target.closest('.annotation-lane-surface')||container.querySelector('.annotation-lane-surface');if(!surface)return;changeView(e.shiftKey?(e.deltaY<0?'left':'right'):(e.deltaY<0?'in':'out'),time(e,surface));},{passive:false});
  container.addEventListener('pointerdown',e=>{
    if(e.button!==0||saving)return;
    const block=e.target.closest('[data-annotation-id]');if(!block)return;
    const id=block.dataset.annotationId,a=getState().annotations.find(a=>a.annotation_id===id);
    const handle=e.target.closest('[data-edge]');
    if(!a||!(a.scope==='time_point'||(a.scope==='time_range'&&handle)))return;
    e.preventDefault();e.stopImmediatePropagation();pause();selected=id;
    const rect=block.closest('.annotation-lane-surface').getBoundingClientRect();
    drag={id,original:structuredClone(a),draft:{...a},edge:a.scope==='time_point'?'point':handle.dataset.edge,anchorX:e.clientX,episode:getState().episodeId,pointer:e.pointerId,surface:{getBoundingClientRect:()=>rect},changed:false};
    container.setPointerCapture(e.pointerId);
    render();
  },true);
  container.addEventListener('pointermove',e=>{
    if(!drag||e.pointerId!==drag.pointer)return;
    if(getState().episodeId!==drag.episode){cancel();return;}
    if(drag.edge==='point'&&!drag.changed&&Math.abs(e.clientX-drag.anchorX)<4)return;
    const s=getState(),raw=time(e,drag.surface);
    const value=Math.round(nearestFrame(s.grid?.exact?s.grid.frameOffsetsNs:null,raw));
    let bounds;try{bounds=draggedBounds(drag.draft,drag.edge,value,s.limit);}catch{return;}
    drag.draft={...drag.draft,...bounds};drag.changed=bounds.start_offset_ns!==drag.original.start_offset_ns||bounds.end_offset_ns!==drag.original.end_offset_ns;seek(value);render();
  });
  function cancel(){if(drag){const pointer=drag.pointer;drag=null;if(container.hasPointerCapture(pointer))container.releasePointerCapture(pointer);}render();}
  container.addEventListener('pointercancel',cancel);
  container.addEventListener('lostpointercapture',()=>{if(drag)cancel();});
  window.addEventListener('keydown',e=>{if(e.key==='Escape'&&drag){e.preventDefault();cancel();}});
  container.addEventListener('pointerup',async e=>{
    if(!drag||e.pointerId!==drag.pointer)return;
    const done=drag;drag=null;suppressClick=true;setTimeout(()=>suppressClick=false,0);
    if(container.hasPointerCapture(e.pointerId))container.releasePointerCapture(e.pointerId);
    if(!done.changed||getState().episodeId!==done.episode){if(!done.changed&&done.edge==='point'&&getState().episodeId===done.episode)seek(done.original.start_offset_ns);render();return;}
    saving=true;render();
    try{await save(done.original,calibrationBounds(done.original.scope,done.draft.start_offset_ns,done.draft.end_offset_ns,getState().limit));}
    catch(error){notify(error.message||'保存失败，已恢复原区间');}finally{saving=false;render();}
  });
  container.addEventListener('click',e=>{
    if(suppressClick||saving)return;const block=e.target.closest('[data-annotation-id]');
    if(block){selected=block.dataset.annotationId;const a=getState().annotations.find(a=>a.annotation_id===selected);if(a){pause();seek(a.start_offset_ns);}render();}
    else{const surface=e.target.closest('.annotation-lane-surface');if(surface){pause();seek(time(e,surface));}}
  });
  return {render,time,atRatio(ratio){return view[0]+Math.max(0,Math.min(1,ratio))*(view[1]-view[0]);},select(id){selected=id;render();}};
}
